import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from gmail_preapplication import (
    GmailPreApplicationSettings,
    build_gmail_service,
    find_unread_preapplication_messages,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="List unread Roofz pre-application emails via Gmail API.")
    parser.add_argument("--title", default="", help="Optional listing title to add to the Gmail search query.")
    args = parser.parse_args()

    settings = GmailPreApplicationSettings.from_config()
    ready_error = settings.ready_error()
    if ready_error:
        print(f"Cannot check Gmail: {ready_error}")
        return 2

    service = build_gmail_service(settings)
    messages = find_unread_preapplication_messages(service, settings, args.title)
    print(f"matching_unread_count={len(messages)}")
    for message in messages:
        print(f"message_id={message.message_id}")
        print(f"subject={message.subject}")
        print(f"links={len(message.links)}")
        for link in message.links:
            print(link)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
