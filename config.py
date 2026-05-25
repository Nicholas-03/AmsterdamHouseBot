import os
from pathlib import Path
import sys
from dotenv import load_dotenv

load_dotenv()


def _parse_chat_ids(raw_value: str) -> set[int]:
    chat_ids: set[int] = set()
    for item in raw_value.replace(",", " ").split():
        try:
            chat_ids.add(int(item))
        except ValueError:
            sys.exit(f"ERRORE: TELEGRAM_ALLOWED_CHAT_IDS contiene un chat ID non valido: {item}")
    return chat_ids


def _parse_bool(raw_value: str | None, default: bool = False) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().casefold() in {"1", "true", "yes", "y", "on"}


def _parse_non_negative_int(raw_value: str | None, default: int) -> int:
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError:
        sys.exit(f"ERRORE: valore intero non valido: {raw_value}")
    if value < 0:
        sys.exit(f"ERRORE: valore intero negativo non valido: {raw_value}")
    return value


def _load_reply_message(prefix: str, fallback: str = "") -> str:
    message_file = os.getenv(f"{prefix}_REPLY_MESSAGE_FILE", "").strip()
    if message_file:
        path = Path(message_file).expanduser()
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            sys.exit(f"ERRORE: impossibile leggere {prefix}_REPLY_MESSAGE_FILE: {exc}")

    value = os.getenv(f"{prefix}_REPLY_MESSAGE")
    if value is None:
        value = fallback
    return value.replace("\\n", "\n").strip()


def _getenv_fallback(name: str, fallback: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return fallback
    return value


TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
DB_PATH = os.getenv("DB_PATH", "listings.db")
TELEGRAM_ALLOWED_CHAT_IDS = _parse_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", ""))

KAMERNET_AUTO_REPLY_ENABLED = _parse_bool(os.getenv("KAMERNET_AUTO_REPLY_ENABLED"), False)
KAMERNET_REPLY_DRY_RUN = _parse_bool(os.getenv("KAMERNET_REPLY_DRY_RUN"), True)
KAMERNET_EMAIL = os.getenv("KAMERNET_EMAIL", "")
KAMERNET_PASSWORD = os.getenv("KAMERNET_PASSWORD", "")
KAMERNET_REPLY_MESSAGE = _load_reply_message("KAMERNET")
KAMERNET_REPLY_MAX_PER_SCAN = _parse_non_negative_int(os.getenv("KAMERNET_REPLY_MAX_PER_SCAN"), 0)
KAMERNET_EXPECTED_TENANCY_DURATION = os.getenv("KAMERNET_EXPECTED_TENANCY_DURATION", "1 year").strip()
KAMERNET_EXPECTED_MOVE_DATE = os.getenv("KAMERNET_EXPECTED_MOVE_DATE", "07/01/2026").strip()
KAMERNET_BROWSER_HEADLESS = _parse_bool(os.getenv("KAMERNET_BROWSER_HEADLESS"), True)
KAMERNET_BROWSER_TIMEOUT_SECONDS = _parse_non_negative_int(
    os.getenv("KAMERNET_BROWSER_TIMEOUT_SECONDS"),
    45,
)
KAMERNET_STORAGE_STATE_PATH = os.getenv(
    "KAMERNET_STORAGE_STATE_PATH",
    str(Path(DB_PATH).with_name("kamernet_storage_state.json")),
)

FUNDA_AUTO_REPLY_ENABLED = _parse_bool(os.getenv("FUNDA_AUTO_REPLY_ENABLED"), False)
FUNDA_REPLY_DRY_RUN = _parse_bool(os.getenv("FUNDA_REPLY_DRY_RUN"), KAMERNET_REPLY_DRY_RUN)
FUNDA_EMAIL = _getenv_fallback("FUNDA_EMAIL", KAMERNET_EMAIL).strip()
FUNDA_FIRST_NAME = os.getenv("FUNDA_FIRST_NAME", "Nicholas").strip()
FUNDA_LAST_NAME = os.getenv("FUNDA_LAST_NAME", "Guido Boidi").strip()
FUNDA_PHONE_NUMBER = os.getenv("FUNDA_PHONE_NUMBER", "").strip()
FUNDA_REPLY_MESSAGE = _load_reply_message("FUNDA", KAMERNET_REPLY_MESSAGE)
FUNDA_REPLY_MAX_PER_SCAN = _parse_non_negative_int(os.getenv("FUNDA_REPLY_MAX_PER_SCAN"), 0)
FUNDA_VIEWING_REQUEST = _parse_bool(os.getenv("FUNDA_VIEWING_REQUEST"), True)
FUNDA_CONTACT_API_BASE = os.getenv("FUNDA_CONTACT_API_BASE", "https://contacts-bff.funda.io").rstrip("/")
FUNDA_BROWSER_TIMEOUT_SECONDS = _parse_non_negative_int(
    os.getenv("FUNDA_BROWSER_TIMEOUT_SECONDS"),
    45,
)

if not TELEGRAM_TOKEN:
    sys.exit("ERRORE: TELEGRAM_TOKEN non trovato. Copia .env.example in .env e inserisci il token.")
