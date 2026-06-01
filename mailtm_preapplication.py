from __future__ import annotations

from html import unescape
import quopri
import re
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning


@dataclass(frozen=True)
class MailTmAuthSettings:
    api_base: str
    address: str
    password: str

    def ready_error(self, prefix: str = "MAILTM") -> str | None:
        if not self.api_base:
            return f"{prefix}_API_BASE is missing."
        if not self.address:
            return f"{prefix}_ADDRESS is missing."
        if not self.password:
            return f"{prefix}_PASSWORD is missing."
        return None


@dataclass(frozen=True)
class MailTmSettings(MailTmAuthSettings):
    preapplication_sender: str
    forwarder_address: str
    preapplication_subject_prefix: str
    confirmation_sender: str
    confirmation_subject_patterns: tuple[str, ...]
    max_results: int = 30

    @classmethod
    def from_config(cls) -> MailTmSettings:
        import config

        return cls(
            api_base=config.ROOFZ_MAILTM_API_BASE,
            address=config.ROOFZ_MAILTM_ADDRESS,
            password=config.ROOFZ_MAILTM_PASSWORD,
            preapplication_sender=config.ROOFZ_MAILTM_PREAPPLICATION_SENDER,
            forwarder_address=config.ROOFZ_MAILTM_FORWARDER_ADDRESS,
            preapplication_subject_prefix=config.ROOFZ_MAILTM_PREAPPLICATION_SUBJECT_PREFIX,
            confirmation_sender=config.ROOFZ_MAILTM_CONFIRMATION_SENDER,
            confirmation_subject_patterns=tuple(config.ROOFZ_MAILTM_CONFIRMATION_SUBJECT_PATTERNS),
        )

    def ready_error(self) -> str | None:
        auth_error = super().ready_error("ROOFZ_MAILTM")
        if auth_error:
            return auth_error
        if not self.preapplication_sender and not self.forwarder_address:
            return "ROOFZ_MAILTM_PREAPPLICATION_SENDER or ROOFZ_MAILTM_FORWARDER_ADDRESS is missing."
        if not self.preapplication_subject_prefix:
            return "ROOFZ_MAILTM_PREAPPLICATION_SUBJECT_PREFIX is missing."
        if not self.confirmation_sender:
            return "ROOFZ_MAILTM_CONFIRMATION_SENDER is missing."
        if not self.confirmation_subject_patterns:
            return "ROOFZ_MAILTM_CONFIRMATION_SUBJECT_PATTERNS is missing."
        return None


@dataclass(frozen=True)
class MailTmMessage:
    message_id: str
    subject: str
    sender: str
    created_at: datetime | None
    seen: bool
    links: list[str]


class MailTmClient:
    def __init__(self, settings: MailTmAuthSettings):
        self.settings = settings
        self._client = httpx.Client(timeout=30)
        self._token = ""

    def __enter__(self) -> MailTmClient:
        self.authenticate()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._client.close()

    def authenticate(self) -> None:
        response = self._client.post(
            f"{self.settings.api_base}/token",
            json={"address": self.settings.address, "password": self.settings.password},
        )
        response.raise_for_status()
        self._token = response.json()["token"]

    def list_messages(self) -> list[dict]:
        response = self._client.get(
            f"{self.settings.api_base}/messages",
            headers=self._headers(),
            params={"page": 1},
        )
        response.raise_for_status()
        return response.json().get("hydra:member", [])

    def get_message(self, message_id: str) -> dict:
        response = self._client.get(
            f"{self.settings.api_base}/messages/{message_id}",
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}


def find_preapplication_messages(
    client: MailTmClient,
    settings: MailTmSettings,
    listing_title: str = "",
    since: datetime | None = None,
    unread_only: bool = True,
) -> list[MailTmMessage]:
    return _find_messages(
        client,
        senders=mailtm_senders(settings.preapplication_sender, settings.forwarder_address),
        subject_patterns=(settings.preapplication_subject_prefix,),
        listing_title=listing_title,
        since=since,
        unread_only=unread_only,
        require_links=True,
    )


