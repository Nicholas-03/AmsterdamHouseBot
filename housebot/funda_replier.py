from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from housebot.cloudflare_mailbox import CloudflareMailboxAuthSettings, CloudflareMailboxClient
from housebot.mailtm_preapplication import (
    MailTmAuthSettings,
    MailTmClient,
    MailTmMessage,
    _message_bodies,
    _parse_datetime,
    find_mailtm_messages,
    mailtm_senders,
)
from housebot.scrapers.base import Listing

logger = logging.getLogger(__name__)

_GENERIC_FUNDA_CONFIRMATION_PATTERNS = (
    "aanvraag voor een bezichtiging via funda",
    "interesse hebt in een woning die wij via funda aanbieden",
    "thank you for your interest in a property that we list on funda",
)


@dataclass(frozen=True)
class FundaConfirmationSettings:
    enabled: bool
    poll_seconds: int
    poll_interval_seconds: int
    mailbox_provider: str
    mailtm: MailTmAuthSettings
    cloudflare_mailbox: CloudflareMailboxAuthSettings
    sender: str
    forwarder_address: str
    subject_patterns: tuple[str, ...]

    @classmethod
    def from_config(cls) -> FundaConfirmationSettings:
        from housebot import config

        return cls(
            enabled=config.FUNDA_CONFIRMATION_ENABLED,
            poll_seconds=config.FUNDA_CONFIRMATION_POLL_SECONDS,
            poll_interval_seconds=max(1, config.FUNDA_CONFIRMATION_POLL_INTERVAL_SECONDS),
            mailbox_provider=config.FUNDA_MAILBOX_PROVIDER,
            mailtm=MailTmAuthSettings(
                api_base=config.FUNDA_MAILTM_API_BASE,
                address=config.FUNDA_MAILTM_ADDRESS,
                password=config.FUNDA_MAILTM_PASSWORD,
            ),
            cloudflare_mailbox=CloudflareMailboxAuthSettings(
                api_base=config.CLOUDFLARE_MAILBOX_API_BASE,
                api_token=config.CLOUDFLARE_MAILBOX_API_TOKEN,
                address=config.CLOUDFLARE_MAILBOX_ADDRESS,
            ),
            sender=config.FUNDA_MAILTM_CONFIRMATION_SENDER,
            forwarder_address=config.FUNDA_MAILTM_FORWARDER_ADDRESS,
            subject_patterns=tuple(config.FUNDA_MAILTM_CONFIRMATION_SUBJECT_PATTERNS),
        )

    def ready_error(self) -> str | None:
        if not self.enabled:
            return None
        if self.mailbox_provider == "cloudflare":
            auth_error = self.cloudflare_mailbox.ready_error("CLOUDFLARE_MAILBOX")
        elif self.mailbox_provider == "mailtm":
            auth_error = self.mailtm.ready_error("FUNDA_MAILTM")
        else:
            auth_error = f"Unsupported FUNDA_MAILBOX_PROVIDER: {self.mailbox_provider}"
        if auth_error:
            return auth_error
        if not self.sender and not self.forwarder_address:
            return "FUNDA_MAILTM_CONFIRMATION_SENDER or FUNDA_MAILTM_FORWARDER_ADDRESS is missing."
        if not self.subject_patterns:
            return "FUNDA_MAILTM_CONFIRMATION_SUBJECT_PATTERNS is missing."
        return None

    @property
    def senders(self) -> tuple[str, ...]:
        return mailtm_senders(self.sender, self.forwarder_address)

    def open_client(self):
        if self.mailbox_provider == "cloudflare":
            return CloudflareMailboxClient(self.cloudflare_mailbox)
        return MailTmClient(self.mailtm)


@dataclass(frozen=True)
class FundaReplySettings:
    enabled: bool
    dry_run: bool
    email: str
    first_name: str
    last_name: str
    phone_number: str
    message: str
    max_per_scan: int
    viewing_request: bool
    contact_api_base: str
    timeout_seconds: int
    confirmation: FundaConfirmationSettings

    @classmethod
    def from_config(cls) -> FundaReplySettings:
        from housebot import config

        return cls(
            enabled=config.FUNDA_AUTO_REPLY_ENABLED,
            dry_run=config.FUNDA_REPLY_DRY_RUN,
            email=config.FUNDA_EMAIL,
            first_name=config.FUNDA_FIRST_NAME,
            last_name=config.FUNDA_LAST_NAME,
            phone_number=config.FUNDA_PHONE_NUMBER,
            message=config.FUNDA_REPLY_MESSAGE,
            max_per_scan=config.FUNDA_REPLY_MAX_PER_SCAN,
            viewing_request=config.FUNDA_VIEWING_REQUEST,
            contact_api_base=config.FUNDA_CONTACT_API_BASE,
            timeout_seconds=max(5, config.FUNDA_BROWSER_TIMEOUT_SECONDS),
            confirmation=FundaConfirmationSettings.from_config(),
        )

    def ready_error(self) -> str | None:
        if not self.enabled:
            return "Funda auto-reply is disabled."
        if not self.email:
            return "FUNDA_EMAIL is missing."
        if not self.first_name:
            return "FUNDA_FIRST_NAME is missing."
        if not self.last_name:
            return "FUNDA_LAST_NAME is missing."
        if not self.phone_number:
            return "FUNDA_PHONE_NUMBER is missing."
        if not self.message:
            return "FUNDA_REPLY_MESSAGE is missing."
        return self.confirmation.ready_error()


@dataclass(frozen=True)
class FundaReplyResult:
    status: str
    detail: str = ""
    sent_at: datetime | None = None
    confirmation_at: datetime | None = None


