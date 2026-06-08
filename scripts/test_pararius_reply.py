import argparse
import asyncio
from dataclasses import replace
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from housebot.pararius_replier import ParariusReplier, ParariusReplySettings
from housebot.scrapers.base import Listing


async def main() -> int:
    parser = argparse.ArgumentParser(description="Test Pararius auto-reply for one listing.")
    parser.add_argument("url", help="Pararius listing URL")
    parser.add_argument("--title", default="Pararius test listing")
    parser.add_argument("--live", action="store_true", help="Submit the contact form instead of dry-run.")
    args = parser.parse_args()

    settings = replace(ParariusReplySettings.from_config(), enabled=True, dry_run=not args.live)
    ready_error = settings.ready_error()
    if ready_error:
        print(f"Pararius settings are not ready: {ready_error}")
        return 1

    listing = Listing(
        id=args.url.rstrip("/").split("/")[-1] or "pararius-test",
        source="pararius",
        title=args.title,
        price="",
        address="",
        url=args.url,
    )

    async with ParariusReplier(settings) as replier:
        result = await replier.reply_to_listing(listing)

    print(f"status={result.status}")
    if result.detail:
        print(f"detail={result.detail}")
    return 0 if result.status in {"dry_run_ready", "sent", "submitted_unconfirmed"} else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