def find_confirmation_messages(
    client: MailTmClient,
    settings: MailTmSettings,
    listing_title: str = "",
    since: datetime | None = None,
) -> list[MailTmMessage]:
    messages = _find_messages(
        client,
        senders=mailtm_senders(settings.confirmation_sender, settings.forwarder_address),
        subject_patterns=settings.confirmation_subject_patterns,
        listing_title=listing_title,
        since=since,
        unread_only=False,
        require_links=False,
    )
    preapplication_prefix = settings.preapplication_subject_prefix.casefold()
    return [
        message
        for message in messages
        if preapplication_prefix not in message.subject.casefold()
    ]


def find_complete_application_messages(
    client: MailTmClient,
    settings: MailTmSettings,
    subject_patterns: tuple[str, ...] = ("Complete application",),
    since: datetime | None = None,
    unread_only: bool = False,
) -> list[MailTmMessage]:
    senders = mailtm_senders(
        getattr(settings, "confirmation_sender", ""),
        getattr(settings, "preapplication_sender", ""),
        getattr(settings, "forwarder_address", ""),
    )
    found: list[MailTmMessage] = []
    for summary in client.list_messages():
        subject = summary.get("subject") or ""
        sender_address = (summary.get("from") or {}).get("address") or ""
        seen = bool(summary.get("seen"))
        created_at = _parse_datetime(summary.get("createdAt"))
        if unread_only and seen:
            continue
        if since and created_at and created_at < since:
            continue
        if senders and not any(sender.casefold() in sender_address.casefold() for sender in senders):
            continue
        if subject_patterns and not any(pattern.casefold() in subject.casefold() for pattern in subject_patterns):
            continue

        full = client.get_message(summary["id"])
        links = extract_complete_application_links(full)
        found.append(
            MailTmMessage(
                message_id=summary["id"],
                subject=subject,
                sender=sender_address,
                created_at=created_at,
                seen=seen,
                links=links,
            )
        )
    return found


def find_mailtm_messages(
    client: MailTmClient,
    senders: tuple[str, ...],
    subject_patterns: tuple[str, ...],
    listing_title: str = "",
    since: datetime | None = None,
    unread_only: bool = False,
    require_links: bool = False,
    exclude_subject_patterns: tuple[str, ...] = (),
) -> list[MailTmMessage]:
    messages = _find_messages(
        client,
        senders=senders,
        subject_patterns=subject_patterns,
        listing_title=listing_title,
        since=since,
        unread_only=unread_only,
        require_links=require_links,
    )
    if not exclude_subject_patterns:
        return messages
    return [
        message
        for message in messages
        if not any(pattern.casefold() in message.subject.casefold() for pattern in exclude_subject_patterns)
    ]


def extract_preapplication_links(message: dict) -> list[str]:
    links: list[str] = []
    for body in _message_bodies(message):
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)
            soup = BeautifulSoup(body, "html.parser")
        for anchor in soup.find_all("a", href=True):
            text = " ".join(anchor.get_text(" ", strip=True).split())
            href = _clean_link(anchor["href"])
            if href and _looks_like_preapplication_link(text, href):
                links.append(href)
        for href in re.findall(r"https?://[^\s<>\"]+", body):
            href = _clean_link(href)
            if href and _looks_like_preapplication_link("", href):
                links.append(href)
    return list(dict.fromkeys(links))


def extract_complete_application_links(message: dict) -> list[str]:
    links: list[str] = []
    for body in _message_bodies(message):
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)
            soup = BeautifulSoup(body, "html.parser")
        for anchor in soup.find_all("a", href=True):
            text = " ".join(anchor.get_text(" ", strip=True).split())
            href = _clean_link(anchor["href"])
            if href and _looks_like_complete_application_link(text, href):
                links.append(href)
        for href in re.findall(r"https?://[^\s<>\"]+", body):
            href = _clean_link(href)
            if href and _looks_like_complete_application_link("", href):
                links.append(href)
    return list(dict.fromkeys(links))


