from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import config
from cloudflare_mailbox import CloudflareMailboxClient
from mailtm_preapplication import MailTmClient, find_complete_application_messages
from roofz_replier import RoofzReplySettings


@dataclass(frozen=True)
class RoofzCompleteApplicationEmail:
    message_id: str
    subject: str
    sender: str
    links: tuple[str, ...]

    @property
    def listing_title(self) -> str:
        subject = re.sub(r"^\\s*(?:fwd?:\\s*)+", "", self.subject, flags=re.I).strip()
        match = re.search(r"complete application for\\s+(.+)$", subject, flags=re.I)
        return match.group(1).strip().rstrip(".") if match else subject


def complete_application_monitor_ready_error(settings: RoofzReplySettings | None = None) -> str | None:
    settings = settings or RoofzReplySettings.from_config()
    if not config.ROOFZ_COMPLETE_APPLICATION_MONITOR_ENABLED:
        return None
    if settings.mailbox_provider == "cloudflare":
        return settings.cloudflare_mailbox.ready_error()
    if settings.mailbox_provider == "mailtm":
        return settings.mailtm.ready_error()
    return f"Unsupported ROOFZ_MAILBOX_PROVIDER: {settings.mailbox_provider}"


async def find_new_complete_application_emails() -> list[RoofzCompleteApplicationEmail]:
    settings = RoofzReplySettings.from_config()
    ready_error = complete_application_monitor_ready_error(settings)
    if ready_error:
        raise RuntimeError(ready_error)

    mailbox_settings = settings.cloudflare_mailbox if settings.mailbox_provider == "cloudflare" else settings.mailtm
    client_factory = CloudflareMailboxClient if settings.mailbox_provider == "cloudflare" else MailTmClient

    def _read_messages() -> list[RoofzCompleteApplicationEmail]:
        with client_factory(mailbox_settings) as client:
            messages = find_complete_application_messages(
                client,
                mailbox_settings,
                subject_patterns=config.ROOFZ_COMPLETE_APPLICATION_SUBJECT_PATTERNS,
                unread_only=True,
            )
        return [
            RoofzCompleteApplicationEmail(
                message_id=message.message_id,
                subject=message.subject,
                sender=message.sender,
                links=tuple(message.links),
            )
            for message in messages
        ]

    return await asyncio.to_thread(_read_messages)


async def mark_complete_application_email_seen(message_id: str) -> None:
    settings = RoofzReplySettings.from_config()
    ready_error = complete_application_monitor_ready_error(settings)
    if ready_error:
        raise RuntimeError(ready_error)

    mailbox_settings = settings.cloudflare_mailbox if settings.mailbox_provider == "cloudflare" else settings.mailtm
    client_factory = CloudflareMailboxClient if settings.mailbox_provider == "cloudflare" else MailTmClient

    def _mark_seen() -> None:
        with client_factory(mailbox_settings) as client:
            client.mark_seen(message_id)

    await asyncio.to_thread(_mark_seen)
