from html import escape
import logging

from telegram import Bot
from telegram.constants import ParseMode

import db
from scrapers.funda import FundaScraper
from scrapers.huurwoningen import HuurwoningenScraper
from scrapers.kamernet import KamernetScraper
from scrapers.pararius import ParariusScraper
from scrapers.roofz import RoofzScraper

logger = logging.getLogger(__name__)


_FILTER_MATCH_KEYS = (
    "city",
    "max_price",
    "min_bedrooms",
    "min_size_m2",
    "kamernet_property_type",
)


async def run_scan_for_user(bot: Bot, user_filters: dict, require_active: bool = True) -> int:
    chat_id = user_filters["chat_id"]
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