def _find_messages(
    client: MailTmClient,
    senders: tuple[str, ...],
    subject_patterns: tuple[str, ...],
    listing_title: str,
    since: datetime | None,
    unread_only: bool,
    require_links: bool,
) -> list[MailTmMessage]:
    found: list[MailTmMessage] = []
    for summary in client.list_messages():
        subject = summary.get("subject") or ""
        sender_address = (summary.get("from") or {}).get("address") or ""
        seen = bool(summary.get("seen"))
        created_at = _parse_datetime(summary.get("createdAt"))
        if unread_only and seen:
            continue
        if since and created_at and created_at < since:
            continue
        if senders and not any(sender.casefold() in sender_address.casefold() for sender in senders):
            continue
        if subject_patterns and not any(pattern.casefold() in subject.casefold() for pattern in subject_patterns):
            continue
        if listing_title and not _subject_matches_listing(subject, listing_title):
            continue

        full = client.get_message(summary["id"])
        links = extract_preapplication_links(full)
        if require_links and not links:
            continue
        found.append(
            MailTmMessage(
                message_id=summary["id"],
                subject=subject,
                sender=sender_address,
                created_at=created_at,
                seen=seen,
                links=links,
            )
        )
    return found


def _message_bodies(message: dict) -> list[str]:
    bodies: list[str] = []
    text = message.get("text")
    if isinstance(text, str):
        bodies.extend(_body_variants(text))
    html = message.get("html")
    if isinstance(html, list):
        for part in html:
            if isinstance(part, str):
                bodies.extend(_body_variants(part))
    elif isinstance(html, str):
        bodies.extend(_body_variants(html))
    return list(dict.fromkeys(body for body in bodies if body))


def _body_variants(body: str) -> list[str]:
    decoded = _decode_quoted_printable(body)
    if decoded and decoded != body:
        return [decoded, body]
    return [body]


def _decode_quoted_printable(body: str) -> str:
    if "=3D" not in body and "=\r" not in body and "=\n" not in body:
        return body
    try:
        return quopri.decodestring(body.encode("utf-8", errors="replace")).decode(
            "utf-8",
            errors="replace",
        )
    except Exception:
        return body


def _clean_link(href: str) -> str:
    raw = str(href or "").strip()
    if not raw:
        return ""
    if raw.startswith(("3D", "=3D")) or "=3D" in raw:
        return ""
    cleaned = unescape(raw).strip().strip("<>'\"")
    cleaned = cleaned.rstrip(").,;'\"")
    if not re.match(r"^https?://", cleaned, re.I):
        return ""
    return cleaned


def mailtm_senders(*values: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def _subject_matches_listing(subject: str, listing_title: str) -> bool:
    subject_key = _listing_match_key(subject)
    title_key = _listing_match_key(listing_title)
    if not title_key:
        return True
    if title_key in subject_key:
        return True

    title_tokens = title_key.split()
    subject_tokens = set(subject_key.split())
    return all(token in subject_tokens for token in title_tokens)


def _listing_match_key(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.casefold())).strip()


def _looks_like_preapplication_link(text: str, href: str) -> bool:
    combined = f"{text} {href}".casefold()
    if "start pre-application" in combined or "pre-application" in combined:
        return True
    parsed = urlparse(href)
    return "onosre.com" in parsed.netloc.casefold() and "invitation" in parsed.path.casefold()


def _looks_like_complete_application_link(text: str, href: str) -> bool:
    combined = f"{text} {href}".casefold()
    if "complete application" in combined or "finish your application" in combined:
        return True
    parsed = urlparse(href)
    return "onosre.com" in parsed.netloc.casefold() and "application" in parsed.path.casefold()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
