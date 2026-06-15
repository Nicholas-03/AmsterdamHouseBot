import asyncio
from html import escape
import logging
import re
from datetime import datetime, timezone

from telegram import Bot
from telegram.constants import ParseMode

from housebot.auto_reply_queue import AUTO_REPLY_QUEUE, AutoReplyJob
from housebot import config
from housebot import db
from housebot.funda_replier import FundaReplier, FundaReplyResult, FundaReplySettings
from housebot.kamernet_replier import (
    KamernetReplier,
    KamernetReplyResult,
    KamernetReplySettings,
    should_skip_existing_reply,
)
from housebot.notification_sources import ALL_SOURCES, SOURCE_LABELS, normalize_sources
from housebot.pararius_replier import ParariusReplier, ParariusReplyResult, ParariusReplySettings
from housebot.roofz_replier import RoofzReplier, RoofzReplyResult, RoofzReplySettings
from housebot.scrapers.funda import FundaScraper
from housebot.scrapers.huurwoningen import HuurwoningenScraper
from housebot.scrapers.kamernet import KamernetScraper
from housebot.scrapers.pararius import ParariusScraper
from housebot.scrapers.roofz import RoofzScraper
from housebot.source_health import SOURCE_HEALTH

logger = logging.getLogger(__name__)

_AUTO_REPLY_OK_STATUSES = {
    "dry_run_ready",
    "sent",
    "confirmation_confirmed",
    "preapplication_confirmed",
    "preapplication_sent",
    "submitted_unconfirmed",
}
_AUTO_REPLY_CONFIRMATION_STATUSES = _AUTO_REPLY_OK_STATUSES - {"dry_run_ready"}


_FILTER_MATCH_KEYS = (
    "city",
    "max_price",
    "min_bedrooms",
    "min_size_m2",
    "kamernet_property_type",
    "auto_reply_enabled",
    "auto_reply_sources",
    "enabled_sources",
)


def _auto_reply_enabled_for_source(user_filters: dict, source: str) -> bool:
    if not user_filters.get("auto_reply_enabled"):
        return False
    sources = user_filters.get("auto_reply_sources")
    if sources is None:
        return True
    return source in sources


