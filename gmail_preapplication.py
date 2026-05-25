from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


@dataclass(frozen=True)
class GmailPreApplicationSettings:
    credentials_path: Path
    token_path: Path
    sender: str
    subject_prefix: str
    max_results: int = 5

    @classmethod
    def from_config(cls) -> GmailPreApplicationSettings:
        import config

        return cls(
            credentials_path=Path(config.ROOFZ_GMAIL_CREDENTIALS_PATH).expanduser(),
            token_path=Path(config.ROOFZ_GMAIL_TOKEN_PATH).expanduser(),
            sender=config.ROOFZ_GMAIL_SENDER,
            subject_prefix=config.ROOFZ_GMAIL_SUBJECT_PREFIX,
        )

    def ready_error(self) -> str | None:
        if not self.credentials_path.exists():
            return f"{self.credentials_path} is missing."
        if not self.token_path.exists():
            return f"{self.token_path} is missing; run scripts/gmail_authorize.py once."
        if not self.sender:
            return "ROOFZ_GMAIL_SENDER is missing."
        if not self.subject_prefix:
            return "ROOFZ_GMAIL_SUBJECT_PREFIX is missing."
        return None


@dataclass(frozen=True)
class GmailPreApplicationMessage:
    message_id: str
    subject: str
    sender: str
    links: list[str]


def authorize_gmail(settings: GmailPreApplicationSettings) -> None:
    flow = InstalledAppFlow.from_client_secrets_file(
        str(settings.credentials_path),
        [GMAIL_READONLY_SCOPE],
    )
    creds = flow.run_local_server(port=0)
    settings.token_path.parent.mkdir(parents=True, exist_ok=True)
    settings.token_path.write_text(creds.to_json(), encoding="utf-8")


def build_gmail_service(settings: GmailPreApplicationSettings):
    creds = Credentials.from_authorized_user_file(str(settings.token_path), [GMAIL_READONLY_SCOPE])
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        settings.token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds)


def build_roofz_preapplication_query(settings: GmailPreApplicationSettings, listing_title: str = "") -> str:
    parts = [
        f"from:{settings.sender}",
        f'subject:"{settings.subject_prefix}"',
        "is:unread",
    ]
    if listing_title:
        parts.append(f'"{listing_title}"')
    return " ".join(parts)


def find_unread_preapplication_messages(
    service,
    settings: GmailPreApplicationSettings,
    listing_title: str = "",
) -> list[GmailPreApplicationMessage]:
    query = build_roofz_preapplication_query(settings, listing_title)
    result = service.users().messages().list(
        userId="me",
        q=query,
        maxResults=settings.max_results,
    ).execute()
    messages = result.get("messages", [])
    found: list[GmailPreApplicationMessage] = []
    for item in messages:
        message = service.users().messages().get(
            userId="me",
            id=item["id"],
            format="full",
        ).execute()
        headers = _headers(message)
        subject = headers.get("subject", "")
        sender = headers.get("from", "")
        links = extract_preapplication_links(message)
        if links:
            found.append(
                GmailPreApplicationMessage(
                    message_id=item["id"],
                    subject=subject,
                    sender=sender,
                    links=links,
                )
            )
    return found


def extract_preapplication_links(message: dict) -> list[str]:
    bodies = list(_payload_bodies(message.get("payload", {})))
    links: list[str] = []
    for mime_type, body in bodies:
        if mime_type == "text/html":
            soup = BeautifulSoup(body, "html.parser")
            for anchor in soup.find_all("a", href=True):
                text = " ".join(anchor.get_text(" ", strip=True).split())
                href = anchor["href"].strip()
                if _looks_like_preapplication_link(text, href):
                    links.append(href)
        elif mime_type == "text/plain":
            for href in re.findall(r"https?://[^\s<>\"]+", body):
                if _looks_like_preapplication_link("", href):
                    links.append(href.rstrip(").,"))

    return list(dict.fromkeys(links))


def _headers(message: dict) -> dict[str, str]:
    values = {}
    for header in message.get("payload", {}).get("headers", []):
        name = header.get("name", "").casefold()
        if name in {"subject", "from"}:
            values[name] = header.get("value", "")
    return values


def _payload_bodies(payload: dict):
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")
    if body_data and mime_type in {"text/html", "text/plain"}:
        yield mime_type, _decode_body(body_data)
    for part in payload.get("parts", []) or []:
        yield from _payload_bodies(part)


def _decode_body(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")


def _looks_like_preapplication_link(text: str, href: str) -> bool:
    combined = f"{text} {href}".casefold()
    if "start pre-application" in combined or "pre-application" in combined:
        return True
    parsed = urlparse(href)
    return "roofz" in parsed.netloc.casefold() and "application" in parsed.path.casefold()
