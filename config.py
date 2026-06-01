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


def _parse_csv(raw_value: str | None, default: str = "") -> tuple[str, ...]:
    value = default if raw_value is None else raw_value
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _parse_csv_ints(raw_value: str | None, default: str = "") -> tuple[int, ...]:
    values: list[int] = []
    for item in _parse_csv(raw_value, default):
        try:
            values.append(int(item))
        except ValueError:
            sys.exit(f"ERRORE: valore intero non valido: {item}")
    return tuple(values)


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
SCRAPER_TIMEOUT_SECONDS = _parse_non_negative_int(os.getenv("SCRAPER_TIMEOUT_SECONDS"), 240)
LOCAL_TIMEZONE = os.getenv("LOCAL_TIMEZONE", "Europe/Amsterdam").strip() or "Europe/Amsterdam"

FAST_SCAN_ENABLED = _parse_bool(os.getenv("FAST_SCAN_ENABLED"), True)
FAST_SCAN_INTERVAL_SECONDS = _parse_non_negative_int(os.getenv("FAST_SCAN_INTERVAL_SECONDS"), 120)
FAST_SCAN_SOURCES = _parse_csv(os.getenv("FAST_SCAN_SOURCES"), "kamernet,funda,roofz")

HEALTH_ALERT_ENABLED = _parse_bool(os.getenv("HEALTH_ALERT_ENABLED"), True)
HEALTH_ALERT_STALE_SCAN_MINUTES = _parse_non_negative_int(
    os.getenv("HEALTH_ALERT_STALE_SCAN_MINUTES"),
    15,
)
HEALTH_ALERT_COOLDOWN_MINUTES = _parse_non_negative_int(
    os.getenv("HEALTH_ALERT_COOLDOWN_MINUTES"),
    30,
)

DAILY_SUMMARY_ENABLED = _parse_bool(os.getenv("DAILY_SUMMARY_ENABLED"), True)
DAILY_SUMMARY_HOUR = _parse_non_negative_int(os.getenv("DAILY_SUMMARY_HOUR"), 9)
DAILY_SUMMARY_MINUTE = _parse_non_negative_int(os.getenv("DAILY_SUMMARY_MINUTE"), 0)

SOURCE_FAILURE_COOLDOWN_THRESHOLD = _parse_non_negative_int(
    os.getenv("SOURCE_FAILURE_COOLDOWN_THRESHOLD"),
    2,
)
SOURCE_FAILURE_COOLDOWN_MINUTES = _parse_non_negative_int(
    os.getenv("SOURCE_FAILURE_COOLDOWN_MINUTES"),
    15,
)
ROOFZ_FAILURE_COOLDOWN_THRESHOLD = _parse_non_negative_int(
    os.getenv("ROOFZ_FAILURE_COOLDOWN_THRESHOLD"),
    5,
)
ROOFZ_FAILURE_COOLDOWN_MINUTES = _parse_non_negative_int(
    os.getenv("ROOFZ_FAILURE_COOLDOWN_MINUTES"),
    1,
)
AUTO_REPLY_QUEUE_WORKERS = max(
    1,
    _parse_non_negative_int(os.getenv("AUTO_REPLY_QUEUE_WORKERS"), 4),
)

PARARIUS_STUDENT_COMPATIBILITY_FILTER_ENABLED = _parse_bool(
    os.getenv("PARARIUS_STUDENT_COMPATIBILITY_FILTER_ENABLED"),
    True,
)
HUURWONINGEN_STUDENT_COMPATIBILITY_FILTER_ENABLED = _parse_bool(
    os.getenv("HUURWONINGEN_STUDENT_COMPATIBILITY_FILTER_ENABLED"),
    True,
)