async def run_scan_for_user(
    bot: Bot,
    user_filters: dict,
    require_active: bool = True,
    source_filter: set[str] | None = None,
) -> int:
    chat_id = user_filters["chat_id"]
    configured_sources = normalize_sources(user_filters.get("enabled_sources"))
    enabled_sources = tuple(
        source
        for source in configured_sources
        if source_filter is None or source in source_filter
    )
    enabled_source_set = set(enabled_sources)
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
            "enabled_sources": list(enabled_sources),
            "configured_sources": list(configured_sources),
            "source_filter": sorted(source_filter) if source_filter else None,
        },
    )
    if not enabled_sources:
        await db.log_event(
            "scan_user_skipped",
            chat_id=chat_id,
            status="no_enabled_sources",
            data={"source_filter": sorted(source_filter) if source_filter else None},
        )
        return 0
    kamernet_reply_settings = KamernetReplySettings.from_config()
    kamernet_reply_settings_error = kamernet_reply_settings.ready_error()
    funda_reply_settings = FundaReplySettings.from_config()
    funda_reply_settings_error = funda_reply_settings.ready_error()
    roofz_reply_settings = RoofzReplySettings.from_config()
    roofz_reply_settings_error = roofz_reply_settings.ready_error()
    pararius_reply_settings = ParariusReplySettings.from_config()
    pararius_reply_settings_error = pararius_reply_settings.ready_error()
    if user_filters.get("auto_reply_enabled"):
        if (
            ParariusScraper.SOURCE in enabled_source_set
            and pararius_reply_settings.enabled
            and pararius_reply_settings_error
        ):
            logger.warning("Pararius auto-reply is unavailable for this scan: %s", pararius_reply_settings_error)
            await db.log_event(
                "auto_reply_settings_unavailable",
                level="warning",
                chat_id=chat_id,
                source=ParariusScraper.SOURCE,
                status="not_ready",
                detail=pararius_reply_settings_error,
            )
        if (
            KamernetScraper.SOURCE in enabled_source_set
            and kamernet_reply_settings.enabled
            and kamernet_reply_settings_error
        ):
            logger.warning("Kamernet auto-reply is unavailable for this scan: %s", kamernet_reply_settings_error)
            await db.log_event(
                "auto_reply_settings_unavailable",
                level="warning",
                chat_id=chat_id,
                source=KamernetScraper.SOURCE,
                status="not_ready",
                detail=kamernet_reply_settings_error,
            )
        if (
            FundaScraper.SOURCE in enabled_source_set
            and funda_reply_settings.enabled
            and funda_reply_settings_error
        ):
            logger.warning("Funda auto-reply is unavailable for this scan: %s", funda_reply_settings_error)
            await db.log_event(
                "auto_reply_settings_unavailable",
                level="warning",
                chat_id=chat_id,
                source=FundaScraper.SOURCE,
                status="not_ready",
                detail=funda_reply_settings_error,
            )
        if (
            RoofzScraper.SOURCE in enabled_source_set
            and roofz_reply_settings.enabled
            and roofz_reply_settings_error
        ):
            logger.warning("Roofz auto-reply is unavailable for this scan: %s", roofz_reply_settings_error)
            await db.log_event(
                "auto_reply_settings_unavailable",
                level="warning",
                chat_id=chat_id,
                source=RoofzScraper.SOURCE,
                status="not_ready",
                detail=roofz_reply_settings_error,
            )

    scraper_factories = {
        ParariusScraper.SOURCE: lambda: ParariusScraper(
            city=user_filters["city"],
            max_price=user_filters["max_price"],
            min_bedrooms=user_filters["min_bedrooms"],
            min_size_m2=user_filters["min_size_m2"],
            student_compatibility_filter_enabled=config.PARARIUS_STUDENT_COMPATIBILITY_FILTER_ENABLED,
        ),
        FundaScraper.SOURCE: lambda: FundaScraper(
            city=user_filters["city"],
            max_price=user_filters["max_price"],
            min_bedrooms=user_filters["min_bedrooms"],
            min_size_m2=user_filters["min_size_m2"],
            keywords=config.FUNDA_KEYWORDS,
        ),
        KamernetScraper.SOURCE: lambda: KamernetScraper(
            city=user_filters["city"],
            max_price=user_filters["max_price"],
            min_bedrooms=user_filters["min_bedrooms"],
            min_size_m2=user_filters["min_size_m2"],
            property_type=user_filters.get("kamernet_property_type", "any"),
        ),
        HuurwoningenScraper.SOURCE: lambda: HuurwoningenScraper(
            city=user_filters["city"],
            max_price=user_filters["max_price"],
            min_bedrooms=user_filters["min_bedrooms"],
            min_size_m2=user_filters["min_size_m2"],
            student_compatibility_filter_enabled=config.HUURWONINGEN_STUDENT_COMPATIBILITY_FILTER_ENABLED,
        ),
        RoofzScraper.SOURCE: lambda: RoofzScraper(
            city=user_filters["city"],
            max_price=user_filters["max_price"],
            min_bedrooms=user_filters["min_bedrooms"],
            min_size_m2=user_filters["min_size_m2"],
        ),
    }
    scrapers = [
        scraper_factories[source]()
        for source in ALL_SOURCES
        if source in enabled_source_set
    ]

    new_count = 0
    reply_attempts = {
        ParariusScraper.SOURCE: 0,
        KamernetScraper.SOURCE: 0,
        FundaScraper.SOURCE: 0,
        RoofzScraper.SOURCE: 0,
    }
    for scraper in scrapers:
        try:
            in_cooldown, cooldown_seconds = SOURCE_HEALTH.cooldown_status(scraper.SOURCE)
            if in_cooldown:
                await db.log_event(
                    "scraper_skipped",
                    level="warning",
                    chat_id=chat_id,
                    source=scraper.SOURCE,
                    status="cooldown",
                    detail=f"Skipped because the source is cooling down for {cooldown_seconds} more seconds.",
                    data={"cooldown_remaining_seconds": cooldown_seconds},
                )
                continue

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

            scraper_data = _scraper_event_data(scraper, user_filters)
            await db.log_event(
                "scraper_started",
                chat_id=chat_id,
                source=scraper.SOURCE,
                status="started",
                data=scraper_data,
            )
            listings = await _scrape_with_timeout(scraper)
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
                source_available_at = listing.reply_data.get("available_at")
                reply_timing_start = source_available_at or first_seen_at
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
                        "source_available_at": source_available_at,
                        "first_seen_by_bot": bool(seen_listing.get("inserted")) if seen_listing else False,
                        "student_compatibility_reason": listing.reply_data.get("student_compatibility_reason"),
                    },
                )
                await _send_notification(bot, chat_id, listing)
                await db.mark_sent(chat_id, listing.source, listing.id)
                if _auto_reply_enabled_for_source(user_filters, listing.source):
                    if listing.source == ParariusScraper.SOURCE:
                        attempted = await _enqueue_auto_reply_to_listing(
                            chat_id,
                            listing,
                            pararius_reply_settings,
                            pararius_reply_settings_error,
                            reply_attempts[ParariusScraper.SOURCE],
                            ParariusReplier,
                            ParariusReplyResult,
                            "Pararius",
                            bot,
                            reply_timing_start,
                        )
                        if attempted:
                            reply_attempts[ParariusScraper.SOURCE] += 1
                    elif listing.source == KamernetScraper.SOURCE:
                        attempted = await _enqueue_auto_reply_to_listing(
                            chat_id,
                            listing,
                            kamernet_reply_settings,
                            kamernet_reply_settings_error,
                            reply_attempts[KamernetScraper.SOURCE],
                            KamernetReplier,
                            KamernetReplyResult,
                            "Kamernet",
                            bot,
                            reply_timing_start,
                        )
                        if attempted:
                            reply_attempts[KamernetScraper.SOURCE] += 1
                    elif listing.source == FundaScraper.SOURCE:
                        attempted = await _enqueue_auto_reply_to_listing(
                            chat_id,
                            listing,
                            funda_reply_settings,
                            funda_reply_settings_error,
                            reply_attempts[FundaScraper.SOURCE],
                            FundaReplier,
                            FundaReplyResult,
                            "Funda",
                            bot,
                            reply_timing_start,
                        )
                        if attempted:
                            reply_attempts[FundaScraper.SOURCE] += 1
                    elif listing.source == RoofzScraper.SOURCE:
                        attempted = await _enqueue_auto_reply_to_listing(
                            chat_id,
                            listing,
                            roofz_reply_settings,
                            roofz_reply_settings_error,
                            reply_attempts[RoofzScraper.SOURCE],
                            RoofzReplier,
                            RoofzReplyResult,
                            "Roofz",
                            bot,
                            reply_timing_start,
                        )
                        if attempted:
                            reply_attempts[RoofzScraper.SOURCE] += 1
                elif listing.source in {
                    ParariusScraper.SOURCE,
                    KamernetScraper.SOURCE,
                    FundaScraper.SOURCE,
                    RoofzScraper.SOURCE,
                }:
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
                await SOURCE_HEALTH.record_failure(scraper.SOURCE, status="error", detail=scrape_error)
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
                await SOURCE_HEALTH.record_success(scraper.SOURCE)
                await db.log_event(
                    "scraper_finished",
                    chat_id=chat_id,
                    source=scraper.SOURCE,
                    status="ok",
                    data={
                        "listing_count": len(listings),
                        "new_count": new_from_scraper,
                        **scraper_data,
                    },
                )
        except TimeoutError:
            detail = f"{scraper.SOURCE} scraper timed out after {config.SCRAPER_TIMEOUT_SECONDS} seconds."
            logger.error("Scraper %s timed out for user %s", scraper.SOURCE, chat_id)
            await SOURCE_HEALTH.record_failure(scraper.SOURCE, status="timeout", detail=detail)
            await db.log_event(
                "scraper_failed",
                level="error",
                chat_id=chat_id,
                source=scraper.SOURCE,
                status="timeout",
                detail=detail,
            )
        except Exception as exc:
            logger.error("Scraper %s failed for user %s: %s", scraper.SOURCE, chat_id, exc)
            await SOURCE_HEALTH.record_failure(scraper.SOURCE, status="error", detail=str(exc))
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
        data={
            "new_count": new_count,
            "reply_attempts": reply_attempts,
            "enabled_sources": list(enabled_sources),
        },
    )
    return new_count


