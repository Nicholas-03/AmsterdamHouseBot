import asyncio
import json
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from housebot.auto_reply_queue import AUTO_REPLY_QUEUE
from housebot import config
from housebot import db
from housebot.funda_replier import FundaReplySettings
from housebot.kamernet_replier import KamernetReplySettings
from housebot.notification_sources import ALL_SOURCES, SOURCE_LABELS, format_sources, normalize_sources, parse_source_tokens
from housebot.pararius_alert_mailbox import (
    find_new_pararius_plus_alert_emails,
    mark_pararius_plus_alert_email_seen,
)
from housebot.pararius_replier import ParariusReplier, ParariusReplyResult, ParariusReplySettings
from housebot.roofz_complete_application import RoofzCompleteApplicationCompleter, RoofzCompleteApplicationSettings
from housebot.roofz_mailbox_monitor import find_new_complete_application_emails, mark_complete_application_email_seen
from housebot.roofz_replier import RoofzReplier, RoofzReplySettings
from housebot import scanner as scanner_module
from housebot.scanner import run_scan_for_user
from housebot.scrapers.base import Listing
from housebot.scrapers.kamernet import (
    KAMERNET_PROPERTY_TYPE_LABELS,
    format_kamernet_property_types,
    serialize_kamernet_property_types,
)
from housebot.scrapers.pararius import ParariusScraper
from housebot.source_health import SOURCE_HEALTH

logger = logging.getLogger(__name__)
_SCAN_LOCK = asyncio.Lock()

ASK_PROPERTY_TYPE, ASK_PRICE, ASK_BEDROOMS, ASK_SIZE = range(4)
DEFAULT_CITY = "Amsterdam"
DEFAULT_MAX_PRICE = 2000
DEFAULT_MIN_BEDROOMS = 1
DEFAULT_MIN_SIZE_M2 = 0
DEFAULT_KAMERNET_PROPERTY_TYPE = "any"
KAMERNET_PROPERTY_TYPE_DONE = "Done"
KAMERNET_PROPERTY_TYPE_CLEAR = "Clear selection"
KAMERNET_PROPERTY_TYPE_CHOICES = {
    label.casefold(): key
    for key, label in KAMERNET_PROPERTY_TYPE_LABELS.items()
}
KAMERNET_PROPERTY_TYPE_CHOICES.update(
    {
        key.replace("_", " ").casefold(): key
        for key in KAMERNET_PROPERTY_TYPE_LABELS
    }
)
KAMERNET_PROPERTY_TYPE_OPTIONS_TEXT = ", ".join(KAMERNET_PROPERTY_TYPE_LABELS.values())


def create_application() -> Application:
    async def _post_init(app: Application) -> None:
        await db.init_db()
        AUTO_REPLY_QUEUE.start()
        logger.info("Database initialized.")
        await db.log_event("bot_started", status="started")

    async def _post_shutdown(app: Application) -> None:
        await AUTO_REPLY_QUEUE.stop()

    app = (
        Application.builder()
        .token(config.TELEGRAM_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("filters", cmd_filters))
    app.add_handler(CommandHandler("sources", cmd_sources))
    app.add_handler(CommandHandler("autoreply", cmd_autoreply))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("test", cmd_test))
    app.add_handler(CommandHandler("clear", cmd_clear))

    search_conversation = ConversationHandler(
        entry_points=[CommandHandler("search", cmd_search)],
        allow_reentry=True,
        states={
            ASK_PROPERTY_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_property_type)],
            ASK_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price)],
            ASK_BEDROOMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_bedrooms)],
            ASK_SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_size)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    app.add_handler(search_conversation)
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_error_handler(log_error)

    app.job_queue.run_repeating(
        scheduled_scan,
        interval=config.POLL_INTERVAL_SECONDS,
        first=20,
        job_kwargs={"coalesce": True, "max_instances": 1},
    )
    if config.FAST_SCAN_ENABLED and config.FAST_SCAN_INTERVAL_SECONDS:
        app.job_queue.run_repeating(
            fast_scheduled_scan,
            interval=config.FAST_SCAN_INTERVAL_SECONDS,
            first=45,
            job_kwargs={"coalesce": True, "max_instances": 1},
        )
    if config.HEALTH_ALERT_ENABLED:
        app.job_queue.run_repeating(
            health_watchdog,
            interval=300,
            first=120,
            job_kwargs={"coalesce": True, "max_instances": 1},
        )
    if config.DAILY_SUMMARY_ENABLED:
        app.job_queue.run_daily(
            daily_summary,
            time=_daily_summary_time(),
            job_kwargs={"coalesce": True, "max_instances": 1},
        )
    if (
        config.ROOFZ_COMPLETE_APPLICATION_MONITOR_ENABLED
        and config.ROOFZ_COMPLETE_APPLICATION_MONITOR_INTERVAL_SECONDS
    ):
        app.job_queue.run_repeating(
            roofz_complete_application_watchdog,
            interval=config.ROOFZ_COMPLETE_APPLICATION_MONITOR_INTERVAL_SECONDS,
            first=90,
            job_kwargs={"coalesce": True, "max_instances": 1},
        )
    if (
        config.ROOFZ_PREAPPLICATION_MONITOR_ENABLED
        and config.ROOFZ_PREAPPLICATION_MONITOR_INTERVAL_SECONDS
    ):
        app.job_queue.run_repeating(
            roofz_preapplication_watchdog,
            interval=config.ROOFZ_PREAPPLICATION_MONITOR_INTERVAL_SECONDS,
            first=60,
            job_kwargs={"coalesce": True, "max_instances": 1},
        )
    if (
        config.PARARIUS_ALERT_MONITOR_ENABLED
        and config.PARARIUS_ALERT_MONITOR_INTERVAL_SECONDS
    ):
        app.job_queue.run_repeating(
            pararius_alert_watchdog,
            interval=config.PARARIUS_ALERT_MONITOR_INTERVAL_SECONDS,
            first=75,
            job_kwargs={"coalesce": True, "max_instances": 1},
        )

    return app


async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_scan_job(
        context,
        started_event="scheduled_scan_started",
        finished_event="scheduled_scan_finished",
        failed_event="scheduled_scan_user_failed",
        run_maintenance=True,
    )


async def fast_scheduled_scan(context: ContextTypes.DEFAULT_TYPE) -> None:
    fast_sources = tuple(source for source in normalize_sources(config.FAST_SCAN_SOURCES) if source in ALL_SOURCES)
    if not fast_sources:
        await db.log_event("fast_scan_skipped", level="warning", status="no_sources")
        return
    await _run_scan_job(
        context,
        started_event="fast_scan_started",
        finished_event="fast_scan_finished",
        failed_event="fast_scan_user_failed",
        source_filter=set(fast_sources),
    )


