from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

from cloudflare_mailbox import CloudflareMailboxClient, CloudflareMailboxSettings
from mailtm_preapplication import (
    MailTmClient,
    MailTmSettings,
    find_confirmation_messages,
    find_preapplication_messages,
)
from scrapers.base import Listing

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoofzReplySettings:
    enabled: bool
    dry_run: bool
    email: str
    first_name: str
    last_name: str
    phone_number: str
    message: str
    max_per_scan: int
    contact_api_url: str
    headless: bool
    timeout_seconds: int
    preapplication_enabled: bool
    preapplication_api_enabled: bool
    preapplication_poll_seconds: int
    preapplication_poll_interval_seconds: int
    preapplication_api_url: str
    preapplication_availability_api_base: str
    mailbox_provider: str
    mailtm: MailTmSettings
    cloudflare_mailbox: CloudflareMailboxSettings
    initials: str
    birth_date: str
    rent_together: bool
    current_living_situation: str
    work_situation: str
    monthly_income: str
    annual_income: str
    savings: str
    bank_name: str
    expected_stay_duration: str
    expected_move_date: str
    gender: str
    age: str
    occupation: str
    languages: str
    pets: str
    people_moving: str

    @classmethod
    def from_config(cls) -> RoofzReplySettings:
        import config

        return cls(
            enabled=config.ROOFZ_AUTO_REPLY_ENABLED,
            dry_run=config.ROOFZ_REPLY_DRY_RUN,
            email=config.ROOFZ_EMAIL,
            first_name=config.ROOFZ_FIRST_NAME,
            last_name=config.ROOFZ_LAST_NAME,
            phone_number=config.ROOFZ_PHONE_NUMBER,
            message=config.ROOFZ_REPLY_MESSAGE,
            max_per_scan=config.ROOFZ_REPLY_MAX_PER_SCAN,
            contact_api_url=config.ROOFZ_CONTACT_API_URL,
            headless=config.ROOFZ_BROWSER_HEADLESS,
            timeout_seconds=max(5, config.ROOFZ_BROWSER_TIMEOUT_SECONDS),
            preapplication_enabled=config.ROOFZ_PREAPPLICATION_ENABLED,
            preapplication_api_enabled=config.ROOFZ_PREAPPLICATION_API_ENABLED,
            preapplication_poll_seconds=config.ROOFZ_PREAPPLICATION_POLL_SECONDS,
            preapplication_poll_interval_seconds=max(1, config.ROOFZ_PREAPPLICATION_POLL_INTERVAL_SECONDS),
            preapplication_api_url=config.ROOFZ_OSRE_PREAPPLICATION_API_URL,
            preapplication_availability_api_base=config.ROOFZ_OSRE_AVAILABILITY_API_BASE,
            mailbox_provider=config.ROOFZ_MAILBOX_PROVIDER,
            mailtm=MailTmSettings.from_config(),
            cloudflare_mailbox=CloudflareMailboxSettings.from_config(),
            initials=config.ROOFZ_INITIALS,
            birth_date=config.ROOFZ_BIRTH_DATE,
            rent_together=config.ROOFZ_RENT_TOGETHER,
            current_living_situation=config.ROOFZ_CURRENT_LIVING_SITUATION,
            work_situation=config.ROOFZ_WORK_SITUATION,
            monthly_income=config.ROOFZ_MONTHLY_INCOME,
            annual_income=config.ROOFZ_ANNUAL_INCOME,
            savings=config.ROOFZ_SAVINGS,
            bank_name=config.ROOFZ_BANK_NAME,
            expected_stay_duration=config.ROOFZ_EXPECTED_STAY_DURATION,
            expected_move_date=config.ROOFZ_EXPECTED_MOVE_DATE,
            gender=config.ROOFZ_GENDER,
            age=config.ROOFZ_AGE,
            occupation=config.ROOFZ_OCCUPATION,
            languages=config.ROOFZ_LANGUAGES,
            pets=config.ROOFZ_PETS,
            people_moving=config.ROOFZ_PEOPLE_MOVING,
        )

    def ready_error(self) -> str | None:
        if not self.enabled:
            return "Roofz auto-reply is disabled."
        if not self.email:
            return "ROOFZ_EMAIL is missing."
        if not self.first_name:
            return "ROOFZ_FIRST_NAME is missing."
        if not self.last_name:
            return "ROOFZ_LAST_NAME is missing."
        if not self.phone_number:
            return "ROOFZ_PHONE_NUMBER is missing."
        if not self.message:
            return "ROOFZ_REPLY_MESSAGE is missing."
        if not self.contact_api_url:
            return "ROOFZ_CONTACT_API_URL is missing."
        if self.preapplication_enabled:
            if not self.birth_date:
                return "ROOFZ_BIRTH_DATE is missing."
            if not self.monthly_income:
                return "ROOFZ_MONTHLY_INCOME is missing."
            if self.preapplication_api_enabled and not self.preapplication_api_url:
                return "ROOFZ_OSRE_PREAPPLICATION_API_URL is missing."
            if self.mailbox_provider == "cloudflare":
                return self.cloudflare_mailbox.ready_error()
            if self.mailbox_provider == "mailtm":
                return self.mailtm.ready_error()
            return f"Unsupported ROOFZ_MAILBOX_PROVIDER: {self.mailbox_provider}"
        return None


