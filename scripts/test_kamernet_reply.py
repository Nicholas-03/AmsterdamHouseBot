import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from housebot.kamernet_replier import KamernetReplier, KamernetReplySettings
from housebot.scrapers.base import Listing


async def main() -> int:
    parser = argparse.ArgumentParser(description="Test one Kamernet reply flow.")
    parser.add_argument("url", help="Kamernet listing URL")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Allow clicking the final send button. Also requires KAMERNET_REPLY_DRY_RUN=0.",
    )
    args = parser.parse_args()

    settings = replace(KamernetReplySettings.from_config(), enabled=True)
    if args.live and settings.dry_run:
        print("Refusing live send: set KAMERNET_REPLY_DRY_RUN=0 and pass --live.")
        return 2
    if not args.live:
        settings = replace(settings, dry_run=True)

    ready_error = settings.ready_error()
    if ready_error:
        print(f"Cannot run Kamernet reply test: {ready_error}")
        return 2

    listing_id = args.url.rstrip("/").rsplit("-", 1)[-1]
    listing = Listing(
        id=listing_id,
        source="kamernet",
        title="Kamernet reply test",
        price="",
        address="",
        url=args.url,
    )

    async with KamernetReplier(settings) as replier:
        result = await replier.reply_to_listing(listing)

    print(f"status={result.status}")
    if result.detail:
        print(f"detail={result.detail}")
    return 0 if result.status in {"dry_run_ready", "sent", "submitted_unconfirmed"} else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
