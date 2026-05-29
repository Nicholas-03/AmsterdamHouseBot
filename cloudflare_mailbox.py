from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.parser import Parser

import httpx


@dataclass(frozen=True)
class CloudflareMailboxAuthSettings:
    api_base: str
    api_token: str
    address: str = ""
    max_results: int = 30

    def ready_error(self, prefix: str = "CLOUDFLARE_MAILBOX") -> str | None:
        if not self.api_base:
            return f"{prefix}_API_BASE is missing."
        if not self.api_token:
            return f"{prefix}_API_TOKEN is missing."
        return None


@dataclass(frozen=True)
class CloudflareMailboxSettings(CloudflareMailboxAuthSettings):
    preapplication_sender: str = ""
    forwarder_address: str = ""
    preapplication_subject_prefix: str = ""
    confirmation_sender: str = ""
    confirmation_subject_patterns: tuple[str, ...] = ()

    @classmethod
    def from_config(cls) -> CloudflareMailboxSettings:
        import config

        return cls(
            api_base=config.CLOUDFLARE_MAILBOX_API_BASE,
            api_token=config.CLOUDFLARE_MAILBOX_API_TOKEN,
            address=config.CLOUDFLARE_MAILBOX_ADDRESS,
            preapplication_sender=config.ROOFZ_MAILTM_PREAPPLICATION_SENDER,
            forwarder_address=config.ROOFZ_MAILTM_FORWARDER_ADDRESS,
            preapplication_subject_prefix=config.ROOFZ_MAILTM_PREAPPLICATION_SUBJECT_PREFIX,
            confirmation_sender=config.ROOFZ_MAILTM_CONFIRMATION_SENDER,
            confirmation_subject_patterns=tuple(config.ROOFZ_MAILTM_CONFIRMATION_SUBJECT_PATTERNS),
        )

    def ready_error(self, prefix: str = "CLOUDFLARE_MAILBOX") -> str | None:
        auth_error = super().ready_error(prefix)
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


class CloudflareMailboxClient:
    def __init__(self, settings: CloudflareMailboxAuthSettings):
        self.settings = settings
        self._client = httpx.Client(timeout=30)

    def __enter__(self) -> CloudflareMailboxClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._client.close()

    def list_messages(self) -> list[dict]:
        response = self._client.get(
            f"{self.settings.api_base}/messages",
            headers=self._headers(),
            params={"limit": self.settings.max_results},
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [_normalize_summary(item) for item in payload if isinstance(item, dict)]
        messages = payload.get("messages", []) if isinstance(payload, dict) else []
        return [_normalize_summary(item) for item in messages if isinstance(item, dict)]

    def get_message(self, message_id: str) -> dict:
        response = self._client.get(
            f"{self.settings.api_base}/messages/{message_id}",
            headers=self._headers(),
        )
        response.raise_for_status()
        message = response.json()
        if not isinstance(message, dict):
            return {}
        return _normalize_full_message(message)

    def mark_seen(self, message_id: str) -> None:
        try:
            self._client.post(
                f"{self.settings.api_base}/messages/{message_id}/seen",
                headers=self._headers(),
            )
        except httpx.HTTPError:
            return

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.api_token}"}


def _normalize_summary(message: dict) -> dict:
    sender = message.get("from") or {}
    sender_address = sender.get("address") if isinstance(sender, dict) else str(sender)
    return {
        "id": message.get("id") or message.get("message_id") or "",
        "subject": message.get("subject") or "",
        "from": {"address": sender_address or message.get("sender", "") or ""},
        "seen": bool(message.get("seen")),
        "createdAt": message.get("createdAt") or message.get("created_at") or "",
    }


def _normalize_full_message(message: dict) -> dict:
    normalized = _normalize_summary(message)
    raw = message.get("raw") if isinstance(message.get("raw"), str) else ""
    text_parts, html_parts = _extract_bodies_from_raw(raw)
    if isinstance(message.get("text"), str):
        text_parts.insert(0, message["text"])
    html = message.get("html")
    if isinstance(html, str):
        html_parts.insert(0, html)
    elif isinstance(html, list):
        html_parts = [part for part in html if isinstance(part, str)] + html_parts
    normalized.update(
        {
            "text": "\n\n".join(dict.fromkeys(part for part in text_parts if part)),
            "html": list(dict.fromkeys(part for part in html_parts if part)),
            "raw": raw,
        }
    )
    return normalized


def _extract_bodies_from_raw(raw: str) -> tuple[list[str], list[str]]:
    if not raw:
        return [], []
    try:
        parsed = Parser(policy=policy.default).parsestr(raw)
    except Exception:
        return [raw], [raw]

    text_parts: list[str] = []
    html_parts: list[str] = []
    if parsed.is_multipart():
        parts = parsed.walk()
    else:
        parts = [parsed]
    for part in parts:
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True)
            content = payload.decode(errors="replace") if isinstance(payload, bytes) else str(payload or "")
        if content_type == "text/html":
            html_parts.append(content)
        else:
            text_parts.append(content)
    if not text_parts and not html_parts:
        return [raw], [raw]
    return text_parts, html_parts