async def _run_scan_job(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    started_event: str,
    finished_event: str,
    failed_event: str,
    run_maintenance: bool = False,
    source_filter: set[str] | None = None,
) -> None:
    if _SCAN_LOCK.locked():
        await db.log_event(
            started_event.replace("_started", "_skipped"),
            level="info",
            status="scan_already_running",
            data={"source_filter": sorted(source_filter) if source_filter else None},
        )
        return

    async with _SCAN_LOCK:
        if run_maintenance:
            await db.run_maintenance()
        users = await _get_active_allowed_users()
        filtered_users: list[dict] = []
        for user in users:
            if source_filter:
                effective_sources = tuple(
                    source
                    for source in normalize_sources(user.get("enabled_sources"))
                    if source in source_filter
                )
                if not effective_sources:
                    continue
            filtered_users.append(user)

        logger.info("%s: %d active users.", started_event, len(filtered_users))
        await db.log_event(
            started_event,
            status="started",
            data={
                "active_users": len(filtered_users),
                "source_filter": sorted(source_filter) if source_filter else None,
            },
        )
        for user in filtered_users:
            try:
                await run_scan_for_user(context.bot, user, source_filter=source_filter)
            except Exception as exc:
                logger.error("Scan error for user %s: %s", user["chat_id"], exc)
                await db.log_event(
                    failed_event,
                    level="error",
                    chat_id=user["chat_id"],
                    status="error",
                    detail=str(exc),
                )
        await db.log_event(
            finished_event,
            status="finished",
            data={
                "active_users": len(filtered_users),
                "source_filter": sorted(source_filter) if source_filter else None,
            },
        )