KAMERNET_AUTO_REPLY_ENABLED = _parse_bool(os.getenv("KAMERNET_AUTO_REPLY_ENABLED"), False)
KAMERNET_REPLY_DRY_RUN = _parse_bool(os.getenv("KAMERNET_REPLY_DRY_RUN"), True)
KAMERNET_EMAIL = os.getenv("KAMERNET_EMAIL", "")
KAMERNET_PASSWORD = os.getenv("KAMERNET_PASSWORD", "")
KAMERNET_REPLY_MESSAGE = _load_reply_message("KAMERNET")
KAMERNET_REPLY_MAX_PER_SCAN = _parse_non_negative_int(os.getenv("KAMERNET_REPLY_MAX_PER_SCAN"), 0)
KAMERNET_EXPECTED_TENANCY_DURATION = os.getenv("KAMERNET_EXPECTED_TENANCY_DURATION", "1 year").strip()
KAMERNET_EXPECTED_MOVE_DATE = os.getenv("KAMERNET_EXPECTED_MOVE_DATE", "07/01/2026").strip()
KAMERNET_DATE_OF_BIRTH = os.getenv("KAMERNET_DATE_OF_BIRTH", os.getenv("ROOFZ_BIRTH_DATE", "")).strip()
KAMERNET_EXPECTED_TENANCY_DURATION_ID = _parse_non_negative_int(
    os.getenv("KAMERNET_EXPECTED_TENANCY_DURATION_ID"),
    0,
)
KAMERNET_GENDER_ID = _parse_non_negative_int(os.getenv("KAMERNET_GENDER_ID"), 1)
KAMERNET_STATUS_ID = _parse_non_negative_int(os.getenv("KAMERNET_STATUS_ID"), 2)
KAMERNET_LANGUAGES_SPOKEN_IDS = _parse_csv_ints(os.getenv("KAMERNET_LANGUAGES_SPOKEN_IDS"), "1,2,16")
KAMERNET_HAS_PET = _parse_bool(os.getenv("KAMERNET_HAS_PET"), False)
KAMERNET_PEOPLE_MOVING_IN = _parse_non_negative_int(os.getenv("KAMERNET_PEOPLE_MOVING_IN"), 1)
KAMERNET_TENANT_LANGUAGE_ID = _parse_non_negative_int(os.getenv("KAMERNET_TENANT_LANGUAGE_ID"), 2)
KAMERNET_BROWSER_HEADLESS = _parse_bool(os.getenv("KAMERNET_BROWSER_HEADLESS"), True)
KAMERNET_BROWSER_TIMEOUT_SECONDS = _parse_non_negative_int(
    os.getenv("KAMERNET_BROWSER_TIMEOUT_SECONDS"),
    45,
)
KAMERNET_API_REPLY_ENABLED = _parse_bool(os.getenv("KAMERNET_API_REPLY_ENABLED"), True)
KAMERNET_STORAGE_STATE_PATH = os.getenv(
    "KAMERNET_STORAGE_STATE_PATH",
    str(Path(DB_PATH).with_name("kamernet_storage_state.json")),
)

HOUSING_EMAIL = os.getenv("HOUSING_EMAIL", "").strip()
MAILBOX_PROVIDER = os.getenv("MAILBOX_PROVIDER", "mailtm").strip().casefold()
CLOUDFLARE_MAILBOX_API_BASE = os.getenv("CLOUDFLARE_MAILBOX_API_BASE", "").rstrip("/")
CLOUDFLARE_MAILBOX_API_TOKEN = os.getenv("CLOUDFLARE_MAILBOX_API_TOKEN", "")
CLOUDFLARE_MAILBOX_ADDRESS = os.getenv("CLOUDFLARE_MAILBOX_ADDRESS", HOUSING_EMAIL).strip()
FUNDA_MAILBOX_PROVIDER = os.getenv("FUNDA_MAILBOX_PROVIDER", MAILBOX_PROVIDER).strip().casefold()
ROOFZ_MAILBOX_PROVIDER = os.getenv("ROOFZ_MAILBOX_PROVIDER", MAILBOX_PROVIDER).strip().casefold()

