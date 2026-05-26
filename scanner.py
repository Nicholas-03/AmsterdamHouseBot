from html import escape
import logging
from datetime import datetime, timezone

from telegram import Bot
from telegram.constants import ParseMode

import db
from funda_replier import FundaReplier, FundaReplyResult, FundaReplySettings
from kamernet_replier import (
    KamernetReplier,
    KamernetReplyResult,
    KamernetReplySettings,
    should_skip_existing_reply,
)
from roofz_replier import RoofzReplier, RoofzReplyResult, RoofzReplySettings
from scrapers.funda import FundaScraper
from scrapers.huurwoningen import HuurwoningenScraper
from scrapers.kamernet import KamernetScraper
from scrapers.pararius import ParariusScraper
from scrapers.roofz import RoofzScraper

logger = logging.getLogger(__name__)

_AUTO_REPLY_OK_STATUSES = {
    "dry_run_ready",
    "sent",
    "confirmation_confirmed",
    "preapplication_confirmed",
    "preapplication_sent",
}


_FILTER_MATCH_KEYS = (
    "city",
    "max_price",
    "min_bedrooms",
    "min_size_m2",
    "kamernet_property_type",
    "auto_reply_enabled",
)


async def run_scan_for_user(bot: Bot, user_filters: dict, require_active: bool = True) -> int:
    chat_id = user_filters["chat_id"]
    await db.log_event(
        "scan_user_started",
        chat_id=chat_id,
        status="started",
        data={
            "require_active": require_active,
            "city": user_filters.get("city"),
            "max_price": user_filters.get("max_price"),
            "min_bedrooms": user_filters.get("min_bedrooms"),
            "min_size_m2": user_filters.get("min_size_m2"),
            "kamernet_property_type": user_filters.get("kamernet_property_type"),
            "auto_reply_enabled": user_filters.get("auto_reply_enabled"),
        },
    )
    kamernet_reply_settings = KamernetReplySettings.from_config()
    kamernet_reply_settings_error = kamernet_reply_settings.ready_error()
    funda_reply_settings = FundaReplySettings.from_config()
    funda_reply_settings_error = funda_reply_settings.ready_error()
    roofz_reply_settings = RoofzReplySettings.from_config()
    roofz_reply_settings_error = roofz_reply_settings.ready_error()
    if user_filters.get("auto_reply_enabled"):
        if kamernet_reply_settings.enabled and kamernet_reply_settings_error:
            logger.warning("Kamernet auto-reply is unavailable for this scan: %s", kamernet_reply_settings_error)
            await db.log_event(
                "auto_reply_settings_unavailable",
                level="warning",
                chat_id=chat_id,
                source=KamernetScraper.SOURCE,
                status="not_ready",
                detail=kamernet_reply_settings_error,
            )
        if funda_reply_settings.enabled and funda_reply_settings_error:
            logger.warning("Funda auto-reply is unavailable for this scan: %s", funda_reply_settings_error)
            await db.log_event(
                "auto_reply_settings_unavailable",
                level="warning",
                chat_id=chat_id,
                source=FundaScraper.SOURCE,
                status="not_ready",
                detail=funda_reply_settings_error,
            )
        if roofz_reply_settings.enabled and roofz_reply_settings_error:
            logger.warning("Roofz auto-reply is unavailable for this scan: %s", roofz_reply_settings_error)
            await db.log_event(
                "auto_reply_settings_unavailable",
                level="warning",
                chat_id=chat_id,
                source=RoofzScraper.SOURCE,
                status="not_ready",
                detail=roofz_reply_settings_error,
            )

    scrapers = [
        ParariusScraper(
            city=user_filters["city"],
            max_price=user_filters["max_price"],
            min_bedrooms=user_filters["min_bedrooms"],
            min_size_m2=user_filters["min_size_m2"],
        ),
        FundaScraper(
            city=user_filters["city"],
            max_price=user_filters["max_price"],
            min_bedrooms=user_filters["min_bedrooms"],
            min_size_m2=user_filters["min_size_m2"],
        ),
        KamernetScraper(
            city=user_filters["city"],
            max_price=user_filters["max_price"],
            min_bedrooms=user_filters["min_bedrooms"],
            min_size_m2=user_filters["min_size_m2"],
            property_type=user_filters.get("kamernet_property_type", "any"),
        ),
        HuurwoningenScraper(
            city=user_filters["city"],
            max_price=user_filters["max_price"],
            min_bedrooms=user_filters["min_bedrooms"],
            min_size_m2=user_filters["min_size_m2"],
        ),
        RoofzScraper(
            city=user_filters["city"],
            max_price=user_filters["max_price"],
            min_bedrooms=user_filters["min_bedrooms"],
            min_size_m2=user_filters["min_size_m2"],
        ),
    ]

    new_count = 0
    reply_attempts = {
        KamernetScraper.SOURCE: 0,
        FundaScraper.SOURCE: 0,
        RoofzScraper.SOURCE: 0,
    }
    for scraper in scrapers:
        try:
            if not await _scan_is_current(chat_id, user_filters, require_active):
                logger.info("Scan stopped for user %s because filters changed or setup is open.", chat_id)
                await db.log_event(
                    "scan_user_stopped",
                    level="warning",
                    chat_id=chat_id,
                    status="stale_before_scraper",
                    detail="Filters changed, notifications paused, or setup is open.",
                )
                return new_count

            await db.log_event("scraper_started", chat_id=chat_id, source=scraper.SOURCE, status="started")
            listings = await scraper.scrape()
            new_from_scraper = 0
            for listing in listings:
                if not await _scan_is_current(chat_id, user_filters, require_active):
                    logger.info("Scan stopped for user %s before sending stale results.", chat_id)
                    await db.log_event(
                        "scan_user_stopped",
                        level="warning",
                        chat_id=chat_id,
                        source=listing.source,
                        listing_id=listing.id,
                        title=listing.title,
                        status="stale_before_notification",
                        detail="Filters changed, notifications paused, or setup is open.",
                    )
                    return new_count

                if await db.was_sent(chat_id, listing.source, listing.id):
                    continue
                seen_listing = await db.mark_seen(listing.source, listing.id, listing.url, listing.title, listing.price)
                first_seen_at = seen_listing.get("scraped_at") if seen_listing else None
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
                        "first_seen_at": first_seen_at,
                        "first_seen_by_bot": bool(seen_listing.get("inserted")) if seen_listing else False,
                    },
                )
                await _send_notification(bot, chat_id, listing)
                await db.mark_sent(chat_id, listing.source, listing.id)
                if user_filters.get("auto_reply_enabled"):
                    if listing.source == KamernetScraper.SOURCE:
                        attempted = await _maybe_auto_reply_to_listing(
                            chat_id,
                            listing,
                            kamernet_reply_settings,
                            kamernet_reply_settings_error,
                            reply_attempts[KamernetScraper.SOURCE],
                            KamernetReplier,
                            KamernetReplyResult,
                            "Kamernet",
                            bot,
                            first_seen_at,
                        )
                        if attempted:
                            reply_attempts[KamernetScraper.SOURCE] += 1
                    elif listing.source == FundaScraper.SOURCE:
                        attempted = await _maybe_auto_reply_to_listing(
                            chat_id,
                            listing,
                            funda_reply_settings,
                            funda_reply_settings_error,
                            reply_attempts[FundaScraper.SOURCE],
                            FundaReplier,
                            FundaReplyResult,
                            "Funda",
                            bot,
                            first_seen_at,
                        )
                        if attempted:
                            reply_attempts[FundaScraper.SOURCE] += 1
                    elif listing.source == RoofzScraper.SOURCE:
                        attempted = await _maybe_auto_reply_to_listing(
                            chat_id,
                            listing,
                            roofz_reply_settings,
                            roofz_reply_settings_error,
                            reply_attempts[RoofzScraper.SOURCE],
                            RoofzReplier,
                            RoofzReplyResult,
                            "Roofz",
                            bot,
                            first_seen_at,
                        )
                        if attempted:
                            reply_attempts[RoofzScraper.SOURCE] += 1
                elif listing.source in {KamernetScraper.SOURCE, FundaScraper.SOURCE, RoofzScraper.SOURCE}:
                    await db.log_event(
                        "auto_reply_user_disabled",
                        chat_id=chat_id,
                        source=listing.source,
                        listing_id=listing.id,
                        title=listing.title,
                        status="skipped",
                    )
                new_count += 1
                new_from_scraper += 1
            logger.info(
                "%s: %d listings found, %d new for user %s",
                scraper.SOURCE,
                len(listings),
                new_from_scraper,
                chat_id,
            )
            scrape_error = getattr(scraper, "last_error", "")
            if scrape_error:
                await db.log_event(
                    "scraper_failed",
                    level="error",
                    chat_id=chat_id,
                    source=scraper.SOURCE,
                    status="error",
                    detail=scrape_error,
                    data={"listing_count": len(listings), "new_count": new_from_scraper},
                )
            else:
                await db.log_event(
                    "scraper_finished",
                    chat_id=chat_id,
                    source=scraper.SOURCE,
                    status="ok",
                    data={"listing_count": len(listings), "new_count": new_from_scraper},
                )
        except Exception as exc:
            logger.error("Scraper %s failed for user %s: %s", scraper.SOURCE, chat_id, exc)
            await db.log_event(
                "scraper_failed",
                level="error",
                chat_id=chat_id,
                source=scraper.SOURCE,
                status="error",
                detail=str(exc),
            )

    await db.log_event(
        "scan_user_finished",
        chat_id=chat_id,
        status="finished",
        data={"new_count": new_count, "reply_attempts": reply_attempts},
    )
    return new_count