@dataclass(frozen=True)
class RoofzReplyResult:
    status: str
    detail: str = ""
    sent_at: datetime | None = None
    confirmation_at: datetime | None = None


class RoofzReplier:
    def __init__(self, settings: RoofzReplySettings):
        self.settings = settings

    async def __aenter__(self) -> RoofzReplier:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def reply_to_listing(self, listing: Listing) -> RoofzReplyResult:
        property_id = listing.reply_data.get("property_id", "")
        if not property_id:
            return RoofzReplyResult("missing_contact_data", "Roofz property_id is missing.")

        payload = _build_contact_payload(self.settings, property_id)
        if self.settings.dry_run:
            return RoofzReplyResult("dry_run_ready", "Roofz contact payload is complete; submit was skipped.")

        started_at = datetime.now(timezone.utc)
        response_result = await self._send_initial_interest(listing, payload)
        if response_result.status != "sent":
            return response_result

        if not self.settings.preapplication_enabled:
            return response_result

        return await self._complete_preapplication_from_mailtm(listing, started_at, response_result.sent_at)

    async def complete_pending_preapplication(
        self,
        listing: Listing,
        since: datetime,
        initial_sent_at: datetime | None = None,
        *,
        poll_seconds: int = 0,
    ) -> RoofzReplyResult:
        return await self._complete_preapplication_from_mailtm(
            listing,
            since,
            initial_sent_at,
            poll_seconds=poll_seconds,
            unread_only=False,
        )

    async def _send_initial_interest(self, listing: Listing, payload: dict) -> RoofzReplyResult:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://www.roofz.eu",
            "Referer": listing.url,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
                response = await client.post(self.settings.contact_api_url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            logger.exception("Roofz reply HTTP request failed for %s", listing.url)
            return RoofzReplyResult("error", str(exc))

        if 200 <= response.status_code < 300:
            return RoofzReplyResult(
                "sent",
                f"Roofz accepted the contact request ({response.status_code}).",
                sent_at=datetime.now(timezone.utc),
            )

        detail = response.text[:1000]
        if response.status_code == 400:
            return RoofzReplyResult("validation_failed", detail)
        if response.status_code in {401, 403, 429}:
            return RoofzReplyResult("blocked", detail)
        return RoofzReplyResult("submit_failed", f"{response.status_code}: {detail}")

    async def _complete_preapplication_from_mailtm(
        self,
        listing: Listing,
        started_at: datetime,
        initial_sent_at: datetime | None,
        *,
        poll_seconds: int | None = None,
        unread_only: bool = False,
    ) -> RoofzReplyResult:
        deadline = time.monotonic() + (
            self.settings.preapplication_poll_seconds if poll_seconds is None else max(0, poll_seconds)
        )
        last_detail = "No matching Roofz pre-application email arrived yet."
        mailbox_settings = self._mailbox_settings()
        with self._open_mailbox_client() as mailbox:
            while True:
                messages = await asyncio.to_thread(
                    find_preapplication_messages,
                    mailbox,
                    mailbox_settings,
                    listing.title,
                    started_at,
                    unread_only,
                )
                if messages:
                    confirmation_started_at = datetime.now(timezone.utc)
                    result = await self._complete_first_working_preapplication_link(messages)
                    if not result or result.status not in {"preapplication_sent", "preapplication_submitted_unconfirmed"}:
                        detail = result.detail if result else "No usable pre-application link was found."
                        return RoofzReplyResult(
                            "sent_preapplication_failed",
                            f"Initial contact was sent, but pre-application failed: {detail}",
                            sent_at=initial_sent_at,
                        )

                    confirmation = await self._wait_for_confirmation(
                        mailbox,
                        mailbox_settings,
                        listing,
                        confirmation_started_at,
                    )
                    if confirmation:
                        return RoofzReplyResult(
                            "preapplication_confirmed",
                            "Roofz confirmation email arrived.",
                            sent_at=initial_sent_at,
                            confirmation_at=confirmation.created_at or datetime.now(timezone.utc),
                        )
                    return RoofzReplyResult(
                        "preapplication_confirmation_missing",
                        "Pre-application was submitted, but no confirmation email arrived in time.",
                        sent_at=initial_sent_at,
                    )

                if time.monotonic() >= deadline:
                    return RoofzReplyResult("sent_preapplication_pending", last_detail, sent_at=initial_sent_at)
                await asyncio.sleep(self.settings.preapplication_poll_interval_seconds)

    async def _complete_first_working_preapplication_link(self, messages) -> RoofzReplyResult | None:
        last_result: RoofzReplyResult | None = None
        seen_links: set[str] = set()
        for message in messages:
            for link in message.links:
                if link in seen_links:
                    continue
                seen_links.add(link)
                result = await self.complete_preapplication(link)
                if result.status in {"preapplication_sent", "preapplication_submitted_unconfirmed"}:
                    return result
                last_result = result
                logger.warning(
                    "Roofz pre-application link failed for message %s: %s (%s)",
                    getattr(message, "message_id", ""),
                    result.status,
                    result.detail,
                )
        return last_result

    def _mailbox_settings(self):
        if self.settings.mailbox_provider == "cloudflare":
            return self.settings.cloudflare_mailbox
        return self.settings.mailtm

    def _open_mailbox_client(self):
        if self.settings.mailbox_provider == "cloudflare":
            return CloudflareMailboxClient(self.settings.cloudflare_mailbox)
        return MailTmClient(self.settings.mailtm)

    async def _wait_for_confirmation(self, mailbox, mailbox_settings, listing: Listing, since: datetime):
        deadline = time.monotonic() + self.settings.preapplication_poll_seconds
        while True:
            messages = await asyncio.to_thread(
                find_confirmation_messages,
                mailbox,
                mailbox_settings,
                listing.title,
                since,
            )
            if messages:
                return messages[0]
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(self.settings.preapplication_poll_interval_seconds)

    async def complete_preapplication(self, application_url: str) -> RoofzReplyResult:
        api_result: RoofzReplyResult | None = None
        if self.settings.preapplication_api_enabled:
            api_result = await self._complete_preapplication_with_api(application_url)
            if api_result.status in {
                "preapplication_sent",
                "preapplication_submitted_unconfirmed",
                "preapplication_dry_run_ready",
            }:
                return api_result
            logger.warning(
                "Roofz OSRE API pre-application failed for %s (%s); falling back to browser.",
                application_url,
                api_result.detail,
            )

        return await self._complete_preapplication_with_browser(application_url, api_result)

    async def _complete_preapplication_with_api(self, application_url: str) -> RoofzReplyResult:
        invitation = _parse_osre_invitation(application_url)
        if not invitation:
            resolved_url = await _resolve_redirected_url(application_url, self.settings.timeout_seconds)
            invitation = _parse_osre_invitation(resolved_url)
        if not invitation:
            return RoofzReplyResult(
                "preapplication_api_unavailable",
                "Could not parse the OSRE invitation id and token from the pre-application URL.",
            )

        payload = _build_preapplication_payload(self.settings, invitation["invitation_id"])
        if self.settings.dry_run:
            return RoofzReplyResult(
                "preapplication_dry_run_ready",
                "Roofz OSRE pre-application API payload is complete; submit was skipped.",
            )

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": invitation["origin"],
            "Referer": f"{invitation['origin']}/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout_seconds, follow_redirects=True) as client:
                if invitation["token"] and self.settings.preapplication_availability_api_base:
                    await client.put(
                        f"{self.settings.preapplication_availability_api_base}/{invitation['token']}",
                        headers={
                            "Accept": "application/json, text/plain, */*",
                            "Origin": invitation["origin"],
                            "Referer": f"{invitation['origin']}/",
                            "User-Agent": headers["User-Agent"],
                        },
                    )
                response = await client.post(self.settings.preapplication_api_url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            return RoofzReplyResult("preapplication_api_error", str(exc))

        detail = _trim_detail(response.text, self.settings)
        if 200 <= response.status_code < 300:
            return RoofzReplyResult(
                "preapplication_sent",
                f"Roofz OSRE accepted the pre-application API request ({response.status_code}).",
                sent_at=datetime.now(timezone.utc),
            )
        if response.status_code in {400, 409} and re.search(r"(already|submitted|duplicate)", detail, re.I):
            return RoofzReplyResult(
                "preapplication_sent",
                "Roofz OSRE says the pre-application is already submitted.",
                sent_at=datetime.now(timezone.utc),
            )
        if response.status_code == 400:
            return RoofzReplyResult("preapplication_validation_failed", detail)
        if response.status_code in {401, 403, 429}:
            return RoofzReplyResult("preapplication_blocked", f"{response.status_code}: {detail}")
        return RoofzReplyResult("preapplication_submit_failed", f"{response.status_code}: {detail}")

    async def _complete_preapplication_with_browser(
        self,
        application_url: str,
        api_result: RoofzReplyResult | None,
    ) -> RoofzReplyResult:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.settings.headless)
            page = await browser.new_page(viewport={"width": 1440, "height": 1200})
            page.set_default_timeout(self.settings.timeout_seconds * 1000)
            try:
                await page.goto(application_url, wait_until="domcontentloaded", timeout=self.settings.timeout_seconds * 1000)
                await _accept_cookies(page)
                start_button = await _first_button(page, re.compile(r"start pre-?application", re.I))
                if start_button:
                    await start_button.click()
                    await _wait_for_quiet(page)

                for _ in range(12):
                    await _fill_preapplication_form(page, self.settings)
                    final_button = await _first_button(
                        page,
                        re.compile(r"(send|submit|verzend|verstuur|indienen)", re.I),
                    )
                    if final_button:
                        if await final_button.is_disabled():
                            return RoofzReplyResult(
                                "preapplication_validation_failed",
                                "The final submit button stayed disabled after filling known fields.",
                            )
                        if self.settings.dry_run:
                            return RoofzReplyResult(
                                "preapplication_dry_run_ready",
                                "Pre-application form was filled; final submit was skipped.",
                            )
                        await final_button.click()
                        await _wait_for_quiet(page)
                        sent_at = datetime.now(timezone.utc)
                        text = await _body_text(page)
                        if re.search(r"(thank you|submitted|application.*received|success|bedankt|verzonden|sent)", text, re.I):
                            return _with_api_context(
                                RoofzReplyResult(
                                    "preapplication_sent",
                                    "Roofz showed a pre-application confirmation.",
                                    sent_at=sent_at,
                                ),
                                api_result,
                            )
                        return RoofzReplyResult(
                            "preapplication_submitted_unconfirmed",
                            _with_api_detail("Submit was clicked, but no confirmation text was detected.", api_result),
                            sent_at=sent_at,
                        )

                    continue_button = await _first_button(
                        page,
                        re.compile(r"(save and continue|next|continue|volgende|doorgaan)", re.I),
                    )
                    if not continue_button:
                        return RoofzReplyResult("preapplication_submit_not_found", "No continue or submit button was detected.")
                    if await continue_button.is_disabled():
                        return RoofzReplyResult(
                            "preapplication_validation_failed",
                            "The continue button stayed disabled after filling known fields.",
                        )
                    if self.settings.dry_run:
                        return RoofzReplyResult(
                            "preapplication_dry_run_ready",
                            "Pre-application step was fillable; continue was skipped.",
                        )
                    await continue_button.click()
                    await _wait_for_quiet(page)

                return RoofzReplyResult(
                    "preapplication_too_many_steps",
                    _with_api_detail("The pre-application had more steps than expected.", api_result),
                )
            except PlaywrightTimeoutError as exc:
                return RoofzReplyResult("preapplication_timeout", _with_api_detail(str(exc), api_result))
            except Exception as exc:
                logger.exception("Roofz pre-application failed for %s", application_url)
                return RoofzReplyResult("preapplication_error", _with_api_detail(str(exc), api_result))
            finally:
                await browser.close()


def _build_contact_payload(settings: RoofzReplySettings, property_id: str) -> dict:
    return {
        "candidate": {
            "email": settings.email,
        },
        "subscription": {
            "firstname": settings.first_name,
            "lastname": settings.last_name,
            "phone": settings.phone_number,
            "message": settings.message,
            "property_id": int(property_id),
            "metadata": {
                "_ts": int(time.time() * 1000),
            },
        },
    }


def _parse_osre_invitation(application_url: str) -> dict[str, str] | None:
    parsed = urlparse(application_url)
    parts = [part for part in parsed.path.split("/") if part]
    try:
        invitation_index = parts.index("invitation")
        token_index = parts.index("token")
    except ValueError:
        return None
    if invitation_index + 1 >= len(parts) or token_index + 1 >= len(parts):
        return None
    return {
        "origin": f"{parsed.scheme}://{parsed.netloc}",
        "invitation_id": parts[invitation_index + 1],
        "token": parts[token_index + 1],
    }


async def _resolve_redirected_url(application_url: str, timeout_seconds: int) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
            response = await client.get(application_url, headers=headers)
            return str(response.url)
    except httpx.HTTPError:
        return application_url


def _build_preapplication_payload(settings: RoofzReplySettings, invitation_id: str) -> dict:
    return {
        "invitationId": invitation_id,
        "application": {
            "person": {
                "personalDetails": {
                    "initials": settings.initials,
                    "firstName": settings.first_name,
                    "insertion": "",
                    "lastName": settings.last_name,
                    "email": settings.email,
                    "phoneNumber": settings.phone_number,
                    "dateOfBirth": _normalize_date(settings.birth_date),
                    "gender": _normalize_gender(settings.gender),
                    "livingSituation": None,
                },
                "address": {
                    "country": None,
                    "street": None,
                    "houseNumber": None,
                    "houseNumberExtension": None,
                    "postalCode": None,
                    "city": None,
                },
                "idDocument": {
                    "idDocumentType": None,
                    "idDocumentNumber": None,
                    "idIssueDate": None,
                    "idExpirationDate": None,
                    "idIssueCountry": None,
                    "cityOfBirth": None,
                },
                "workSituation": {
                    "workSituation": _normalize_work_situation(settings.work_situation),
                    "workMonthlySalary": _parse_amount(settings.monthly_income),
                },
                "employment": {
                    "employerName": None,
                    "workJobTitle": None,
                    "employerContactName": None,
                    "employerPhoneNumber": None,
                },
                "financialSituation": {
                    "financialSavings": _parse_amount(settings.savings),
                    "financialCredits": 0,
                    "bankName": settings.bank_name or None,
                    "bankAccount": None,
                    "otherBankName": None,
                    "otherBankAccount": None,
                    "annualIncomeYear": None,
                    "annualIncome": _parse_amount(settings.annual_income),
                },
            },
            "partnerSituation": bool(settings.rent_together),
            "partner": None,
            "currentHousingSituation": None,
            "familyComposition": None,
            "maritalState": None,
        },
    }


def _normalize_date(value: str) -> str:
    from datetime import datetime

    normalized = value.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(normalized, fmt).date().isoformat()
        except ValueError:
            continue
    return normalized


def _normalize_gender(value: str) -> str | None:
    normalized = value.casefold().strip()
    if normalized in {"male", "man", "m"}:
        return "male"
    if normalized in {"female", "woman", "f"}:
        return "female"
    return normalized or None


def _normalize_work_situation(value: str) -> str:
    normalized = value.casefold().strip()
    if "student" in normalized:
        return "student"
    if "self" in normalized or "entrepreneur" in normalized or "freelance" in normalized:
        return "entrepreneur"
    if "unemployed" in normalized:
        return "unemployed"
    if "retired" in normalized:
        return "retired"
    if "work" in normalized or "employ" in normalized:
        return "employed"
    return normalized or "student"


def _parse_amount(value: str) -> int:
    normalized = value.strip()
    if not normalized:
        return 0
    normalized = normalized.replace(".", "").replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", normalized)
    if not match:
        return 0
    return int(float(match.group(0)))


def _with_api_context(result: RoofzReplyResult, api_result: RoofzReplyResult | None) -> RoofzReplyResult:
    return RoofzReplyResult(
        result.status,
        _with_api_detail(result.detail, api_result),
        sent_at=result.sent_at,
        confirmation_at=result.confirmation_at,
    )


def _with_api_detail(detail: str, api_result: RoofzReplyResult | None) -> str:
    if not api_result:
        return detail
    return f"Browser fallback after API failure ({api_result.status}: {api_result.detail}): {detail}"


def _trim_detail(detail: str, settings: RoofzReplySettings, limit: int = 1000) -> str:
    sanitized = detail or ""
    for secret in (
        settings.email,
        settings.phone_number,
        settings.message,
        settings.birth_date,
        settings.monthly_income,
        settings.annual_income,
        settings.savings,
    ):
        if secret:
            sanitized = sanitized.replace(secret, "<redacted>")
    return sanitized[:limit]


async def _fill_preapplication_form(page, settings: RoofzReplySettings) -> None:
    values = {
        "first name": settings.first_name,
        "firstname": settings.first_name,
        "voornaam": settings.first_name,
        "last name": settings.last_name,
        "lastname": settings.last_name,
        "achternaam": settings.last_name,
        "initials": settings.initials,
        "e-mail": settings.email,
        "email": settings.email,
        "phone": settings.phone_number,
        "phonenumber": settings.phone_number,
        "telefoon": settings.phone_number,
        "birth": settings.birth_date,
        "dateofbirth": settings.birth_date,
        "message": settings.message,
        "comment": settings.message,
        "motivation": settings.message,
        "stay": settings.expected_stay_duration,
        "verblijfsduur": settings.expected_stay_duration,
        "move": settings.expected_move_date,
        "verhuis": settings.expected_move_date,
        "gender": settings.gender,
        "geslacht": settings.gender,
        "age": settings.age,
        "leeftijd": settings.age,
        "occupation": settings.occupation,
        "beroep": settings.occupation,
        "language": settings.languages,
        "taal": settings.languages,
        "pet": settings.pets,
        "huisdieren": settings.pets,
        "people": settings.people_moving,
        "mensen": settings.people_moving,
        "living situation": settings.current_living_situation,
        "work situation": settings.work_situation,
        "monthly salary": settings.monthly_income,
        "monthly income": settings.monthly_income,
        "annual income": settings.annual_income,
        "savings": settings.savings,
        "equity": settings.savings,
        "bank name": settings.bank_name,
        "bank": settings.bank_name,
    }
    controls = page.locator("input:not([type='hidden']), textarea, select, mat-select")
    count = await controls.count()
    for index in range(count):
        control = controls.nth(index)
        try:
            control_type = (await control.get_attribute("type") or "").casefold()
            if control_type in {"checkbox", "radio", "submit", "button"}:
                continue
            label_text = await _control_label_text(control)
            value = _value_for_label(label_text, values)
            if not value and control_type == "tel":
                value = settings.phone_number
            if not value:
                continue
            tag_name = (await control.evaluate("el => el.tagName")).casefold()
            if tag_name == "select":
                await _select_best_option(control, value)
            elif tag_name == "mat-select":
                await _select_material_option(page, control, value)
            else:
                await control.fill(value)
        except Exception:
            continue

    for checkbox in await page.locator("input[type='checkbox']").all():
        try:
            label_text = await _control_label_text(checkbox)
            if re.search(r"(agree|consent|privacy|terms|akkoord|toestemming)", label_text, re.I):
                await checkbox.check(force=True)
        except Exception:
            continue

    for radio in await page.locator("input[type='radio']").all():
        try:
            option_text = await _radio_option_text(radio)
            group_text = await _radio_group_text(radio)
            value = (await radio.get_attribute("value") or "").casefold()
            normalized_option = option_text.casefold()
            normalized_group = group_text.casefold()
            if _radio_option_matches(value, normalized_option, settings.gender):
                await radio.check(force=True)
            elif "rent together" in normalized_group or "together with someone" in normalized_group:
                expected = "true" if settings.rent_together else "false"
                expected_text = "yes" if settings.rent_together else "no"
                expected_nl = "ja" if settings.rent_together else "nee"
                if (
                    value == expected
                    or _radio_option_matches(value, normalized_option, expected_text)
                    or _radio_option_matches(value, normalized_option, expected_nl)
                ):
                    await radio.check(force=True)
        except Exception:
            continue


async def _control_label_text(control) -> str:
    return await control.evaluate(
        """
        (el) => {
            const bits = [
                el.getAttribute("aria-label"),
                el.getAttribute("placeholder"),
                el.getAttribute("data-placeholder"),
                el.getAttribute("formcontrolname"),
                el.getAttribute("ng-reflect-name"),
                el.name,
                el.id,
            ];
            const id = el.id;
            if (id) {
                const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                if (label) bits.push(label.textContent);
            }
            const wrapper = el.closest("label, mat-form-field, .mat-form-field, .q-field, .input-field, .field");
            if (wrapper) bits.push(wrapper.textContent);
            return bits.filter(Boolean).join(" ");
        }
        """
    )


async def _radio_group_text(radio) -> str:
    return await radio.evaluate(
        """
        (el) => {
            const container =
                el.closest(".form-group")
                || el.closest("[role='radiogroup']")
                || el.closest("mat-radio-group")
                || el.closest(".field");
            return (container?.textContent || "").replace(/\\s+/g, " ").trim();
        }
        """
    )


async def _radio_option_text(radio) -> str:
    return await radio.evaluate(
        """
        (el) => {
            const bits = [
                el.getAttribute("aria-label"),
                el.getAttribute("value"),
            ];
            const id = el.id;
            if (id) {
                const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                if (label) bits.push(label.textContent);
            }
            const parentLabel = el.closest("label");
            if (parentLabel) bits.push(parentLabel.textContent);
            let node = el.nextSibling;
            while (node) {
                if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) {
                    bits.push(node.textContent);
                    break;
                }
                if (node.nodeType === Node.ELEMENT_NODE && node.textContent.trim()) {
                    bits.push(node.textContent);
                    break;
                }
                node = node.nextSibling;
            }
            return bits.filter(Boolean).join(" ");
        }
        """
    )


def _radio_option_matches(value: str, normalized_option_text: str, expected: str) -> bool:
    expected_normalized = expected.casefold().strip()
    if not expected_normalized:
        return False
    if value == expected_normalized:
        return True
    return re.search(rf"(?<![a-z0-9]){re.escape(expected_normalized)}(?![a-z0-9])", normalized_option_text) is not None


def _value_for_label(label: str, values: dict[str, str]) -> str:
    normalized = re.sub(r"\s+", " ", label.casefold())
    for key, value in values.items():
        if value and key in normalized:
            return value
    return ""


async def _select_best_option(control, value: str) -> None:
    options = await control.evaluate(
        """
        (el) => [...el.options].map((option) => ({
            value: option.value,
            text: option.textContent || "",
        }))
        """
    )
    normalized_value = value.casefold()
    for option in options:
        text = option["text"].casefold()
        if normalized_value in text or text in normalized_value:
            await control.select_option(option["value"])
            return


async def _select_material_option(page, control, value: str) -> None:
    await control.click()
    option = page.get_by_role("option", name=re.compile(rf"^{re.escape(value)}$", re.I))
    try:
        if await option.count():
            await option.first.click()
            return
    except Exception:
        pass

    fallback = page.locator("mat-option, [role='option']").filter(has_text=re.compile(re.escape(value), re.I))
    if await fallback.count():
        await fallback.first.click()


async def _first_button(page, pattern: re.Pattern):
    button = page.get_by_role("button", name=pattern)
    try:
        count = await button.count()
    except Exception:
        return None
    for index in range(min(count, 10)):
        item = button.nth(index)
        try:
            if await item.is_visible():
                return item
        except Exception:
            continue
    return None


async def _accept_cookies(page) -> None:
    for label in ("Allow everything", "Accept", "Akkoord", "Allow all", "Alles accepteren"):
        try:
            await page.get_by_role("button", name=re.compile(label, re.I)).click(timeout=1200)
            return
        except Exception:
            pass


async def _body_text(page) -> str:
    try:
        return await page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


async def _wait_for_quiet(page) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10_000)
    except PlaywrightTimeoutError:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=5_000)
    except PlaywrightTimeoutError:
        pass
    await page.wait_for_timeout(700)