async def health_watchdog(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not config.HEALTH_ALERT_ENABLED:
        return
    latest_scan = await db.get_latest_bot_event(
        ("scheduled_scan_finished", "fast_scan_finished", "manual_scan_finished")
    )
    if not latest_scan:
        return
    scan_age_seconds = _event_age_seconds(latest_scan)
    stale_after_seconds = config.HEALTH_ALERT_STALE_SCAN_MINUTES * 60
    if scan_age_seconds is None or scan_age_seconds <= stale_after_seconds:
        return

    latest_alert = await db.get_latest_bot_event("health_alert_sent", status="stale_scan")
    alert_age_seconds = _event_age_seconds(latest_alert) if latest_alert else None
    alert_cooldown_seconds = config.HEALTH_ALERT_COOLDOWN_MINUTES * 60
    if alert_age_seconds is not None and alert_age_seconds < alert_cooldown_seconds:
        return

    users = await _get_active_allowed_users()
    text = (
        "Bot health warning\n\n"
        "Status: no recent completed scan\n"
        f"Last completed scan: {_format_age(scan_age_seconds)} ago\n"
        "I will keep trying, but the server may need attention."
    )
    for user in users:
        await context.bot.send_message(chat_id=user["chat_id"], text=text, disable_web_page_preview=True)
    await db.log_event(
        "health_alert_sent",
        level="warning",
        status="stale_scan",
        detail=text,
        data={"notified_users": len(users), "scan_age_seconds": scan_age_seconds},
    )


async def daily_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    users = await _get_active_allowed_users()
    if not users:
        return
    text = await _build_daily_summary_text()
    for user in users:
        await context.bot.send_message(chat_id=user["chat_id"], text=text, disable_web_page_preview=True)
    await db.log_event(
        "daily_summary_sent",
        status="sent",
        data={"notified_users": len(users)},
    )


async def roofz_complete_application_watchdog(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not config.ROOFZ_COMPLETE_APPLICATION_MONITOR_ENABLED:
        return
    try:
        messages = await find_new_complete_application_emails()
    except Exception as exc:
        logger.warning("Roofz complete-application mailbox check failed: %s", exc)
        await db.log_event(
            "roofz_complete_application_check_failed",
            level="warning",
            source="roofz",
            status="error",
            detail=str(exc),
        )
        return

    if not messages:
        return

    complete_settings = RoofzCompleteApplicationSettings.from_config()
    complete_ready_error = complete_settings.ready_error()
    users = [
        user
        for user in await _get_active_allowed_users()
        if "roofz" in normalize_sources(user.get("enabled_sources"))
    ]
    for message in messages:
        if await db.bot_event_exists(
            "roofz_complete_application_result",
            source="roofz",
            listing_id=message.message_id,
        ):
            try:
                await mark_complete_application_email_seen(message.message_id)
            except Exception as exc:
                logger.warning("Could not mark Roofz complete-application email seen: %s", exc)
            continue

        await db.log_event(
            "roofz_complete_application_detected",
            level="warning",
            source="roofz",
            listing_id=message.message_id,
            title=message.listing_title,
            status="detected",
            detail=message.subject,
            data={
                "sender": message.sender,
                "link_count": len(message.links),
                "links": list(message.links[:3]),
            },
        )
        link_text = message.links[0] if message.links else "No application link detected in the email."
        if not message.links:
            result_status = "complete_application_missing_link"
            result_detail = "No complete-application link was detected in the email."
        elif complete_ready_error:
            result_status = "complete_application_not_ready"
            result_detail = complete_ready_error
        else:
            try:
                result = await RoofzCompleteApplicationCompleter(complete_settings).complete_application(message.links[0])
                result_status = result.status
                result_detail = result.detail
            except Exception as exc:
                logger.exception("Roofz complete-application submit failed for %s", message.listing_title)
                result_status = "complete_application_error"
                result_detail = str(exc)

        result_ok = _roofz_complete_application_status_ok(result_status)
        await db.log_event(
            "roofz_complete_application_result",
            level="info" if result_ok else "warning",
            source="roofz",
            listing_id=message.message_id,
            title=message.listing_title,
            status=result_status,
            detail=result_detail,
            data={
                "sender": message.sender,
                "link": message.links[0] if message.links else None,
            },
        )

        if result_ok:
            text = _format_roofz_complete_application_message(
                message.listing_title,
                result_status,
                result_detail,
                ok=True,
            )
        else:
            text = _format_roofz_complete_application_message(
                message.listing_title,
                result_status,
                result_detail,
                ok=False,
                link=link_text,
            )

        notified_users = 0
        recipients = []
        for user in users:
            sent_message = await context.bot.send_message(
                chat_id=user["chat_id"],
                text=text,
                disable_web_page_preview=True,
            )
            notified_users += 1
            message_id = getattr(sent_message, "message_id", None)
            recipients.append({"chat_id": user["chat_id"], "message_id": message_id})
            await db.log_event(
                "roofz_complete_application_notification_user_sent",
                source="roofz",
                chat_id=user["chat_id"],
                listing_id=message.message_id,
                title=message.listing_title,
                status=result_status,
                data={"message_id": message_id, "result_ok": result_ok},
            )
        await db.log_event(
            "roofz_complete_application_notification_sent",
            source="roofz",
            listing_id=message.message_id,
            title=message.listing_title,
            status=result_status,
            data={"notified_users": notified_users, "result_ok": result_ok, "recipients": recipients},
        )
        if result_ok or notified_users:
            try:
                await mark_complete_application_email_seen(message.message_id)
            except Exception as exc:
                logger.warning("Could not mark Roofz complete-application email seen: %s", exc)


async def roofz_preapplication_watchdog(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not config.ROOFZ_PREAPPLICATION_MONITOR_ENABLED:
        return

    settings = RoofzReplySettings.from_config()
    ready_error = settings.ready_error()
    if ready_error:
        await db.log_event(
            "roofz_preapplication_monitor_skipped",
            level="warning",
            source="roofz",
            status="not_ready",
            detail=ready_error,
        )
        return

    pending = await db.get_auto_replies_by_status(
        "roofz",
        ("sent_preapplication_pending",),
        limit=20,
    )
    if not pending:
        return

    users = [
        user
        for user in await _get_active_allowed_users()
        if "roofz" in normalize_sources(user.get("enabled_sources"))
    ]

    async with RoofzReplier(settings) as replier:
        for row in pending:
            listing = _listing_from_auto_reply_row(row)
            since = (
                _parse_event_timestamp(row.get("sent_at"))
                or _parse_event_timestamp(row.get("attempted_at"))
                or datetime.now(timezone.utc) - timedelta(hours=2)
            )
            initial_sent_at = _parse_event_timestamp(row.get("sent_at"))
            try:
                result = await replier.complete_pending_preapplication(
                    listing,
                    since,
                    initial_sent_at,
                    poll_seconds=0,
                )
            except Exception as exc:
                detail = str(exc)
                logger.warning("Roofz pre-application mailbox check failed for %s: %s", listing.id, detail)
                await db.log_event(
                    "roofz_preapplication_check_failed",
                    level="warning",
                    chat_id=row.get("triggered_by_chat_id"),
                    source=listing.source,
                    listing_id=listing.id,
                    title=listing.title,
                    status="error",
                    detail=detail,
                )
                continue
            if result.status == "sent_preapplication_pending":
                continue

            await db.mark_auto_reply_result(
                listing.source,
                listing.id,
                listing.url,
                int(row.get("triggered_by_chat_id") or 0),
                result.status,
                settings.dry_run,
                result.detail,
                first_seen_at=row.get("first_seen_at"),
                sent_at=result.sent_at or row.get("sent_at"),
                confirmation_at=result.confirmation_at,
            )
            await db.log_event(
                "roofz_preapplication_monitor_result",
                level="info" if _auto_reply_status_ok(result.status) else "warning",
                chat_id=row.get("triggered_by_chat_id"),
                source=listing.source,
                listing_id=listing.id,
                title=listing.title,
                status=result.status,
                detail=result.detail,
            )
            if _auto_reply_status_ok(result.status):
                continue

            text = _format_roofz_preapplication_warning(listing, result)
            for user in users:
                await context.bot.send_message(
                    chat_id=user["chat_id"],
                    text=text,
                    disable_web_page_preview=True,
                )


async def pararius_alert_watchdog(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not config.PARARIUS_ALERT_MONITOR_ENABLED:
        return

    try:
        alerts = await find_new_pararius_plus_alert_emails()
    except Exception as exc:
        logger.warning("Pararius+ alert mailbox check failed: %s", exc)
        await db.log_event(
            "pararius_plus_alert_check_failed",
            level="warning",
            source=ParariusScraper.SOURCE,
            status="error",
            detail=str(exc),
        )
        return

    if not alerts:
        return

    users = [
        user
        for user in await _get_active_allowed_users()
        if ParariusScraper.SOURCE in normalize_sources(user.get("enabled_sources"))
    ]
    reply_settings = ParariusReplySettings.from_config()
    reply_settings_error = reply_settings.ready_error()
    reply_attempts_by_chat: dict[int, int] = {}

    for alert in alerts:
        await db.log_event(
            "pararius_plus_alert_detected",
            source=ParariusScraper.SOURCE,
            listing_id=alert.message_id,
            title=alert.subject,
            status="detected",
            data={
                "sender": alert.sender,
                "listing_count": len(alert.listings),
                "created_at": alert.created_at.isoformat() if alert.created_at else None,
            },
        )
        notified_users = 0
        processed_listings = 0
        for listing in alert.listings:
            for user in users:
                chat_id = int(user["chat_id"])
                if not _listing_matches_user_filters(listing, user):
                    await db.log_event(
                        "pararius_plus_alert_filtered",
                        chat_id=chat_id,
                        source=listing.source,
                        listing_id=listing.id,
                        title=listing.title,
                        status="filtered",
                        data={
                            "price_eur": listing.price_eur,
                            "bedrooms": listing.bedrooms,
                            "size_m2_value": listing.size_m2_value,
                            "max_price": user.get("max_price"),
                            "min_bedrooms": user.get("min_bedrooms"),
                            "min_size_m2": user.get("min_size_m2"),
                        },
                    )
                    continue
                if await db.was_sent(chat_id, listing.source, listing.id):
                    continue

                seen_listing = await db.mark_seen(
                    listing.source,
                    listing.id,
                    listing.url,
                    listing.title,
                    listing.price,
                )
                first_seen_at = seen_listing.get("scraped_at") if seen_listing else None
                reply_timing_start = listing.reply_data.get("available_at") or first_seen_at
                await db.log_event(
                    "listing_new",
                    chat_id=chat_id,
                    source=listing.source,
                    listing_id=listing.id,
                    title=listing.title,
                    status="new",
                    data={
                        "price": listing.price,
                        "url": listing.url,
                        "mailbox_message_id": alert.message_id,
                        "mailbox_source": "pararius_plus_alert",
                        "first_seen_at": first_seen_at,
                        "source_available_at": listing.reply_data.get("available_at"),
                        "first_seen_by_bot": bool(seen_listing.get("inserted")) if seen_listing else False,
                    },
                )
                await scanner_module._send_notification(context.bot, chat_id, listing)
                await db.mark_sent(chat_id, listing.source, listing.id)
                notified_users += 1
                processed_listings += 1

                if user.get("auto_reply_enabled"):
                    attempts = reply_attempts_by_chat.get(chat_id, 0)
                    attempted = await scanner_module._enqueue_auto_reply_to_listing(
                        chat_id,
                        listing,
                        reply_settings,
                        reply_settings_error,
                        attempts,
                        ParariusReplier,
                        ParariusReplyResult,
                        "Pararius",
                        context.bot,
                        reply_timing_start,
                    )
                    if attempted:
                        reply_attempts_by_chat[chat_id] = attempts + 1
                else:
                    await db.log_event(
                        "auto_reply_user_disabled",
                        chat_id=chat_id,
                        source=listing.source,
                        listing_id=listing.id,
                        title=listing.title,
                        status="skipped",
                    )

        if not alert.listings and users:
            text = (
                "Pararius+ alert needs attention\n\n"
                f"Subject: {alert.subject or '(no subject)'}\n"
                "Problem: no Pararius listing link was detected in the forwarded email."
            )
            for user in users:
                await context.bot.send_message(
                    chat_id=user["chat_id"],
                    text=text,
                    disable_web_page_preview=True,
                )
                notified_users += 1

        await db.log_event(
            "pararius_plus_alert_processed",
            source=ParariusScraper.SOURCE,
            listing_id=alert.message_id,
            title=alert.subject,
            status="processed",
            data={
                "listing_count": len(alert.listings),
                "processed_listings": processed_listings,
                "notified_users": notified_users,
                "active_pararius_users": len(users),
            },
        )
        try:
            await mark_pararius_plus_alert_email_seen(alert.message_id)
        except Exception as exc:
            logger.warning("Could not mark Pararius+ alert email seen: %s", exc)
            await db.log_event(
                "pararius_plus_alert_mark_seen_failed",
                level="warning",
                source=ParariusScraper.SOURCE,
                listing_id=alert.message_id,
                title=alert.subject,
                status="error",
                detail=str(exc),
            )


def _listing_from_auto_reply_row(row: dict) -> Listing:
    title = row.get("seen_title") or row.get("listing_id") or "Roofz listing"
    url = row.get("listing_url") or row.get("url") or ""
    return Listing(
        id=row["listing_id"],
        source="roofz",
        title=title,
        price=row.get("seen_price") or "Price unavailable",
        address="Amsterdam",
        url=url,
    )


def _listing_matches_user_filters(listing: Listing, user_filters: dict) -> bool:
    max_price = int(user_filters.get("max_price") or 0)
    min_bedrooms = int(user_filters.get("min_bedrooms") or 0)
    min_size_m2 = int(user_filters.get("min_size_m2") or 0)
    if max_price and listing.price_eur and listing.price_eur > max_price:
        return False
    if min_bedrooms and listing.bedrooms is not None and listing.bedrooms < min_bedrooms:
        return False
    if min_size_m2 and listing.size_m2_value and listing.size_m2_value < min_size_m2:
        return False
    return True


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_authorized(update):
        return

    logger.info("/start from chat %s", update.effective_chat.id)
    await db.log_event("telegram_command", chat_id=update.effective_chat.id, status="/start")
    await _send_help(update)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_authorized(update):
        return

    logger.info("/help from chat %s", update.effective_chat.id)
    await db.log_event("telegram_command", chat_id=update.effective_chat.id, status="/help")
    await _send_help(update)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_authorized(update):
        return

    chat_id = update.effective_chat.id
    await db.log_event("telegram_command", chat_id=chat_id, status="/status")
    user_filters = await db.get_filters(chat_id)
    await update.message.reply_text(await _build_status_text(user_filters))


async def cmd_filters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_authorized(update):
        return

    user_filters = await db.get_filters(update.effective_chat.id)
    await db.log_event("telegram_command", chat_id=update.effective_chat.id, status="/filters")
    if not user_filters:
        await update.message.reply_text("No filters configured. Use /search.")
        return

    await update.message.reply_text(_format_filters(user_filters))


async def cmd_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_authorized(update):
        return

    chat_id = update.effective_chat.id
    await db.log_event("telegram_command", chat_id=chat_id, status="/sources")
    user_filters = await db.get_filters(chat_id)
    if not user_filters:
        await update.message.reply_text("Set your filters first with /search.")
        return

    args = context.args or []
    action = args[0].casefold() if args else "status"
    current_sources = normalize_sources(user_filters.get("enabled_sources"))

    if action in {"status", "show"}:
        await update.message.reply_text(_format_sources_status(user_filters))
        return

    if action in {"all", "reset"}:
        next_sources = ALL_SOURCES
    elif action in {"only", "set"}:
        if _source_args_include_all(args[1:]):
            next_sources = ALL_SOURCES
        else:
            next_sources = await _parse_sources_or_reply(update, args[1:])
            if next_sources is None:
                return
    elif action in {"on", "enable", "add"}:
        if _source_args_include_all(args[1:]):
            next_sources = ALL_SOURCES
        else:
            sources_to_add = await _parse_sources_or_reply(update, args[1:])
            if sources_to_add is None:
                return
            next_sources = tuple(
                source
                for source in ALL_SOURCES
                if source in current_sources or source in sources_to_add
            )
    elif action in {"off", "disable", "remove"}:
        if _source_args_include_all(args[1:]):
            await update.message.reply_text(
                "Use /pause to pause all notifications, or turn off specific sites with /sources off <site>."
            )
            return
        sources_to_remove = await _parse_sources_or_reply(update, args[1:])
        if sources_to_remove is None:
            return
        next_sources = tuple(
            source
            for source in current_sources
            if source not in sources_to_remove
        )
        if not next_sources:
            await update.message.reply_text(
                "At least one notification site must stay enabled. Use /pause to pause all notifications."
            )
            return
    else:
        await update.message.reply_text(_sources_usage())
        return

    await db.set_enabled_sources(chat_id, next_sources)
    await db.log_event(
        "notification_sources_changed",
        chat_id=chat_id,
        status="saved",
        data={"enabled_sources": list(next_sources)},
    )
    updated_filters = await db.get_filters(chat_id)
    await update.message.reply_text(
        "Notification sites updated.\n\n" + _format_sources_status(updated_filters)
    )


async def cmd_autoreply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_authorized(update):
        return

    chat_id = update.effective_chat.id
    await db.log_event("telegram_command", chat_id=chat_id, status="/autoreply")
    user_filters = await db.get_filters(chat_id)
    if not user_filters:
        await update.message.reply_text("Set your filters first with /search.")
        return

    arg = context.args[0].casefold() if context.args else "status"
    if arg in {"status", "show"}:
        await update.message.reply_text(_format_auto_reply_status(user_filters))
        return

    if arg in {"off", "disable", "disabled", "stop"}:
        await db.set_auto_reply(chat_id, False)
        await db.log_event("auto_reply_toggle_changed", chat_id=chat_id, status="off")
        await update.message.reply_text(
            "Auto-reply is off. I will still send matching listings in Telegram."
        )
        return

    if arg in {"on", "enable", "enabled", "start"}:
        status = _auto_reply_source_status()
        if not any(item[1] is None for item in status):
            await update.message.reply_text(
                "Auto-reply cannot be enabled yet.\n"
                "Server setup issues:\n"
                + "\n".join(f"{label}: {error}" for label, error in status)
            )
            return

        await db.set_auto_reply(chat_id, True)
        await db.log_event("auto_reply_toggle_changed", chat_id=chat_id, status="on")
        await update.message.reply_text(
            "Auto-reply is on for sources that are ready on the server.\n"
            + "\n".join(_format_source_status(label, error) for label, error in status)
            + "\n"
            "Use /autoreply off to disable it."
        )
        return

    await update.message.reply_text("Use /autoreply on, /autoreply off, or /autoreply status.")


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_authorized(update):
        return

    await db.log_event("telegram_command", chat_id=update.effective_chat.id, status="/logs")
    events = await db.get_recent_bot_events(limit=15)
    if not events:
        await update.message.reply_text("No bot events in the last 3 days.")
        return

    lines = ["Recent bot events:"]
    for event in reversed(events[:10]):
        lines.append(_format_event_line(event))
    await update.message.reply_text("\n".join(lines))


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await _ensure_authorized(update):
        return ConversationHandler.END

    chat_id = update.effective_chat.id
    previous_filters = await db.get_filters(chat_id)
    logger.info("/search from chat %s", chat_id)
    await db.log_event("telegram_command", chat_id=chat_id, status="/search")
    context.user_data.clear()
    context.user_data["had_filters"] = previous_filters is not None
    context.user_data["previous_active"] = (
        previous_filters["active"] if previous_filters else True
    )
    if previous_filters:
        await db.set_setup_in_progress(chat_id, True)
        await db.log_event("search_setup_started", chat_id=chat_id, status="editing_existing_filters")

    await update.message.reply_text(
        "Kamernet property types?\n"
        "Tap one or more options, then Done.\n"
        "Choose Any property type to skip this filter.",
        reply_markup=ReplyKeyboardMarkup(
            _property_type_keyboard(),
            one_time_keyboard=False,
            resize_keyboard=True,
            input_field_placeholder="Choose property types",
        ),
    )
    return ASK_PROPERTY_TYPE


async def receive_property_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await _ensure_authorized(update):
        return ConversationHandler.END

    choice = (update.message.text or "").strip()
    logger.info("Received property type choice from chat %s: %s", update.effective_chat.id, choice)
    if choice.casefold() == KAMERNET_PROPERTY_TYPE_DONE.casefold():
        selected_types = context.user_data.get("kamernet_property_types", [])
        context.user_data["kamernet_property_type"] = serialize_kamernet_property_types(
            selected_types or DEFAULT_KAMERNET_PROPERTY_TYPE
        )
        return await _ask_price(update)

    if choice.casefold() == KAMERNET_PROPERTY_TYPE_CLEAR.casefold():
        context.user_data["kamernet_property_types"] = []
        await update.message.reply_text(
            "Selection cleared. Choose property types, or tap Done for Any property type.",
            reply_markup=ReplyKeyboardMarkup(
                _property_type_keyboard(),
                one_time_keyboard=False,
                resize_keyboard=True,
                input_field_placeholder="Choose property types",
            ),
        )
        return ASK_PROPERTY_TYPE

    property_types, invalid_choices = _parse_property_type_choices(choice)
    if invalid_choices:
        await update.message.reply_text(
            "Choose or type property types separated by commas:\n"
            f"{KAMERNET_PROPERTY_TYPE_OPTIONS_TEXT}"
        )
        return ASK_PROPERTY_TYPE

    if "any" in property_types:
        context.user_data["kamernet_property_types"] = []
        context.user_data["kamernet_property_type"] = DEFAULT_KAMERNET_PROPERTY_TYPE
        return await _ask_price(update)

    selected_types = list(context.user_data.get("kamernet_property_types", []))
    for property_type in property_types:
        if property_type in selected_types:
            selected_types.remove(property_type)
        else:
            selected_types.append(property_type)
    context.user_data["kamernet_property_types"] = selected_types

    await update.message.reply_text(
        _format_property_type_selection(selected_types),
        reply_markup=ReplyKeyboardMarkup(
            _property_type_keyboard(),
            one_time_keyboard=False,
            resize_keyboard=True,
            input_field_placeholder="Choose property types",
        ),
    )
    return ASK_PROPERTY_TYPE


async def receive_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await _ensure_authorized(update):
        return ConversationHandler.END

    price = _parse_non_negative_int(update.message.text)
    if price is None:
        await update.message.reply_text("Please send a valid number, for example 1500.")
        return ASK_PRICE

    context.user_data["max_price"] = price
    await update.message.reply_text(
        "Minimum bedrooms/rooms?\n"
        "Send 1, 2, 3, etc. Use 0 if you do not want this filter."
    )
    return ASK_BEDROOMS


async def receive_bedrooms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await _ensure_authorized(update):
        return ConversationHandler.END

    bedrooms = _parse_non_negative_int(update.message.text)
    if bedrooms is None:
        await update.message.reply_text("Please send a valid number, for example 2.")
        return ASK_BEDROOMS

    context.user_data["min_bedrooms"] = bedrooms
    await update.message.reply_text(
        "Minimum surface area in square meters?\n"
        "Send a number like 45, or 0 for no minimum size."
    )
    return ASK_SIZE


async def receive_size(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await _ensure_authorized(update):
        return ConversationHandler.END

    min_size_m2 = _parse_non_negative_int(update.message.text)
    if min_size_m2 is None:
        await update.message.reply_text("Please send a valid number, for example 45.")
        return ASK_SIZE

    chat_id = update.effective_chat.id
    await db.save_filters(
        chat_id,
        max_price=context.user_data.get("max_price", DEFAULT_MAX_PRICE),
        min_bedrooms=context.user_data.get("min_bedrooms", DEFAULT_MIN_BEDROOMS),
        min_size_m2=min_size_m2,
        city=DEFAULT_CITY,
        kamernet_property_type=context.user_data.get(
            "kamernet_property_type",
            DEFAULT_KAMERNET_PROPERTY_TYPE,
        ),
        active=context.user_data.get("previous_active", True),
    )
    context.user_data.clear()

    saved_filters = await db.get_filters(chat_id)
    await db.log_event(
        "filters_saved",
        chat_id=chat_id,
        status="saved",
        data={
            "max_price": saved_filters["max_price"],
            "min_bedrooms": saved_filters["min_bedrooms"],
            "min_size_m2": saved_filters["min_size_m2"],
            "kamernet_property_type": saved_filters["kamernet_property_type"],
            "active": saved_filters["active"],
            "enabled_sources": list(saved_filters["enabled_sources"]),
        },
    )
    await update.message.reply_text("Filters saved.\n\n" + _format_filters(saved_filters))
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await _ensure_authorized(update):
        return ConversationHandler.END

    if context.user_data.get("had_filters"):
        await db.set_setup_in_progress(update.effective_chat.id, False)
    context.user_data.clear()
    await db.log_event("search_setup_cancelled", chat_id=update.effective_chat.id, status="cancelled")
    await update.message.reply_text("Search setup cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_authorized(update):
        return

    if not await db.get_filters(update.effective_chat.id):
        await update.message.reply_text("No filters configured. Use /search.")
        return

    await db.set_active(update.effective_chat.id, False)
    await db.log_event("notifications_paused", chat_id=update.effective_chat.id, status="paused")
    await update.message.reply_text("Notifications paused. Use /resume to resume.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_authorized(update):
        return

    if not await db.get_filters(update.effective_chat.id):
        await update.message.reply_text("Set your filters first with /search.")
        return

    await db.set_active(update.effective_chat.id, True)
    await db.log_event("notifications_resumed", chat_id=update.effective_chat.id, status="active")
    await update.message.reply_text("Notifications resumed.")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_authorized(update):
        return

    await db.clear_seen()
    await db.log_event("seen_listings_cleared", chat_id=update.effective_chat.id, level="warning", status="cleared")
    await update.message.reply_text(
        "Seen and sent listings were cleared. The next scan will treat matching listings as new."
    )


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_authorized(update):
        return

    chat_id = update.effective_chat.id
    user_filters = await db.get_filters(chat_id)
    if not user_filters:
        await update.message.reply_text("Set your filters first with /search.")
        return
    if user_filters.get("setup_in_progress"):
        await update.message.reply_text("Finish or cancel /search before running a test scan.")
        return

    await update.message.reply_text("Searching for listings now...")
    await db.log_event("manual_scan_started", chat_id=chat_id, status="started")
    if _SCAN_LOCK.locked():
        await db.log_event("manual_scan_skipped", chat_id=chat_id, level="warning", status="scan_already_running")
        await update.message.reply_text("A scan is already running. Try again in a minute.")
        return
    async with _SCAN_LOCK:
        count = await run_scan_for_user(context.bot, user_filters, require_active=False)
    await db.log_event("manual_scan_finished", chat_id=chat_id, status="finished", data={"new_count": count})
    if count == 0:
        await update.message.reply_text("No new matching listings found at the moment.")
    else:
        await update.message.reply_text(f"Sent {count} new matching listings.")


async def log_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if _is_transient_telegram_polling_error(update, error):
        detail = str(error)
        logger.warning("Transient Telegram polling error: %s", detail)
        await db.log_event(
            "telegram_polling_transient_error",
            level="warning",
            status="transient",
            detail=detail,
        )
        return

    logger.exception("Telegram handler failed for update %s", update, exc_info=error)
    await db.log_event("telegram_handler_failed", level="error", status="error", detail=str(error))


def _is_authorized(update: Update) -> bool:
    if not config.TELEGRAM_ALLOWED_CHAT_IDS:
        return True
    if not update.effective_chat:
        return False
    return update.effective_chat.id in config.TELEGRAM_ALLOWED_CHAT_IDS


def _is_transient_telegram_polling_error(update: object, error: object) -> bool:
    if update is not None:
        return False
    if isinstance(error, (NetworkError, TimedOut)):
        return True
    detail = str(error).casefold()
    return any(
        marker in detail
        for marker in (
            "bad gateway",
            "gateway timeout",
            "timed out",
            "connection reset",
            "connection aborted",
        )
    )


async def _ensure_authorized(update: Update) -> bool:
    if _is_authorized(update):
        return True

    chat_id = update.effective_chat.id if update.effective_chat else "unknown"
    logger.warning("Unauthorized Telegram chat attempted to use the bot: %s", chat_id)
    await db.log_event(
        "telegram_unauthorized",
        level="warning",
        chat_id=chat_id if isinstance(chat_id, int) else None,
        status="blocked",
    )
    if update.message:
        await update.message.reply_text(
            "This is a private bot.\n"
            f"Your chat ID is {chat_id}."
        )
    return False


def _parse_non_negative_int(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        value = int(text.strip())
    except ValueError:
        return None
    return value if value >= 0 else None


async def _ask_price(update: Update) -> int:
    await update.message.reply_text(
        "Maximum monthly rent in EUR?\n"
        "Send a number like 1800, or 0 for no limit.\n\n"
        "Use /cancel to stop.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ASK_PRICE


def _property_type_keyboard() -> list[list[str]]:
    return [
        [KAMERNET_PROPERTY_TYPE_DONE, KAMERNET_PROPERTY_TYPE_CLEAR],
        ["Any property type"],
        ["Room", "Apartment"],
        ["Studio", "Anti-squat"],
        ["Student Housing", "Furnished"],
        ["Short Term", "Long Term"],
    ]


def _parse_property_type_choices(text: str | None) -> tuple[list[str], list[str]]:
    if not text:
        return [], [""]

    label_lookup = {
        " ".join(label.split()).casefold(): key
        for label, key in KAMERNET_PROPERTY_TYPE_CHOICES.items()
    }
    parts = []
    for value in text.replace(";", ",").replace("\n", ",").split(","):
        label = " ".join(value.strip().split())
        if label:
            parts.append(label)

    property_types: list[str] = []
    invalid_choices: list[str] = []
    for label in parts:
        property_type = label_lookup.get(label.casefold())
        if property_type is None:
            invalid_choices.append(label)
        elif property_type not in property_types:
            property_types.append(property_type)

    return property_types, invalid_choices


def _format_property_type_selection(property_types: list[str]) -> str:
    if not property_types:
        return "No property types selected. Tap Done for Any property type."
    selected = format_kamernet_property_types(property_types)
    return (
        f"Selected: {selected}\n"
        "Choose another property type, tap a selected type again to remove it, or tap Done."
    )


async def _send_help(update: Update) -> None:
    await update.message.reply_text(
        "Amsterdam House Bot is running.\n\n"
        "Commands:\n"
        "/help - show this help message\n"
        "/status - show scan health, source status, and auto-reply queue\n"
        "/search - set Kamernet property types, rent, bedrooms, and size filters\n"
        "/filters - show active filters\n"
        "/sources on|off|only|all|status - control which sites send notifications\n"
        "/autoreply on|off|status - control Pararius/Kamernet/Funda/Roofz auto-replies\n"
        "/logs - show recent operational events\n"
        "/test - scan now\n"
        "/pause - pause notifications\n"
        "/resume - resume notifications\n"
        "/clear - clear sent/seen listings\n"
        "/cancel - cancel search setup\n\n"
        "Notifications start after you complete /search.\n"
        f"I scan every {_format_interval(config.POLL_INTERVAL_SECONDS)}."
    )


async def _build_status_text(user_filters: dict | None) -> str:
    latest_scan = await db.get_latest_bot_event(
        ("scheduled_scan_finished", "fast_scan_finished", "manual_scan_finished")
    )
    latest_source_events = await db.get_latest_source_events()
    listing_counts = await db.get_event_counts_since("listing_new", hours=24)
    level_counts = await db.get_level_counts_since(hours=24)
    auto_summary = await db.get_auto_reply_summary_since(hours=24)
    queue = AUTO_REPLY_QUEUE.snapshot()
    source_health = SOURCE_HEALTH.snapshot()

    lines = ["Bot status:"]
    if user_filters:
        lines.append(
            "Notifications: "
            + ("Active" if user_filters.get("active") and not user_filters.get("setup_in_progress") else "Paused/setup")
        )
        lines.append(f"Sites: {format_sources(user_filters.get('enabled_sources'))}")
        lines.append(f"Auto-reply: {'On' if user_filters.get('auto_reply_enabled') else 'Off'}")
    else:
        lines.append("Filters: not configured. Use /search.")

    scan_age = _event_age_seconds(latest_scan) if latest_scan else None
    lines.append(
        "Last completed scan: "
        + (_format_age(scan_age) + " ago" if scan_age is not None else "never recorded")
    )
    lines.append(
        "Fast scan: "
        + (
            f"On every {_format_interval(config.FAST_SCAN_INTERVAL_SECONDS)} for {format_sources(config.FAST_SCAN_SOURCES)}"
            if config.FAST_SCAN_ENABLED and config.FAST_SCAN_INTERVAL_SECONDS
            else "Off"
        )
    )
    lines.append(f"Scan lock: {'busy' if _SCAN_LOCK.locked() else 'idle'}")
    lines.append(
        "Auto-reply queue: "
        f"{queue['queued']} queued, {'running' if queue['running'] else 'idle'}, "
        f"{queue['completed']} done, {queue['failed']} failed"
    )
    lines.append(f"New listings last 24h: {_format_count_rows(listing_counts)}")
    lines.append(
        "Warnings/errors last 24h: "
        f"{level_counts.get('warning', 0)} warnings, {level_counts.get('error', 0)} errors"
    )
    lines.append(f"Auto-replies last 24h: {_format_auto_reply_summary_rows(auto_summary)}")
    lines.append("Sources:")
    for source in ALL_SOURCES:
        lines.append(
            "- " + _format_source_status_line(
                source,
                latest_source_events.get(source),
                source_health.get(source),
            )
        )
    return "\n".join(lines)


async def _build_daily_summary_text() -> str:
    latest_scan = await db.get_latest_bot_event(
        ("scheduled_scan_finished", "fast_scan_finished", "manual_scan_finished")
    )
    listing_counts = await db.get_event_counts_since("listing_new", hours=24)
    level_counts = await db.get_level_counts_since(hours=24)
    auto_summary = await db.get_auto_reply_summary_since(hours=24)
    scan_age = _event_age_seconds(latest_scan) if latest_scan else None
    return _format_daily_summary_text(listing_counts, auto_summary, level_counts, scan_age)


def _format_filters(user_filters: dict) -> str:
    max_price = user_filters["max_price"]
    min_size = user_filters["min_size_m2"]
    price_text = f"EUR {max_price}/month" if max_price else "No limit"
    bedrooms_text = user_filters["min_bedrooms"] or "No minimum"
    size_text = f"{min_size} m2" if min_size else "No minimum"
    kamernet_property_type = format_kamernet_property_types(
        user_filters.get("kamernet_property_type", DEFAULT_KAMERNET_PROPERTY_TYPE),
    )
    auto_reply_text = "On" if user_filters.get("auto_reply_enabled") else "Off"
    notification_sources = format_sources(user_filters.get("enabled_sources"))
    status_text = "Setup in progress"
    if not user_filters.get("setup_in_progress"):
        status_text = "Active" if user_filters["active"] else "Paused"
    return (
        "Active filters:\n"
        f"City: {user_filters['city']}\n"
        f"Kamernet property types: {kamernet_property_type}\n"
        f"Notification sites: {notification_sources}\n"
        f"Auto-reply: {auto_reply_text}\n"
        "Kamernet search radius: 5 km\n"
        f"Max rent: {price_text}\n"
        f"Minimum bedrooms/rooms: {bedrooms_text}\n"
        f"Minimum size: {size_text}\n"
        f"Status: {status_text}"
    )


async def _parse_sources_or_reply(update: Update, args: list[str]) -> tuple[str, ...] | None:
    sources, invalid = parse_source_tokens(args)
    if invalid or not sources:
        await update.message.reply_text(_sources_usage(invalid))
        return None
    return tuple(sources)


def _source_args_include_all(args: list[str]) -> bool:
    return any(arg.strip(" ,;").casefold() == "all" for arg in args)


def _format_sources_status(user_filters: dict) -> str:
    return (
        "Notification sites:\n"
        f"Enabled: {format_sources(user_filters.get('enabled_sources'))}\n"
        f"Available: {format_sources(ALL_SOURCES)}\n\n"
        "Use /sources on funda, /sources off pararius, /sources only kamernet funda, or /sources all."
    )


def _sources_usage(invalid: list[str] | None = None) -> str:
    prefix = ""
    if invalid:
        prefix = "Unknown site: " + ", ".join(invalid) + "\n\n"
    return (
        prefix
        + "Use /sources status, /sources all, /sources on <site>, /sources off <site>, or /sources only <sites>.\n"
        f"Sites: {format_sources(ALL_SOURCES)}."
    )


def _format_interval(seconds: int) -> str:
    if seconds % 60 == 0 and seconds >= 60:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{seconds} second{'s' if seconds != 1 else ''}"


def _format_auto_reply_status(user_filters: dict) -> str:
    status_text = "On" if user_filters.get("auto_reply_enabled") else "Off"
    source_status = "\n".join(
        _format_source_status(label, error)
        for label, error in _auto_reply_source_status()
    )
    return (
        "Auto-reply status:\n"
        f"Toggle: {status_text}\n"
        f"{source_status}\n\n"
        "Use /autoreply on or /autoreply off."
    )


def _auto_reply_source_status() -> list[tuple[str, str | None]]:
    return [
        ("Pararius", ParariusReplySettings.from_config().ready_error()),
        ("Kamernet", KamernetReplySettings.from_config().ready_error()),
        ("Funda", FundaReplySettings.from_config().ready_error()),
        ("Roofz", RoofzReplySettings.from_config().ready_error()),
    ]


def _format_source_status(label: str, error: str | None) -> str:
    return f"{label}: {'Ready' if error is None else 'Not ready - ' + error}"


def _format_event_line(event: dict) -> str:
    created_at = str(event.get("created_at", ""))[5:19]
    pieces = [created_at, event.get("event_type", "event")]
    if event.get("source"):
        pieces.append(str(event["source"]))
    if event.get("listing_id"):
        pieces.append(str(event["listing_id"]))
    if event.get("status"):
        pieces.append(str(event["status"]))
    if event.get("title"):
        pieces.append(str(event["title"])[:40])
    return " | ".join(piece for piece in pieces if piece)


async def _get_active_allowed_users() -> list[dict]:
    users = await db.get_all_active_users()
    if config.TELEGRAM_ALLOWED_CHAT_IDS:
        users = [user for user in users if user["chat_id"] in config.TELEGRAM_ALLOWED_CHAT_IDS]
    return users


def _daily_summary_time() -> time:
    hour = min(config.DAILY_SUMMARY_HOUR, 23)
    minute = min(config.DAILY_SUMMARY_MINUTE, 59)
    return time(hour=hour, minute=minute, tzinfo=_local_timezone())


def _local_timezone():
    try:
        return ZoneInfo(config.LOCAL_TIMEZONE)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown LOCAL_TIMEZONE %s; falling back to UTC.", config.LOCAL_TIMEZONE)
        return timezone.utc


def _format_source_status_line(source: str, event: dict | None, health: dict | None) -> str:
    if not event:
        base = f"{source}: no scraper event recorded"
    else:
        age = _event_age_seconds(event)
        event_type = str(event.get("event_type") or "event").replace("scraper_", "")
        status = event.get("status") or ""
        data = _decode_event_data(event)
        counts = ""
        if "listing_count" in data or "new_count" in data:
            counts = f", {data.get('listing_count', 0)} found, {data.get('new_count', 0)} new"
        detail = str(event.get("detail") or "")
        if detail:
            detail = f", {detail[:80]}"
        base = f"{source}: {event_type} {status}{counts}, {_format_age(age)} ago{detail}"

    if health and health.get("cooldown"):
        base += f" (cooldown {_format_age(health.get('cooldown_remaining_seconds'))})"
    elif health and health.get("failures"):
        base += f" ({health['failures']} consecutive failure{'s' if health['failures'] != 1 else ''})"
    return base


def _decode_event_data(event: dict | None) -> dict:
    if not event:
        return {}
    raw = event.get("data_json") or ""
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _format_count_rows(rows: list[dict]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        key = row.get("source") or row.get("status") or "total"
        counts[key] = counts.get(key, 0) + int(row.get("count") or 0)
    if not counts:
        return "none"
    return ", ".join(f"{source} {count}" for source, count in sorted(counts.items()))


def _format_auto_reply_summary_rows(rows: list[dict]) -> str:
    if not rows:
        return "none"
    return ", ".join(
        f"{row.get('source') or 'unknown'} {row.get('status') or 'unknown'} {row.get('count') or 0}"
        for row in rows
    )


def _format_daily_summary_text(
    listing_counts: list[dict],
    auto_summary: list[dict],
    level_counts: dict,
    scan_age: float | None,
) -> str:
    lines = [
        "Daily housing summary",
        "Period: last 24h",
        "",
        "New listings",
        *_format_daily_count_lines(listing_counts),
        "",
        "Auto-replies",
        *_format_daily_auto_reply_lines(auto_summary),
        "",
        "Warnings / errors",
        f"- Warnings: {_format_plural_count(level_counts.get('warning', 0), 'warning')}",
        f"- Errors: {_format_plural_count(level_counts.get('error', 0), 'error')}",
        "",
        "Health",
        "- Last completed scan: "
        + (_format_age(scan_age) + " ago" if scan_age is not None else "never recorded"),
    ]
    return "\n".join(lines)


def _format_roofz_complete_application_message(
    listing_title: str,
    status: str,
    detail: str,
    *,
    ok: bool,
    link: str = "",
) -> str:
    if ok:
        lines = [
            "Roofz complete application completed",
            "",
            f"Listing: {listing_title}",
            f"Status: {_auto_reply_status_label(status, 1)}",
        ]
        if detail:
            lines.append(f"Detail: {_compact_message_detail(detail)}")
        return "\n".join(lines)

    lines = [
        "Roofz complete application needs attention",
        "",
        f"Listing: {listing_title}",
        f"Status: {_auto_reply_status_label(status, 1)}",
    ]
    if detail:
        lines.append(f"Problem: {_compact_message_detail(detail)}")
    if link:
        lines.append(f"Link: {link}")
    lines.append("Action: complete or verify this application manually.")
    return "\n".join(lines)


def _format_roofz_preapplication_warning(listing: Listing, result) -> str:
    lines = [
        "Roofz pre-application needs attention",
        "",
        f"Listing: {listing.title}",
        f"Status: {_auto_reply_status_label(result.status, 1)}",
    ]
    if result.detail:
        lines.append(f"Problem: {_compact_message_detail(result.detail)}")
    lines.extend(
        [
            "Action: check this listing manually.",
            f"Link: {listing.url}",
        ]
    )
    return "\n".join(lines)


def _compact_message_detail(detail: str, limit: int = 900) -> str:
    compact = " ".join(str(detail or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)].rstrip() + "..."


def _format_daily_count_lines(rows: list[dict]) -> list[str]:
    counts: dict[str, int] = {}
    for row in rows:
        source = row.get("source") or row.get("status") or "total"
        counts[source] = counts.get(source, 0) + int(row.get("count") or 0)
    if not counts:
        return ["- None"]
    return [
        f"- {_source_label(source)}: {count}"
        for source, count in sorted(counts.items(), key=lambda item: _source_sort_key(item[0]))
    ]


def _format_daily_auto_reply_lines(rows: list[dict]) -> list[str]:
    grouped: dict[str, list[str]] = {}
    for row in rows:
        source = row.get("source") or "unknown"
        status = row.get("status") or "unknown"
        count = int(row.get("count") or 0)
        grouped.setdefault(source, []).append(f"{count} {_auto_reply_status_label(status, count)}")
    if not grouped:
        return ["- None"]
    return [
        f"- {_source_label(source)}: {', '.join(statuses)}"
        for source, statuses in sorted(grouped.items(), key=lambda item: _source_sort_key(item[0]))
    ]


def _auto_reply_status_label(status: str, count: int) -> str:
    labels = {
        "sent": "sent",
        "submitted_unconfirmed": "submitted, unconfirmed",
        "confirmation_confirmed": "confirmation confirmed",
        "confirmation_missing": "confirmation missing",
        "confirmation_error": "confirmation error",
        "preapplication_confirmed": "pre-application confirmed",
        "preapplication_sent": "pre-application sent",
        "preapplication_confirmation_missing": "pre-application confirmation missing",
        "sent_preapplication_pending": "pre-application pending",
        "sent_preapplication_failed": "pre-application failed",
        "complete_application_sent": "completed",
        "complete_application_dry_run_ready": "complete application ready, dry run",
        "complete_application_missing_link": "application link missing",
        "complete_application_not_ready": "complete application not ready",
        "complete_application_error": "complete application error",
        "complete_application_api_error": "complete application API error",
        "complete_application_update_failed": "application update failed",
        "complete_application_finalize_failed": "application finalize failed",
        "complete_application_pending_verification": "pending verification",
        "complete_application_validation_failed": "validation failed",
        "complete_application_browser_error": "browser fallback error",
        "complete_application_browser_timeout": "browser fallback timeout",
        "needs_verification": "needs verification",
        "login_failed": "login failed",
        "error": "error",
    }
    label = labels.get(status, status.replace("_", " "))
    if count == 1:
        return label
    plural_labels = {
        "needs verification": "need verification",
        "error": "errors",
    }
    return plural_labels.get(label, label)


def _format_plural_count(count, singular: str) -> str:
    value = int(count or 0)
    suffix = "" if value == 1 else "s"
    return f"{value} {singular}{suffix}"


def _source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source.capitalize() if source else "Unknown")


def _source_sort_key(source: str) -> tuple[int, str]:
    try:
        return (ALL_SOURCES.index(source), source)
    except ValueError:
        return (len(ALL_SOURCES), source)


def _auto_reply_status_ok(status: str) -> bool:
    return status in {
        "dry_run_ready",
        "sent",
        "confirmation_confirmed",
        "preapplication_confirmed",
        "preapplication_sent",
        "complete_application_sent",
    }


def _roofz_complete_application_status_ok(status: str) -> bool:
    return status in {
        "complete_application_sent",
        "complete_application_dry_run_ready",
    }


def _event_age_seconds(event: dict | None) -> float | None:
    if not event:
        return None
    created_at = _parse_event_timestamp(event.get("created_at"))
    if not created_at:
        return None
    return max(0.0, round((datetime.now(timezone.utc) - created_at).total_seconds(), 3))


def _parse_event_timestamp(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_age(seconds: float | int | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"
