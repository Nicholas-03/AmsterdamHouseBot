from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class MailTmSettings:
    api_base: str
    address: str
    password: str
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
        if not self.api_base:
            return "ROOFZ_MAILTM_API_BASE is missing."
        if not self.address:
            return "ROOFZ_MAILTM_ADDRESS is missing."
        if not self.password:
            return "ROOFZ_MAILTM_PASSWORD is missing."
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
    def __init__(self, settings: MailTmSettings):
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
        senders=_mailtm_senders(settings.preapplication_sender, settings.forwarder_address),
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
        senders=_mailtm_senders(settings.confirmation_sender, settings.forwarder_address),
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


def extract_preapplication_links(message: dict) -> list[str]:
    links: list[str] = []
    for body in _message_bodies(message):
        soup = BeautifulSoup(body, "html.parser")
        for anchor in soup.find_all("a", href=True):
            text = " ".join(anchor.get_text(" ", strip=True).split())
            href = anchor["href"].strip()
            if _looks_like_preapplication_link(text, href):
                links.append(href)
        for href in re.findall(r"https?://[^\s<>\"]+", body):
            if _looks_like_preapplication_link("", href):
                links.append(href.rstrip(").,"))
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
        if listing_title and listing_title.casefold() not in subject.casefold():
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
        bodies.append(text)
    html = message.get("html")
    if isinstance(html, list):
        bodies.extend(part for part in html if isinstance(part, str))
    elif isinstance(html, str):
        bodies.append(html)
    return bodies


def _mailtm_senders(*values: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def _looks_like_preapplication_link(text: str, href: str) -> bool:
    combined = f"{text} {href}".casefold()
    if "start pre-application" in combined or "pre-application" in combined:
        return True
    parsed = urlparse(href)
    return "onosre.com" in parsed.netloc.casefold() and "invitation" in parsed.path.casefold()


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
