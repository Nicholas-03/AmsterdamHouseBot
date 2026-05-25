import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mailtm_preapplication import (
    MailTmClient,
    MailTmSettings,
    find_confirmation_messages,
    find_preapplication_messages,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="List Roofz emails from the configured mail.tm mailbox.")
    parser.add_argument("--title", default="", help="Optional listing title to match in subjects.")
    parser.add_argument("--include-seen", action="store_true", help="Include already-seen pre-application emails.")
    args = parser.parse_args()

    settings = MailTmSettings.from_config()
    ready_error = settings.ready_error()
    if ready_error:
        print(f"Cannot check mail.tm: {ready_error}")
        return 2

    with MailTmClient(settings) as client:
        preapplications = find_preapplication_messages(
            client,
            settings,
            listing_title=args.title,
            unread_only=not args.include_seen,
        )
        confirmations = find_confirmation_messages(client, settings, listing_title=args.title)

    print(f"preapplication_count={len(preapplications)}")
    for message in preapplications:
        print(f"message_id={message.message_id}")
        print(f"subject={message.subject}")
        print(f"links={len(message.links)}")

    print(f"confirmation_count={len(confirmations)}")
    for message in confirmations:
        print(f"message_id={message.message_id}")
        print(f"subject={message.subject}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