FUNDA_AUTO_REPLY_ENABLED = _parse_bool(os.getenv("FUNDA_AUTO_REPLY_ENABLED"), False)
FUNDA_REPLY_DRY_RUN = _parse_bool(os.getenv("FUNDA_REPLY_DRY_RUN"), KAMERNET_REPLY_DRY_RUN)
FUNDA_EMAIL = _getenv_fallback("FUNDA_EMAIL", HOUSING_EMAIL or KAMERNET_EMAIL).strip()
FUNDA_FIRST_NAME = os.getenv("FUNDA_FIRST_NAME", "Nicholas").strip()
FUNDA_LAST_NAME = os.getenv("FUNDA_LAST_NAME", "Guido Boidi").strip()
FUNDA_PHONE_NUMBER = os.getenv("FUNDA_PHONE_NUMBER", "").strip()
FUNDA_REPLY_MESSAGE = _load_reply_message("FUNDA", KAMERNET_REPLY_MESSAGE)
FUNDA_REPLY_MAX_PER_SCAN = _parse_non_negative_int(os.getenv("FUNDA_REPLY_MAX_PER_SCAN"), 0)
FUNDA_VIEWING_REQUEST = _parse_bool(os.getenv("FUNDA_VIEWING_REQUEST"), True)
FUNDA_CONTACT_API_BASE = os.getenv("FUNDA_CONTACT_API_BASE", "https://contacts-bff.funda.io").rstrip("/")
FUNDA_KEYWORDS = _parse_csv(os.getenv("FUNDA_KEYWORDS"), "student")
FUNDA_BROWSER_TIMEOUT_SECONDS = _parse_non_negative_int(
    os.getenv("FUNDA_BROWSER_TIMEOUT_SECONDS"),
    45,
)

ROOFZ_AUTO_REPLY_ENABLED = _parse_bool(os.getenv("ROOFZ_AUTO_REPLY_ENABLED"), False)
ROOFZ_REPLY_DRY_RUN = _parse_bool(os.getenv("ROOFZ_REPLY_DRY_RUN"), KAMERNET_REPLY_DRY_RUN)
ROOFZ_MAILTM_API_BASE = os.getenv("ROOFZ_MAILTM_API_BASE", "https://api.mail.tm").rstrip("/")
ROOFZ_MAILTM_ADDRESS = os.getenv("ROOFZ_MAILTM_ADDRESS", "").strip()
ROOFZ_MAILTM_PASSWORD = os.getenv("ROOFZ_MAILTM_PASSWORD", "")

FUNDA_MAILTM_API_BASE = os.getenv("FUNDA_MAILTM_API_BASE", ROOFZ_MAILTM_API_BASE).rstrip("/")
FUNDA_MAILTM_ADDRESS = os.getenv("FUNDA_MAILTM_ADDRESS", ROOFZ_MAILTM_ADDRESS).strip()
FUNDA_MAILTM_PASSWORD = os.getenv("FUNDA_MAILTM_PASSWORD", ROOFZ_MAILTM_PASSWORD)
FUNDA_CONFIRMATION_ENABLED = _parse_bool(
    os.getenv("FUNDA_CONFIRMATION_ENABLED"),
    bool(
        (
            FUNDA_MAILBOX_PROVIDER == "cloudflare"
            and CLOUDFLARE_MAILBOX_API_BASE
            and CLOUDFLARE_MAILBOX_API_TOKEN
        )
        or (FUNDA_MAILTM_ADDRESS and FUNDA_MAILTM_PASSWORD)
    ),
)
FUNDA_CONFIRMATION_POLL_SECONDS = _parse_non_negative_int(
    os.getenv("FUNDA_CONFIRMATION_POLL_SECONDS"),
    180,
)
FUNDA_CONFIRMATION_POLL_INTERVAL_SECONDS = _parse_non_negative_int(
    os.getenv("FUNDA_CONFIRMATION_POLL_INTERVAL_SECONDS"),
    15,
)
FUNDA_MAILTM_FORWARDER_ADDRESS = os.getenv("FUNDA_MAILTM_FORWARDER_ADDRESS", FUNDA_EMAIL).strip()
FUNDA_MAILTM_CONFIRMATION_SENDER = os.getenv(
    "FUNDA_MAILTM_CONFIRMATION_SENDER",
    "notificaties@service.funda.nl",
).strip()
FUNDA_MAILTM_CONFIRMATION_SUBJECT_PATTERNS = tuple(
    item.strip()
    for item in os.getenv(
        "FUNDA_MAILTM_CONFIRMATION_SUBJECT_PATTERNS",
        "bevestiging,confirmation,confirmed",
    ).split(",")
    if item.strip()
)

