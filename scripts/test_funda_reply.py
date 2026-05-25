import argparse
import asyncio
import re
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from funda_replier import FundaReplier, FundaReplySettings
from scrapers.base import Listing
from scrapers.funda import FundaScraper


async def main() -> int:
    parser = argparse.ArgumentParser(description="Test one Funda contact request.")
    parser.add_argument("--url", help="Funda listing URL")
    parser.add_argument("--global-id", help="Funda internal global listing id")
    parser.add_argument("--office-id", help="Funda broker/office id")
    parser.add_argument("--listing-id", help="Public Funda listing id. Defaults to the id in --url.")
    parser.add_argument("--title", help="Listing title/address used to match the confirmation email.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Allow the final contact API POST. Also requires FUNDA_REPLY_DRY_RUN=0.",
    )
    args = parser.parse_args()

    settings = replace(FundaReplySettings.from_config(), enabled=True)
    if args.live and settings.dry_run:
        print("Refusing live send: set FUNDA_REPLY_DRY_RUN=0 and pass --live.")
        return 2
    if not args.live:
        settings = replace(settings, dry_run=True)

    ready_error = settings.ready_error()
    if ready_error:
        print(f"Cannot run Funda reply test: {ready_error}")
        return 2

    listing = await _resolve_listing(args)
    if not listing:
        print("Could not resolve a Funda listing with global id and office id.")
        return 2

    async with FundaReplier(settings) as replier:
        result = await replier.reply_to_listing(listing)

    print(f"listing_id={listing.id}")
    print(f"global_id={listing.reply_data.get('global_id', '')}")
    print(f"office_id={listing.reply_data.get('office_id', '')}")
    print(f"status={result.status}")
    if result.detail:
        print(f"detail={result.detail}")
    return 0 if result.status in {"dry_run_ready", "sent", "confirmation_confirmed"} else 1


async def _resolve_listing(args) -> Listing | None:
    if args.global_id and args.office_id:
        listing_id = args.listing_id or _id_from_url(args.url) or args.global_id
        url = args.url or f"https://www.funda.nl/detail/huur/{listing_id}/"
        return Listing(
            id=listing_id,
            source=FundaScraper.SOURCE,
            title=args.title or "Funda reply test",
            price="",
            address="",
            url=url,
            contact_url=f"https://www.funda.nl/makelaar-contact/?listingId={args.global_id}",
            reply_data={"global_id": args.global_id, "office_id": args.office_id},
        )

    scraper = FundaScraper(city="Amsterdam", max_price=0, min_bedrooms=0, min_size_m2=0)
    listings = await scraper.scrape()
    for listing in listings:
        if listing.reply_data.get("global_id") and listing.reply_data.get("office_id"):
            return listing
    return None


def _id_from_url(url: str | None) -> str:
    if not url:
        return ""
    matches = re.findall(r"\d{7,9}", url)
    return matches[-1] if matches else ""


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