async def _scrape_with_timeout(scraper) -> list:
    timeout_seconds = config.SCRAPER_TIMEOUT_SECONDS
    if not timeout_seconds:
        return await scraper.scrape()
    return await asyncio.wait_for(scraper.scrape(), timeout=timeout_seconds)


def _scraper_event_data(scraper, user_filters: dict) -> dict:
    data = {
        "city": user_filters.get("city"),
        "max_price": user_filters.get("max_price"),
        "min_bedrooms": user_filters.get("min_bedrooms"),
        "min_size_m2": user_filters.get("min_size_m2"),
    }
    if scraper.SOURCE == KamernetScraper.SOURCE:
        data["kamernet_property_type"] = user_filters.get("kamernet_property_type", "any")
    if scraper.SOURCE == FundaScraper.SOURCE:
        data["funda_keywords"] = list(getattr(scraper, "keywords", ()))
    if scraper.SOURCE in {ParariusScraper.SOURCE, HuurwoningenScraper.SOURCE}:
        data["student_compatibility_filter_enabled"] = bool(
            getattr(scraper, "student_compatibility_filter_enabled", False)
        )
    build_url = getattr(scraper, "_build_url", None)
    if callable(build_url):
        try:
            data["search_url"] = build_url()
        except Exception as exc:
            data["search_url_error"] = str(exc)
    return data