async def _maybe_auto_reply_to_listing(
    chat_id: int,
    listing,
    settings,
    settings_error: str | None,
    attempts_so_far: int,
    replier_cls,
    result_cls,
    source_label: str,
    bot: Bot | None = None,
    first_seen_at: str | None = None,
) -> bool:
    if not settings.enabled:
        await db.log_event(
            "auto_reply_source_disabled",
            chat_id=chat_id,
            source=listing.source,
            listing_id=listing.id,
            title=listing.title,
            status="skipped",
        )
        return False
    if settings_error:
        await db.log_event(
            "auto_reply_settings_unavailable",
            level="warning",
            chat_id=chat_id,
            source=listing.source,
            listing_id=listing.id,
            title=listing.title,
            status="skipped",
            detail=settings_error,
        )
        return False
    if settings.max_per_scan and attempts_so_far >= settings.max_per_scan:
        logger.info(
            "%s auto-reply cap reached for this scan: %d/%d",
            source_label,
            attempts_so_far,
            settings.max_per_scan,
        )
        await db.log_event(
            "auto_reply_cap_reached",
            chat_id=chat_id,
            source=listing.source,
            listing_id=listing.id,
            title=listing.title,
            status="skipped",
            data={"attempts_so_far": attempts_so_far, "max_per_scan": settings.max_per_scan},
        )
        return False

    existing_reply = await db.get_auto_reply(listing.source, listing.id)
    if should_skip_existing_reply(existing_reply, settings.dry_run):
        logger.info(
            "%s auto-reply skipped for %s; existing status is %s.",
            source_label,
            listing.id,
            existing_reply["status"],
        )
        await db.log_event(
            "auto_reply_skipped_existing",
            chat_id=chat_id,
            source=listing.source,
            listing_id=listing.id,
            title=listing.title,
            status=existing_reply["status"],
            data={"dry_run": existing_reply["dry_run"]},
        )
        return False

    await db.log_event(
        "auto_reply_attempting",
        chat_id=chat_id,
        source=listing.source,
        listing_id=listing.id,
        title=listing.title,
        status="attempting",
        data={
            "dry_run": settings.dry_run,
            **_reply_timing_data(first_seen_at, None),
        },
    )
    await db.mark_auto_reply_result(
        listing.source,
        listing.id,
        listing.url,
        chat_id,
        "attempting",
        settings.dry_run,
        first_seen_at=first_seen_at,
    )

    try:
        async with replier_cls(settings) as replier:
            result = await replier.reply_to_listing(listing)
    except Exception as exc:
        logger.exception("%s auto-reply crashed for listing %s", source_label, listing.id)
        result = result_cls("error", str(exc))

    await db.mark_auto_reply_result(
        listing.source,
        listing.id,
        listing.url,
        chat_id,
        result.status,
        settings.dry_run,
        result.detail,
        first_seen_at=first_seen_at,
        sent_at=getattr(result, "sent_at", None),
        confirmation_at=getattr(result, "confirmation_at", None),
    )
    logger.info(
        "%s auto-reply result for %s: %s (%s)",
        source_label,
        listing.id,
        result.status,
        result.detail,
    )
    await db.log_event(
        "auto_reply_result",
        level="info" if result.status in _AUTO_REPLY_OK_STATUSES else "warning",
        chat_id=chat_id,
        source=listing.source,
        listing_id=listing.id,
        title=listing.title,
        status=result.status,
        detail=result.detail,
        data={
            "dry_run": settings.dry_run,
            **_reply_timing_data(first_seen_at, result),
        },
    )
    if bot and _should_warn_auto_reply(result.status, settings.dry_run):
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"Warning: {source_label} auto-reply needs attention for {listing.title}.\n"
                f"Status: {result.status}\n"
                f"Detail: {result.detail}\n"
                f"Listing: {listing.url}"
            ),
            disable_web_page_preview=True,
        )
        await db.log_event(
            "auto_reply_warning_sent",
            level="warning",
            chat_id=chat_id,
            source=listing.source,
            listing_id=listing.id,
            title=listing.title,
            status=result.status,
            detail=result.detail,
        )
    return True