ROOFZ_EMAIL = _getenv_fallback("ROOFZ_EMAIL", HOUSING_EMAIL or KAMERNET_EMAIL).strip()
ROOFZ_FIRST_NAME = os.getenv("ROOFZ_FIRST_NAME", FUNDA_FIRST_NAME).strip()
ROOFZ_LAST_NAME = os.getenv("ROOFZ_LAST_NAME", FUNDA_LAST_NAME).strip()
ROOFZ_PHONE_NUMBER = _getenv_fallback("ROOFZ_PHONE_NUMBER", FUNDA_PHONE_NUMBER).strip()
ROOFZ_REPLY_MESSAGE = _load_reply_message("ROOFZ", FUNDA_REPLY_MESSAGE or KAMERNET_REPLY_MESSAGE)
ROOFZ_REPLY_MAX_PER_SCAN = _parse_non_negative_int(os.getenv("ROOFZ_REPLY_MAX_PER_SCAN"), 0)
ROOFZ_CONTACT_API_URL = os.getenv(
    "ROOFZ_CONTACT_API_URL",
    "https://www.roofz.eu/api/ms/subscription/candidate",
).strip()
ROOFZ_BROWSER_HEADLESS = _parse_bool(os.getenv("ROOFZ_BROWSER_HEADLESS"), True)
ROOFZ_BROWSER_TIMEOUT_SECONDS = _parse_non_negative_int(
    os.getenv("ROOFZ_BROWSER_TIMEOUT_SECONDS"),
    45,
)
ROOFZ_PREAPPLICATION_ENABLED = _parse_bool(os.getenv("ROOFZ_PREAPPLICATION_ENABLED"), False)
ROOFZ_PREAPPLICATION_API_ENABLED = _parse_bool(os.getenv("ROOFZ_PREAPPLICATION_API_ENABLED"), True)
ROOFZ_PREAPPLICATION_POLL_SECONDS = _parse_non_negative_int(
    os.getenv("ROOFZ_PREAPPLICATION_POLL_SECONDS"),
    420,
)
ROOFZ_PREAPPLICATION_POLL_INTERVAL_SECONDS = _parse_non_negative_int(
    os.getenv("ROOFZ_PREAPPLICATION_POLL_INTERVAL_SECONDS"),
    15,
)
ROOFZ_PREAPPLICATION_MONITOR_ENABLED = _parse_bool(
    os.getenv("ROOFZ_PREAPPLICATION_MONITOR_ENABLED"),
    ROOFZ_PREAPPLICATION_ENABLED,
)
ROOFZ_PREAPPLICATION_MONITOR_INTERVAL_SECONDS = _parse_non_negative_int(
    os.getenv("ROOFZ_PREAPPLICATION_MONITOR_INTERVAL_SECONDS"),
    45,
)
ROOFZ_MAILTM_PREAPPLICATION_SENDER = os.getenv(
    "ROOFZ_MAILTM_PREAPPLICATION_SENDER",
    "living@rockfieldrealestate.com",
).strip()
ROOFZ_MAILTM_FORWARDER_ADDRESS = os.getenv("ROOFZ_MAILTM_FORWARDER_ADDRESS", ROOFZ_EMAIL).strip()
ROOFZ_MAILTM_PREAPPLICATION_SUBJECT_PREFIX = os.getenv(
    "ROOFZ_MAILTM_PREAPPLICATION_SUBJECT_PREFIX",
    "Start your pre-application",
).strip()
ROOFZ_MAILTM_CONFIRMATION_SENDER = os.getenv(
    "ROOFZ_MAILTM_CONFIRMATION_SENDER",
    ROOFZ_MAILTM_PREAPPLICATION_SENDER,
).strip()
ROOFZ_MAILTM_CONFIRMATION_SUBJECT_PATTERNS = tuple(
    item.strip()
    for item in os.getenv(
        "ROOFZ_MAILTM_CONFIRMATION_SUBJECT_PATTERNS",
        "confirmation,confirmed,received,bevestiging,ontvangen",
    ).split(",")
    if item.strip()
)
ROOFZ_COMPLETE_APPLICATION_MONITOR_ENABLED = _parse_bool(
    os.getenv("ROOFZ_COMPLETE_APPLICATION_MONITOR_ENABLED"),
    ROOFZ_PREAPPLICATION_ENABLED,
)
ROOFZ_COMPLETE_APPLICATION_MONITOR_INTERVAL_SECONDS = _parse_non_negative_int(
    os.getenv("ROOFZ_COMPLETE_APPLICATION_MONITOR_INTERVAL_SECONDS"),
    300,
)
ROOFZ_COMPLETE_APPLICATION_SUBJECT_PATTERNS = tuple(
    item.strip()
    for item in os.getenv(
        "ROOFZ_COMPLETE_APPLICATION_SUBJECT_PATTERNS",
        "Complete application",
    ).split(",")
    if item.strip()
)
ROOFZ_OSRE_PREAPPLICATION_API_URL = os.getenv(
    "ROOFZ_OSRE_PREAPPLICATION_API_URL",
    "https://relet.portal.prd.osre.eu/portal/applications/pre-application",
).strip()
ROOFZ_OSRE_AVAILABILITY_API_BASE = os.getenv(
    "ROOFZ_OSRE_AVAILABILITY_API_BASE",
    "https://financial-check.portal.prd.osre.eu/portal/financial-check/check-availability",
).rstrip("/")
ROOFZ_INITIALS = os.getenv("ROOFZ_INITIALS", "N.G.").strip()
ROOFZ_BIRTH_DATE = os.getenv("ROOFZ_BIRTH_DATE", "").strip()
ROOFZ_RENT_TOGETHER = _parse_bool(os.getenv("ROOFZ_RENT_TOGETHER"), False)
ROOFZ_CURRENT_LIVING_SITUATION = os.getenv("ROOFZ_CURRENT_LIVING_SITUATION", "Single without children").strip()
ROOFZ_WORK_SITUATION = os.getenv("ROOFZ_WORK_SITUATION", "Student").strip()
ROOFZ_MONTHLY_INCOME = os.getenv("ROOFZ_MONTHLY_INCOME", "").strip()
ROOFZ_ANNUAL_INCOME = os.getenv("ROOFZ_ANNUAL_INCOME", "").strip()
ROOFZ_SAVINGS = os.getenv("ROOFZ_SAVINGS", "").strip()
ROOFZ_BANK_NAME = os.getenv("ROOFZ_BANK_NAME", "").strip()
ROOFZ_EXPECTED_STAY_DURATION = os.getenv("ROOFZ_EXPECTED_STAY_DURATION", "1 year").strip()
ROOFZ_EXPECTED_MOVE_DATE = os.getenv("ROOFZ_EXPECTED_MOVE_DATE", "01/07/2026").strip()
ROOFZ_GENDER = os.getenv("ROOFZ_GENDER", "Male").strip()
ROOFZ_AGE = os.getenv("ROOFZ_AGE", "23").strip()
ROOFZ_OCCUPATION = os.getenv("ROOFZ_OCCUPATION", "Working student").strip()
ROOFZ_LANGUAGES = os.getenv("ROOFZ_LANGUAGES", "Dutch, English, Italian").strip()
ROOFZ_PETS = os.getenv("ROOFZ_PETS", "No").strip()
ROOFZ_PEOPLE_MOVING = os.getenv("ROOFZ_PEOPLE_MOVING", "1").strip()

if not TELEGRAM_TOKEN:
    sys.exit("ERRORE: TELEGRAM_TOKEN non trovato. Copia .env.example in .env e inserisci il token.")
