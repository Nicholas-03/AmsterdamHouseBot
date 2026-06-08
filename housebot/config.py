import os
from datetime import date
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


def _derive_initials(first_name: str, last_name: str) -> str:
    tokens = [token for token in first_name.replace("-", " ").split() if token]
    if len(tokens) < 2:
        tokens.extend(token for token in last_name.replace("-", " ").split() if token)
    initials = [token[0].upper() for token in tokens[:2] if token]
    return ".".join(initials) + "." if initials else ""


def _derive_age(birth_date: str, today: date | None = None) -> str:
    raw = birth_date.strip()
    if not raw:
        return ""

    for separator in ("-", "/"):
        parts = raw.split(separator)
        if len(parts) != 3:
            continue
        try:
            if len(parts[0]) == 4:
                born = date(int(parts[0]), int(parts[1]), int(parts[2]))
            else:
                born = date(int(parts[2]), int(parts[1]), int(parts[0]))
        except ValueError:
            continue
        current = today or date.today()
        age = current.year - born.year - ((current.month, current.day) < (born.month, born.day))
        return str(age) if age >= 0 else ""

    return ""


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
FUNDA_FIRST_NAME = os.getenv("FUNDA_FIRST_NAME", os.getenv("ROOFZ_FIRST_NAME", "")).strip()
FUNDA_LAST_NAME = os.getenv("FUNDA_LAST_NAME", os.getenv("ROOFZ_LAST_NAME", "")).strip()
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