async def _enqueue_auto_reply_to_listing(
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

    return await AUTO_REPLY_QUEUE.enqueue(
        AutoReplyJob(
            chat_id=chat_id,
            source=listing.source,
            listing_id=listing.id,
            title=listing.title,
            runner=lambda: _maybe_auto_reply_to_listing(
                chat_id,
                listing,
                settings,
                settings_error,
                attempts_so_far,
                replier_cls,
                result_cls,
                source_label,
                bot,
                first_seen_at,
            ),
        )
    )


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
    if bot:
        if _should_confirm_auto_reply(result.status, settings.dry_run):
            await bot.send_message(
                chat_id=chat_id,
                text=_format_auto_reply_confirmation(source_label, listing, result),
                disable_web_page_preview=True,
            )
            await db.log_event(
                "auto_reply_confirmation_sent",
                chat_id=chat_id,
                source=listing.source,
                listing_id=listing.id,
                title=listing.title,
                status=result.status,
                detail=result.detail,
            )
        elif _should_warn_auto_reply(result.status, settings.dry_run):
            await bot.send_message(
                chat_id=chat_id,
                text=_format_auto_reply_warning(source_label, listing, result),
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


def _should_confirm_auto_reply(status: str, dry_run: bool) -> bool:
    if dry_run:
        return False
    return status in _AUTO_REPLY_CONFIRMATION_STATUSES


def _format_auto_reply_confirmation(source_label: str, listing, result) -> str:
    lines = [
        "Auto-reply sent",
        "",
        f"Site: {source_label}",
        f"Listing: {listing.title}",
        f"Status: {_status_label(result.status)}",
    ]
    position = _extract_reply_position(result.detail)
    if position:
        lines.append(f"Position: {position}")
    elif result.detail:
        lines.append(f"Detail: {_compact_detail(result.detail)}")
    lines.append(f"Link: {listing.url}")
    return "\n".join(lines)


def _format_auto_reply_warning(source_label: str, listing, result) -> str:
    lines = [
        "Auto-reply needs attention",
        "",
        f"Site: {source_label}",
        f"Listing: {listing.title}",
        f"Status: {_status_label(result.status)}",
    ]
    if result.detail:
        lines.append(f"Problem: {_compact_detail(result.detail)}")
    lines.extend(
        [
            "Action: check this listing manually.",
            f"Link: {listing.url}",
        ]
    )
    return "\n".join(lines)


def _extract_reply_position(detail: str) -> str:
    match = re.search(r"#\s*\d+\s+(?:van|of)\s+\d+", detail or "", re.I)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(0)).strip()


def _status_label(status: str) -> str:
    labels = {
        "sent": "sent",
        "submitted_unconfirmed": "submitted, confirmation pending",
        "confirmation_confirmed": "confirmed",
        "confirmation_missing": "confirmation missing",
        "confirmation_error": "confirmation error",
        "preapplication_confirmed": "pre-application confirmed",
        "preapplication_sent": "pre-application sent",
        "preapplication_confirmation_missing": "pre-application confirmation missing",
        "sent_preapplication_pending": "pre-application pending",
        "sent_preapplication_failed": "pre-application failed",
        "needs_verification": "manual verification required",
        "login_failed": "login failed",
        "error": "error",
    }
    return labels.get(status, status.replace("_", " "))


def _compact_detail(detail: str, limit: int = 900) -> str:
    compact = re.sub(r"\s+", " ", detail or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)].rstrip() + "..."


def _source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source.capitalize() if source else "Unknown")


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
    source = _source_label(listing.source)
    parts = [
        f"<b>{escape(listing.title)}</b>",
        f"Address: {escape(listing.address)}",
        f"Rent: {escape(listing.price)}",
    ]
    if listing.rooms:
        parts.append(f"Bedrooms/rooms: {escape(listing.rooms)}")
    if listing.size_m2:
        parts.append(f"Size: {escape(listing.size_m2)}")
    parts.append("")
    parts.append(f'<a href="{escape(listing.url)}">Open listing</a>')

    text = f"<b>New {escape(source)} listing</b>\n\n" + "\n".join(parts)

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
