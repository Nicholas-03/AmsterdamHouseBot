import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from housebot.roofz_replier import RoofzReplier, RoofzReplySettings
from housebot.scrapers.base import Listing
from housebot.scrapers.roofz import RoofzScraper


async def main() -> int:
    parser = argparse.ArgumentParser(description="Test one Roofz reply flow.")
    parser.add_argument("--url", help="Roofz listing URL")
    parser.add_argument("--property-id", help="Roofz MarketSuite property id")
    parser.add_argument("--listing-id", help="Listing id/slug. Defaults to the id in --url.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Allow the final contact API POST. Also requires ROOFZ_REPLY_DRY_RUN=0.",
    )
    args = parser.parse_args()

    settings = replace(RoofzReplySettings.from_config(), enabled=True)
    if args.live and settings.dry_run:
        print("Refusing live send: set ROOFZ_REPLY_DRY_RUN=0 and pass --live.")
        return 2
    if not args.live:
        settings = replace(settings, dry_run=True)

    ready_error = settings.ready_error()
    if ready_error:
        print(f"Cannot run Roofz reply test: {ready_error}")
        return 2

    listing = await _resolve_listing(args)
    if not listing:
        print("Could not resolve a Roofz listing with property_id.")
        return 2

    async with RoofzReplier(settings) as replier:
        result = await replier.reply_to_listing(listing)

    print(f"listing_id={listing.id}")
    print(f"property_id={listing.reply_data.get('property_id', '')}")
    print(f"status={result.status}")
    if result.detail:
        print(f"detail={result.detail}")
    success_statuses = {
        "dry_run_ready",
        "sent",
        "sent_preapplication_pending",
        "preapplication_sent",
        "preapplication_confirmed",
    }
    return 0 if result.status in success_statuses else 1


async def _resolve_listing(args) -> Listing | None:
    if args.property_id:
        url = args.url or "https://www.roofz.eu/huur/woningen"
        listing_id = args.listing_id or url.rstrip("/").rsplit("/", 1)[-1]
        return Listing(
            id=listing_id,
            source=RoofzScraper.SOURCE,
            title=listing_id.replace("-", " ").title(),
            price="",
            address="",
            url=url,
            reply_data={"property_id": args.property_id},
        )

    scraper = RoofzScraper(city="Amsterdam", max_price=0, min_bedrooms=0, min_size_m2=0)
    listings = await scraper.scrape()
    if args.url:
        requested = args.url.rstrip("/")
        listings = [listing for listing in listings if listing.url.rstrip("/") == requested]
    for listing in listings:
        if listing.reply_data.get("property_id"):
            return listing
    return None


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
