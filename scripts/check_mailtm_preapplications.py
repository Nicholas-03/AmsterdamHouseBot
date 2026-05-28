import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from cloudflare_mailbox import CloudflareMailboxClient
from mailtm_preapplication import (
    MailTmClient,
    find_confirmation_messages,
    find_preapplication_messages,
)
from roofz_replier import RoofzReplySettings


def main() -> int:
    parser = argparse.ArgumentParser(description="List Roofz emails from the configured mailbox.")
    parser.add_argument("--title", default="", help="Optional listing title to match in subjects.")
    parser.add_argument("--include-seen", action="store_true", help="Include already-seen pre-application emails.")
    args = parser.parse_args()

    settings = RoofzReplySettings.from_config()
    if settings.mailbox_provider not in {"cloudflare", "mailtm"}:
        print(f"Cannot check mailbox: unsupported ROOFZ_MAILBOX_PROVIDER {settings.mailbox_provider}")
        return 2

    mailbox_settings = settings.cloudflare_mailbox if settings.mailbox_provider == "cloudflare" else settings.mailtm
    ready_error = mailbox_settings.ready_error()
    if ready_error:
        print(f"Cannot check mailbox: {ready_error}")
        return 2

    client_factory = CloudflareMailboxClient if settings.mailbox_provider == "cloudflare" else MailTmClient
    with client_factory(mailbox_settings) as client:
        preapplications = find_preapplication_messages(
            client,
            mailbox_settings,
            listing_title=args.title,
            unread_only=not args.include_seen,
        )
        confirmations = find_confirmation_messages(client, mailbox_settings, listing_title=args.title)

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