class FundaReplier:
    def __init__(self, settings: FundaReplySettings):
        self.settings = settings

    async def __aenter__(self) -> FundaReplier:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def reply_to_listing(self, listing: Listing) -> FundaReplyResult:
        global_id = listing.reply_data.get("global_id", "")
        office_id = listing.reply_data.get("office_id", "")
        if not global_id:
            return FundaReplyResult("missing_contact_data", "Funda global listing id is missing.")
        if not office_id:
            return FundaReplyResult("missing_contact_data", "Funda office id is missing.")

        payload = _build_contact_payload(self.settings)
        if self.settings.dry_run:
            return FundaReplyResult("dry_run_ready", "Funda contact payload is complete; submit was skipped.")

        url = f"{self.settings.contact_api_base}/api/v2/contact/listings/{global_id}/contact-request"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Client-Application-Type": "web",
            "Content-Type": "application/json",
            "Origin": "https://www.funda.nl",
            "Referer": listing.contact_url or f"https://www.funda.nl/makelaar-contact/?listingId={global_id}",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }

        started_at = datetime.now(timezone.utc)
        response_result = await self._send_contact_request(listing, url, office_id, headers, payload)
        if response_result.status != "sent" or not self.settings.confirmation.enabled:
            return response_result
        return await self._wait_for_confirmation(listing, started_at, response_result.sent_at)

    async def _send_contact_request(
        self,
        listing: Listing,
        url: str,
        office_id: str,
        headers: dict[str, str],
        payload: dict,
    ) -> FundaReplyResult:
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
                response = await client.post(
                    url,
                    params={"officeId": office_id, "website": "Funda"},
                    headers=headers,
                    json=payload,
                )
        except httpx.HTTPError as exc:
            logger.exception("Funda reply HTTP request failed for %s", listing.url)
            return FundaReplyResult("error", str(exc))

        if 200 <= response.status_code < 300:
            return FundaReplyResult(
                "sent",
                f"Funda accepted the contact request ({response.status_code}).",
                sent_at=datetime.now(timezone.utc),
            )

        detail = response.text[:1000]
        if response.status_code == 400:
            return FundaReplyResult("validation_failed", detail)
        if response.status_code in {401, 403, 429}:
            return FundaReplyResult("blocked", detail)
        return FundaReplyResult("submit_failed", f"{response.status_code}: {detail}")

    async def _wait_for_confirmation(
        self,
        listing: Listing,
        started_at: datetime,
        sent_at: datetime | None,
    ) -> FundaReplyResult:
        confirmation = self.settings.confirmation
        deadline = time.monotonic() + confirmation.poll_seconds
        try:
            with confirmation.open_client() as mailbox:
                while True:
                    messages = await asyncio.to_thread(
                        find_funda_confirmation_messages,
                        mailbox,
                        confirmation.senders,
                        confirmation.subject_patterns,
                        listing.title,
                        started_at,
                    )
                    if messages:
                        return FundaReplyResult(
                            "confirmation_confirmed",
                            "Funda confirmation email arrived.",
                            sent_at=sent_at,
                            confirmation_at=messages[0].created_at or datetime.now(timezone.utc),
                        )
                    if time.monotonic() >= deadline:
                        return FundaReplyResult(
                            "confirmation_missing",
                            "Funda accepted the contact request, but no confirmation email arrived in time.",
                            sent_at=sent_at,
                        )
                    await asyncio.sleep(confirmation.poll_interval_seconds)
        except httpx.HTTPError as exc:
            logger.exception("Funda confirmation check failed for %s", listing.url)
            return FundaReplyResult(
                "confirmation_error",
                f"Funda accepted the contact request, but mailbox confirmation check failed: {exc}",
                sent_at=sent_at,
            )


def find_funda_confirmation_messages(
    client,
    senders: tuple[str, ...],
    subject_patterns: tuple[str, ...],
    listing_title: str = "",
    since: datetime | None = None,
) -> list[MailTmMessage]:
    strict_matches = find_mailtm_messages(
        client,
        senders,
        subject_patterns,
        listing_title,
        since,
    )
    if strict_matches:
        return strict_matches
    return _find_generic_funda_confirmation_messages(client, since=since)


def _find_generic_funda_confirmation_messages(client, since: datetime | None = None) -> list[MailTmMessage]:
    found: list[MailTmMessage] = []
    for summary in client.list_messages():
        message_id = summary.get("id") or summary.get("message_id") or ""
        if not message_id:
            continue
        created_at = _parse_datetime(summary.get("createdAt") or summary.get("created_at"))
        if since and created_at and created_at < since:
            continue

        subject = summary.get("subject") or ""
        sender_address = (summary.get("from") or {}).get("address") or summary.get("sender") or ""
        seen = bool(summary.get("seen"))
        full = client.get_message(message_id)
        if not subject:
            subject = full.get("subject") or ""
        combined = f"{subject}\n" + "\n".join(_message_bodies(full))
        combined_key = combined.casefold()
        if not any(pattern in combined_key for pattern in _GENERIC_FUNDA_CONFIRMATION_PATTERNS):
            continue

        found.append(
            MailTmMessage(
                message_id=message_id,
                subject=subject,
                sender=sender_address,
                created_at=created_at,
                seen=seen,
                links=[],
            )
        )
    return sorted(found, key=lambda message: message.created_at or datetime.min.replace(tzinfo=timezone.utc))


def _build_contact_payload(settings: FundaReplySettings) -> dict:
    return {
        "firstName": settings.first_name,
        "lastName": settings.last_name,
        "emailAddress": settings.email,
        "phoneNumber": settings.phone_number,
        "message": settings.message,
        "days": [],
        "dayParts": [],
        "anonymousUserId": "00000000-0000-0000-0000-000000000000",
        "userId": None,
        "loggedIn": False,
        "acceptedCookies": False,
    }