def _reply_timing_data(first_seen_at: str | None, result=None) -> dict:
    now = datetime.now(timezone.utc)
    sent_at = getattr(result, "sent_at", None) if result else None
    confirmation_at = getattr(result, "confirmation_at", None) if result else None
    first_seen_dt = _parse_timestamp(first_seen_at)
    sent_dt = _parse_timestamp(sent_at)
    confirmation_dt = _parse_timestamp(confirmation_at)

    data = {
        "first_seen_at": first_seen_at,
        "seconds_since_first_seen": _seconds_between(first_seen_dt, now),
    }
    if sent_dt:
        data["reply_sent_at"] = _format_timestamp(sent_dt)
        data["seconds_to_reply"] = _seconds_between(first_seen_dt, sent_dt)
    if confirmation_dt:
        data["confirmation_at"] = _format_timestamp(confirmation_dt)
        data["seconds_to_confirmation"] = _seconds_between(first_seen_dt, confirmation_dt)
    return {key: value for key, value in data.items() if value is not None}


def _parse_timestamp(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = value.strip().replace("Z", "+00:00")
        if not normalized:
            return None
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            try:
                parsed = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _seconds_between(start: datetime | None, end: datetime | None) -> float | None:
    if not start or not end:
        return None
    return max(0.0, round((end - start).total_seconds(), 3))


def _should_warn_auto_reply(status: str, dry_run: bool) -> bool:
    if dry_run:
        return False
    return status not in _AUTO_REPLY_OK_STATUSES


async def _scan_is_current(chat_id: int, user_filters: dict, require_active: bool) -> bool:
    latest_filters = await db.get_filters(chat_id)
    if not latest_filters or latest_filters.get("setup_in_progress"):
        return False
    if require_active and not latest_filters["active"]:
        return False
    return all(
        latest_filters.get(key) == user_filters.get(key)
        for key in _FILTER_MATCH_KEYS
    )


async def _send_notification(bot: Bot, chat_id: int, listing) -> None:
    source = listing.source.capitalize()
    parts = [
        f"<b>{escape(listing.title)}</b>",
        f"Address: {escape(listing.address)}",
        f"Rent: {escape(listing.price)}",
    ]
    if listing.rooms:
        parts.append(f"Bedrooms/rooms: {escape(listing.rooms)}")
    if listing.size_m2:
        parts.append(f"Size: {escape(listing.size_m2)}")
    parts.append(f'\n<a href="{escape(listing.url)}">View listing</a>')

    text = f"<b>New on {escape(source)}</b>\n\n" + "\n".join(parts)

    if listing.image_url:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=listing.image_url,
                caption=text,
                parse_mode=ParseMode.HTML,
            )
            await db.log_event(
                "notification_sent",
                chat_id=chat_id,
                source=listing.source,
                listing_id=listing.id,
                title=listing.title,
                status="photo_sent",
            )
            return
        except Exception as exc:
            logger.warning("Photo send failed (%s), retrying as text: %s", chat_id, exc)
            await db.log_event(
                "notification_photo_failed",
                level="warning",
                chat_id=chat_id,
                source=listing.source,
                listing_id=listing.id,
                title=listing.title,
                status="retrying_text",
                detail=str(exc),
            )

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )
        await db.log_event(
            "notification_sent",
            chat_id=chat_id,
            source=listing.source,
            listing_id=listing.id,
            title=listing.title,
            status="message_sent",
        )
    except Exception as exc:
        logger.error("Notification failed for %s: %s", chat_id, exc)
        await db.log_event(
            "notification_failed",
            level="error",
            chat_id=chat_id,
            source=listing.source,
            listing_id=listing.id,
            title=listing.title,
            status="error",
            detail=str(exc),
        )
        raise
