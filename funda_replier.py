from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from scrapers.base import Listing

logger = logging.getLogger(__name__)


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

    @classmethod
    def from_config(cls) -> FundaReplySettings:
        import config

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
        return None


@dataclass(frozen=True)
class FundaReplyResult:
    status: str
    detail: str = ""


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
            return FundaReplyResult("sent", f"Funda accepted the contact request ({response.status_code}).")

        detail = response.text[:1000]
        if response.status_code == 400:
            return FundaReplyResult("validation_failed", detail)
        if response.status_code in {401, 403, 429}:
            return FundaReplyResult("blocked", detail)
        return FundaReplyResult("submit_failed", f"{response.status_code}: {detail}")


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
