import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from funda_replier import FundaConfirmationSettings
from mailtm_preapplication import MailTmClient, find_mailtm_messages


def main() -> int:
    parser = argparse.ArgumentParser(description="List Funda confirmation emails from the configured mail.tm mailbox.")
    parser.add_argument("--title", default="", help="Optional listing title to match in subjects.")
    args = parser.parse_args()

    settings = FundaConfirmationSettings.from_config()
    ready_error = settings.ready_error()
    if ready_error:
        print(f"Cannot check Funda confirmations: {ready_error}")
        return 2

    with MailTmClient(settings.mailtm) as client:
        messages = find_mailtm_messages(
            client,
            settings.senders,
            settings.subject_patterns,
            listing_title=args.title,
        )

    print(f"confirmation_count={len(messages)}")
    for message in messages:
        print(f"message_id={message.message_id}")
        print(f"subject={message.subject}")
        print(f"sender={message.sender}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