PARARIUS_AUTO_REPLY_ENABLED = _parse_bool(os.getenv("PARARIUS_AUTO_REPLY_ENABLED"), False)
PARARIUS_REPLY_DRY_RUN = _parse_bool(os.getenv("PARARIUS_REPLY_DRY_RUN"), KAMERNET_REPLY_DRY_RUN)
PARARIUS_EMAIL = _getenv_fallback("PARARIUS_EMAIL", FUNDA_EMAIL or HOUSING_EMAIL or KAMERNET_EMAIL).strip()
PARARIUS_PASSWORD = os.getenv("PARARIUS_PASSWORD", "")
PARARIUS_FIRST_NAME = os.getenv("PARARIUS_FIRST_NAME", os.getenv("ROOFZ_FIRST_NAME", FUNDA_FIRST_NAME)).strip()
PARARIUS_LAST_NAME = os.getenv("PARARIUS_LAST_NAME", os.getenv("ROOFZ_LAST_NAME", FUNDA_LAST_NAME)).strip()
PARARIUS_PHONE_NUMBER = _getenv_fallback("PARARIUS_PHONE_NUMBER", FUNDA_PHONE_NUMBER).strip()
PARARIUS_REPLY_MESSAGE = _load_reply_message("PARARIUS", FUNDA_REPLY_MESSAGE or KAMERNET_REPLY_MESSAGE)
PARARIUS_REPLY_MAX_PER_SCAN = _parse_non_negative_int(os.getenv("PARARIUS_REPLY_MAX_PER_SCAN"), 0)
PARARIUS_SALUTATION = os.getenv("PARARIUS_SALUTATION", "male").strip()
PARARIUS_DATE_OF_BIRTH = os.getenv("PARARIUS_DATE_OF_BIRTH", os.getenv("ROOFZ_BIRTH_DATE", KAMERNET_DATE_OF_BIRTH)).strip()
PARARIUS_WORK_SITUATION = os.getenv("PARARIUS_WORK_SITUATION", os.getenv("ROOFZ_WORK_SITUATION", "student")).strip()
PARARIUS_MONTHLY_INCOME = os.getenv("PARARIUS_MONTHLY_INCOME", os.getenv("ROOFZ_MONTHLY_INCOME", "")).strip()
PARARIUS_GUARANTOR = os.getenv("PARARIUS_GUARANTOR", "abroad").strip()
PARARIUS_PREFERRED_LIVING_SITUATION = os.getenv("PARARIUS_PREFERRED_LIVING_SITUATION", "alone").strip()
PARARIUS_NUMBER_OF_TENANTS = os.getenv("PARARIUS_NUMBER_OF_TENANTS", os.getenv("ROOFZ_PEOPLE_MOVING", "1")).strip()
PARARIUS_PETS = os.getenv("PARARIUS_PETS", os.getenv("ROOFZ_PETS", "no")).strip()
PARARIUS_RENT_START_DATE = os.getenv(
    "PARARIUS_RENT_START_DATE",
    os.getenv("ROOFZ_EXPECTED_MOVE_DATE", "2026-07-01"),
).strip()
PARARIUS_PREFERRED_CONTRACT_PERIOD = os.getenv("PARARIUS_PREFERRED_CONTRACT_PERIOD", "1-2 years").strip()
PARARIUS_CURRENT_HOUSING_SITUATION = os.getenv("PARARIUS_CURRENT_HOUSING_SITUATION", "renting").strip()
PARARIUS_BROWSER_HEADLESS = _parse_bool(os.getenv("PARARIUS_BROWSER_HEADLESS"), True)
PARARIUS_BROWSER_TIMEOUT_SECONDS = _parse_non_negative_int(
    os.getenv("PARARIUS_BROWSER_TIMEOUT_SECONDS"),
    45,
)
PARARIUS_STORAGE_STATE_PATH = os.getenv(
    "PARARIUS_STORAGE_STATE_PATH",
    str(Path(DB_PATH).with_name("pararius_storage_state.json")),
)
PARARIUS_BROWSER_FALLBACK_ENABLED = _parse_bool(os.getenv("PARARIUS_BROWSER_FALLBACK_ENABLED"), True)

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
ROOFZ_PREAPPLICATION_INITIAL_CONTACT_RETRIES = _parse_non_negative_int(
    os.getenv("ROOFZ_PREAPPLICATION_INITIAL_CONTACT_RETRIES"),
    1,
)
ROOFZ_PREAPPLICATION_RETRY_POLL_SECONDS = _parse_non_negative_int(
    os.getenv("ROOFZ_PREAPPLICATION_RETRY_POLL_SECONDS"),
    180,
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
ROOFZ_COMPLETE_APPLICATION_AUTO_ENABLED = _parse_bool(
    os.getenv("ROOFZ_COMPLETE_APPLICATION_AUTO_ENABLED"),
    False,
)
ROOFZ_COMPLETE_APPLICATION_DRY_RUN = _parse_bool(
    os.getenv("ROOFZ_COMPLETE_APPLICATION_DRY_RUN"),
    ROOFZ_REPLY_DRY_RUN,
)
ROOFZ_COMPLETE_APPLICATION_API_ENABLED = _parse_bool(
    os.getenv("ROOFZ_COMPLETE_APPLICATION_API_ENABLED"),
    True,
)
ROOFZ_COMPLETE_APPLICATION_BROWSER_FALLBACK_ENABLED = _parse_bool(
    os.getenv("ROOFZ_COMPLETE_APPLICATION_BROWSER_FALLBACK_ENABLED"),
    True,
)
ROOFZ_COMPLETE_APPLICATION_FINALIZE_POLL_SECONDS = _parse_non_negative_int(
    os.getenv("ROOFZ_COMPLETE_APPLICATION_FINALIZE_POLL_SECONDS"),
    20,
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
ROOFZ_OSRE_EMAIL = _getenv_fallback("ROOFZ_OSRE_EMAIL", ROOFZ_EMAIL).strip()
ROOFZ_OSRE_PASSWORD = _getenv_fallback("ROOFZ_OSRE_PASSWORD", KAMERNET_PASSWORD).strip()
ROOFZ_OSRE_ACCOUNT_ID = os.getenv("ROOFZ_OSRE_ACCOUNT_ID", "4def8fb7-ab71-49e4-b3da-77ece7f4c236").strip()
ROOFZ_OSRE_LOGIN_URL = os.getenv(
    "ROOFZ_OSRE_LOGIN_URL",
    "https://entree.portal.prd.osre.eu/login",
).strip()
ROOFZ_OSRE_API_BASE = os.getenv(
    "ROOFZ_OSRE_API_BASE",
    "https://relet.portal.prd.osre.eu",
).rstrip("/")
ROOFZ_INITIALS = os.getenv("ROOFZ_INITIALS", _derive_initials(ROOFZ_FIRST_NAME, ROOFZ_LAST_NAME)).strip()
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
ROOFZ_AGE = os.getenv("ROOFZ_AGE", _derive_age(ROOFZ_BIRTH_DATE)).strip()
ROOFZ_OCCUPATION = os.getenv("ROOFZ_OCCUPATION", "Student").strip()
ROOFZ_LANGUAGES = os.getenv("ROOFZ_LANGUAGES", "English").strip()
ROOFZ_PETS = os.getenv("ROOFZ_PETS", "No").strip()
ROOFZ_PEOPLE_MOVING = os.getenv("ROOFZ_PEOPLE_MOVING", "1").strip()
ROOFZ_COMPLETE_LIVING_SITUATION = os.getenv("ROOFZ_COMPLETE_LIVING_SITUATION", "Renting").strip()
ROOFZ_COMPLETE_HOUSEHOLD_SITUATION = os.getenv("ROOFZ_COMPLETE_HOUSEHOLD_SITUATION", "Alone").strip()
ROOFZ_COMPLETE_FAMILY_COMPOSITION = os.getenv(
    "ROOFZ_COMPLETE_FAMILY_COMPOSITION",
    "Single without children",
).strip()
ROOFZ_COMPLETE_MARITAL_STATE = os.getenv("ROOFZ_COMPLETE_MARITAL_STATE", "Single").strip()
ROOFZ_COMPLETE_ID_DOCUMENT_TYPE = os.getenv("ROOFZ_COMPLETE_ID_DOCUMENT_TYPE", "Identity card").strip()
ROOFZ_COMPLETE_ID_DOCUMENT_NUMBER = os.getenv("ROOFZ_COMPLETE_ID_DOCUMENT_NUMBER", "").strip()
ROOFZ_COMPLETE_ID_ISSUE_DATE = os.getenv("ROOFZ_COMPLETE_ID_ISSUE_DATE", "").strip()
ROOFZ_COMPLETE_ID_EXPIRATION_DATE = os.getenv("ROOFZ_COMPLETE_ID_EXPIRATION_DATE", "").strip()
ROOFZ_COMPLETE_ID_ISSUE_COUNTRY = os.getenv("ROOFZ_COMPLETE_ID_ISSUE_COUNTRY", "Italy").strip()
ROOFZ_COMPLETE_CITY_OF_BIRTH = os.getenv("ROOFZ_COMPLETE_CITY_OF_BIRTH", "").strip()
ROOFZ_COMPLETE_ADDRESS_COUNTRY = os.getenv("ROOFZ_COMPLETE_ADDRESS_COUNTRY", "Netherlands").strip()
ROOFZ_COMPLETE_STREET = os.getenv("ROOFZ_COMPLETE_STREET", "").strip()
ROOFZ_COMPLETE_HOUSE_NUMBER = os.getenv("ROOFZ_COMPLETE_HOUSE_NUMBER", "").strip()
ROOFZ_COMPLETE_HOUSE_NUMBER_EXTENSION = os.getenv("ROOFZ_COMPLETE_HOUSE_NUMBER_EXTENSION", "").strip()
ROOFZ_COMPLETE_POSTAL_CODE = os.getenv("ROOFZ_COMPLETE_POSTAL_CODE", "").strip()
ROOFZ_COMPLETE_CITY = os.getenv("ROOFZ_COMPLETE_CITY", "Amsterdam").strip()
ROOFZ_COMPLETE_FINANCIAL_OBLIGATIONS = os.getenv("ROOFZ_COMPLETE_FINANCIAL_OBLIGATIONS", "0").strip()
ROOFZ_COMPLETE_BANK_ACCOUNT = os.getenv("ROOFZ_COMPLETE_BANK_ACCOUNT", "").replace(" ", "").strip()
ROOFZ_COMPLETE_APPLICATION_COMMENT = os.getenv("ROOFZ_COMPLETE_APPLICATION_COMMENT", ROOFZ_REPLY_MESSAGE).replace("\\n", "\n").strip()
ROOFZ_COMPLETE_ID_DOCUMENT_PATH = os.getenv("ROOFZ_COMPLETE_ID_DOCUMENT_PATH", "").strip()
ROOFZ_COMPLETE_EDUCATIONAL_REGISTRATION_PATH = os.getenv(
    "ROOFZ_COMPLETE_EDUCATIONAL_REGISTRATION_PATH",
    "",
).strip()
ROOFZ_COMPLETE_SALARY_SLIP_PATHS = _parse_csv(os.getenv("ROOFZ_COMPLETE_SALARY_SLIP_PATHS"))
ROOFZ_COMPLETE_BANK_STATEMENT_PATHS = _parse_csv(os.getenv("ROOFZ_COMPLETE_BANK_STATEMENT_PATHS"))
ROOFZ_COMPLETE_DEED_OF_GUARANTEE_PATH = os.getenv("ROOFZ_COMPLETE_DEED_OF_GUARANTEE_PATH", "").strip()

if not TELEGRAM_TOKEN:
    sys.exit("ERRORE: TELEGRAM_TOKEN non trovato. Copia .env.example in .env e inserisci il token.")
