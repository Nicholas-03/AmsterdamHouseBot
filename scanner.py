from html import escape
import logging

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

_AUTO_REPLY_WARNING_STATUSES = {
    "sent_preapplication_pending",
    "sent_preapplication_failed",
    "preapplication_confirmation_missing",
    "preapplication_validation_failed",
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
    kamernet_reply_settings = KamernetReplySettings.from_config()
    kamernet_reply_settings_error = kamernet_reply_settings.ready_error()
    funda_reply_settings = FundaReplySettings.from_config()
    funda_reply_settings_error = funda_reply_settings.ready_error()
    roofz_reply_settings = RoofzReplySettings.from_config()
    roofz_reply_settings_error = roofz_reply_settings.ready_error()
    if user_filters.get("auto_reply_enabled"):
        if kamernet_reply_settings.enabled and kamernet_reply_settings_error:
            logger.warning("Kamernet auto-reply is unavailable for this scan: %s", kamernet_reply_settings_error)
        if funda_reply_settings.enabled and funda_reply_settings_error:
            logger.warning("Funda auto-reply is unavailable for this scan: %s", funda_reply_settings_error)
        if roofz_reply_settings.enabled and roofz_reply_settings_error:
            logger.warning("Roofz auto-reply is unavailable for this scan: %s", roofz_reply_settings_error)

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
                return new_count

            listings = await scraper.scrape()
            new_from_scraper = 0
            for listing in listings:
                if not await _scan_is_current(chat_id, user_filters, require_active):
                    logger.info("Scan stopped for user %s before sending stale results.", chat_id)
                    return new_count

                if await db.was_sent(chat_id, listing.source, listing.id):
                    continue
                await db.mark_seen(listing.source, listing.id, listing.url, listing.title, listing.price)
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
                        )
                        if attempted:
                            reply_attempts[RoofzScraper.SOURCE] += 1
                new_count += 1
                new_from_scraper += 1
            logger.info(
                "%s: %d listings found, %d new for user %s",
                scraper.SOURCE,
                len(listings),
                new_from_scraper,
                chat_id,
            )
        except Exception as exc:
            logger.error("Scraper %s failed for user %s: %s", scraper.SOURCE, chat_id, exc)

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
) -> bool:
    if not settings.enabled:
        return False
    if settings_error:
        return False
    if settings.max_per_scan and attempts_so_far >= settings.max_per_scan:
        logger.info(
            "%s auto-reply cap reached for this scan: %d/%d",
            source_label,
            attempts_so_far,
            settings.max_per_scan,
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
        return False

    await db.mark_auto_reply_result(
        listing.source,
        listing.id,
        listing.url,
        chat_id,
        "attempting",
        settings.dry_run,
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
    )
    logger.info(
        "%s auto-reply result for %s: %s (%s)",
        source_label,
        listing.id,
        result.status,
        result.detail,
    )
    if bot and result.status in _AUTO_REPLY_WARNING_STATUSES:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"Warning: {source_label} auto-reply needs attention for {listing.title}.\n"
                f"Status: {result.status}\n"
                f"Detail: {result.detail}"
            ),
            disable_web_page_preview=True,
        )
    return True


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
            return
        except Exception as exc:
            logger.warning("Photo send failed (%s), retrying as text: %s", chat_id, exc)

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )
    except Exception as exc:
        logger.error("Notification failed for %s: %s", chat_id, exc)
        raise
