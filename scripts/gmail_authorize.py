import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from gmail_preapplication import GmailPreApplicationSettings, authorize_gmail


def main() -> int:
    settings = GmailPreApplicationSettings.from_config()
    if not settings.credentials_path.exists():
        print(
            "Missing Gmail OAuth client file.\n"
            f"Expected: {settings.credentials_path}\n"
            "Create an OAuth desktop client for the Gmail API and save it there."
        )
        return 2

    authorize_gmail(settings)
    print(f"Gmail token saved to {settings.token_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
