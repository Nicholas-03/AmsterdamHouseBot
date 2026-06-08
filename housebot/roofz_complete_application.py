from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

from housebot.roofz_replier import _normalize_date, _normalize_gender, _normalize_work_situation, _parse_amount

logger = logging.getLogger(__name__)


OSRE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class RoofzCompleteApplicationSettings:
    enabled: bool
    dry_run: bool
    api_enabled: bool
    browser_fallback_enabled: bool
    email: str
    password: str
    account_id: str
    login_url: str
    api_base: str
    headless: bool
    timeout_seconds: int
    finalize_poll_seconds: int
    first_name: str
    last_name: str
    initials: str
    phone_number: str
    birth_date: str
    gender: str
    living_situation: str
    household_situation: str
    family_composition: str
    marital_state: str
    id_document_type: str
    id_document_number: str
    id_issue_date: str
    id_expiration_date: str
    id_issue_country: str
    city_of_birth: str
    address_country: str
    street: str
    house_number: str
    house_number_extension: str
    postal_code: str
    city: str
    work_situation: str
    monthly_income: str
    savings: str
    financial_obligations: str
    bank_name: str
    bank_account: str
    comment: str
    id_document_path: str
    educational_registration_path: str
    salary_slip_paths: tuple[str, ...]
    bank_statement_paths: tuple[str, ...]
    deed_of_guarantee_path: str

    @classmethod
    def from_config(cls) -> RoofzCompleteApplicationSettings:
        from housebot import config

        return cls(
            enabled=config.ROOFZ_COMPLETE_APPLICATION_AUTO_ENABLED,
            dry_run=config.ROOFZ_COMPLETE_APPLICATION_DRY_RUN,
            api_enabled=config.ROOFZ_COMPLETE_APPLICATION_API_ENABLED,
            browser_fallback_enabled=config.ROOFZ_COMPLETE_APPLICATION_BROWSER_FALLBACK_ENABLED,
            email=config.ROOFZ_OSRE_EMAIL,
            password=config.ROOFZ_OSRE_PASSWORD,
            account_id=config.ROOFZ_OSRE_ACCOUNT_ID,
            login_url=config.ROOFZ_OSRE_LOGIN_URL,
            api_base=config.ROOFZ_OSRE_API_BASE,
            headless=config.ROOFZ_BROWSER_HEADLESS,
            timeout_seconds=max(5, config.ROOFZ_BROWSER_TIMEOUT_SECONDS),
            finalize_poll_seconds=config.ROOFZ_COMPLETE_APPLICATION_FINALIZE_POLL_SECONDS,
            first_name=config.ROOFZ_FIRST_NAME,
            last_name=config.ROOFZ_LAST_NAME,
            initials=config.ROOFZ_INITIALS,
            phone_number=config.ROOFZ_PHONE_NUMBER,
            birth_date=config.ROOFZ_BIRTH_DATE,
            gender=config.ROOFZ_GENDER,
            living_situation=config.ROOFZ_COMPLETE_LIVING_SITUATION,
            household_situation=config.ROOFZ_COMPLETE_HOUSEHOLD_SITUATION,
            family_composition=config.ROOFZ_COMPLETE_FAMILY_COMPOSITION,
            marital_state=config.ROOFZ_COMPLETE_MARITAL_STATE,
            id_document_type=config.ROOFZ_COMPLETE_ID_DOCUMENT_TYPE,
            id_document_number=config.ROOFZ_COMPLETE_ID_DOCUMENT_NUMBER,
            id_issue_date=config.ROOFZ_COMPLETE_ID_ISSUE_DATE,
            id_expiration_date=config.ROOFZ_COMPLETE_ID_EXPIRATION_DATE,
            id_issue_country=config.ROOFZ_COMPLETE_ID_ISSUE_COUNTRY,
            city_of_birth=config.ROOFZ_COMPLETE_CITY_OF_BIRTH,
            address_country=config.ROOFZ_COMPLETE_ADDRESS_COUNTRY,
            street=config.ROOFZ_COMPLETE_STREET,
            house_number=config.ROOFZ_COMPLETE_HOUSE_NUMBER,
            house_number_extension=config.ROOFZ_COMPLETE_HOUSE_NUMBER_EXTENSION,
            postal_code=config.ROOFZ_COMPLETE_POSTAL_CODE,
            city=config.ROOFZ_COMPLETE_CITY,
            work_situation=config.ROOFZ_WORK_SITUATION,
            monthly_income=config.ROOFZ_MONTHLY_INCOME,
            savings=config.ROOFZ_SAVINGS,
            financial_obligations=config.ROOFZ_COMPLETE_FINANCIAL_OBLIGATIONS,
            bank_name=config.ROOFZ_BANK_NAME,
            bank_account=config.ROOFZ_COMPLETE_BANK_ACCOUNT,
            comment=config.ROOFZ_COMPLETE_APPLICATION_COMMENT,
            id_document_path=config.ROOFZ_COMPLETE_ID_DOCUMENT_PATH,
            educational_registration_path=config.ROOFZ_COMPLETE_EDUCATIONAL_REGISTRATION_PATH,
            salary_slip_paths=config.ROOFZ_COMPLETE_SALARY_SLIP_PATHS,
            bank_statement_paths=config.ROOFZ_COMPLETE_BANK_STATEMENT_PATHS,
            deed_of_guarantee_path=config.ROOFZ_COMPLETE_DEED_OF_GUARANTEE_PATH,
        )

    def ready_error(self) -> str | None:
        if not self.enabled:
            return "Roofz complete-application auto-submit is disabled."
        required = {
            "ROOFZ_OSRE_EMAIL": self.email,
            "ROOFZ_OSRE_PASSWORD": self.password,
            "ROOFZ_OSRE_ACCOUNT_ID": self.account_id,
            "ROOFZ_OSRE_LOGIN_URL": self.login_url,
            "ROOFZ_OSRE_API_BASE": self.api_base,
            "ROOFZ_FIRST_NAME": self.first_name,
            "ROOFZ_LAST_NAME": self.last_name,
            "ROOFZ_PHONE_NUMBER": self.phone_number,
            "ROOFZ_BIRTH_DATE": self.birth_date,
            "ROOFZ_COMPLETE_ID_DOCUMENT_NUMBER": self.id_document_number,
            "ROOFZ_COMPLETE_ID_ISSUE_DATE": self.id_issue_date,
            "ROOFZ_COMPLETE_ID_EXPIRATION_DATE": self.id_expiration_date,
            "ROOFZ_COMPLETE_CITY_OF_BIRTH": self.city_of_birth,
            "ROOFZ_COMPLETE_STREET": self.street,
            "ROOFZ_COMPLETE_HOUSE_NUMBER": self.house_number,
            "ROOFZ_COMPLETE_POSTAL_CODE": self.postal_code,
            "ROOFZ_COMPLETE_CITY": self.city,
            "ROOFZ_MONTHLY_INCOME": self.monthly_income,
            "ROOFZ_SAVINGS": self.savings,
            "ROOFZ_BANK_NAME": self.bank_name,
            "ROOFZ_COMPLETE_BANK_ACCOUNT": self.bank_account,
            "ROOFZ_COMPLETE_APPLICATION_COMMENT": self.comment,
        }
        for name, value in required.items():
            if not str(value or "").strip():
                return f"{name} is missing."

        if len(self.salary_slip_paths) < 3:
            return "ROOFZ_COMPLETE_SALARY_SLIP_PATHS must contain at least 3 files."
        if len(self.bank_statement_paths) < 3:
            return "ROOFZ_COMPLETE_BANK_STATEMENT_PATHS must contain at least 3 files."

        file_requirements = [
            ("ROOFZ_COMPLETE_ID_DOCUMENT_PATH", self.id_document_path),
            ("ROOFZ_COMPLETE_EDUCATIONAL_REGISTRATION_PATH", self.educational_registration_path),
            ("ROOFZ_COMPLETE_DEED_OF_GUARANTEE_PATH", self.deed_of_guarantee_path),
        ]
        file_requirements.extend((f"ROOFZ_COMPLETE_SALARY_SLIP_PATHS[{index}]", path) for index, path in enumerate(self.salary_slip_paths))
        file_requirements.extend((f"ROOFZ_COMPLETE_BANK_STATEMENT_PATHS[{index}]", path) for index, path in enumerate(self.bank_statement_paths))
        for name, value in file_requirements:
            if not str(value or "").strip():
                return f"{name} is missing."
            if not Path(value).expanduser().exists():
                return f"{name} does not exist."
        return None

    def document_uploads(self) -> list[tuple[str, Path]]:
        uploads = [
            ("identityDocument", Path(self.id_document_path).expanduser()),
            ("educationalRegistration", Path(self.educational_registration_path).expanduser()),
        ]
        uploads.extend(("salarySlip", Path(path).expanduser()) for path in self.salary_slip_paths)
        uploads.extend(("bankStatement", Path(path).expanduser()) for path in self.bank_statement_paths)
        uploads.append(("deedOfGuarantee", Path(self.deed_of_guarantee_path).expanduser()))
        return uploads


@dataclass(frozen=True)
class RoofzCompleteApplicationResult:
    status: str
    detail: str = ""
    sent_at: datetime | None = None


class RoofzCompleteApplicationCompleter:
    def __init__(
        self,
        settings: RoofzCompleteApplicationSettings,
        *,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ):
        self.settings = settings
        self.transport = transport

    async def complete_application(self, application_url: str) -> RoofzCompleteApplicationResult:
        ready_error = self.settings.ready_error()
        if ready_error:
            return RoofzCompleteApplicationResult("complete_application_not_ready", ready_error)

        api_result: RoofzCompleteApplicationResult | None = None
        if self.settings.api_enabled:
            api_result = await self._complete_with_api(application_url)
            if _complete_application_status_ok(api_result.status):
                return api_result
            logger.warning(
                "Roofz complete-application API failed for %s (%s); browser fallback=%s",
                application_url,
                api_result.detail,
                self.settings.browser_fallback_enabled,
            )

        if not self.settings.browser_fallback_enabled:
            return api_result or RoofzCompleteApplicationResult(
                "complete_application_api_disabled",
                "Roofz complete-application API is disabled and browser fallback is disabled.",
            )
        return await self._complete_with_browser(application_url, api_result)

    async def _complete_with_api(self, application_url: str) -> RoofzCompleteApplicationResult:
        application_id = await self._resolve_application_id(application_url)
        if not application_id:
            return RoofzCompleteApplicationResult(
                "complete_application_api_unavailable",
                "Could not parse the OSRE application id from the complete-application URL.",
            )
        if self.settings.dry_run:
            return RoofzCompleteApplicationResult(
                "complete_application_dry_run_ready",
                f"Roofz complete-application payload is ready for {application_id}; submit was skipped.",
            )

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.timeout_seconds,
                follow_redirects=True,
                transport=self.transport,
            ) as client:
                session = await self._login(client)
                headers = self._api_headers(session)
                application = await self._get_application(client, headers, application_id)
                attributes = application.get("attributes") or {}
                if attributes.get("status") == "full_application_completed":
                    return RoofzCompleteApplicationResult(
                        "complete_application_sent",
                        "Roofz OSRE says the complete application is already submitted.",
                        sent_at=datetime.now(timezone.utc),
                    )
                person_id = attributes.get("personId")
                if not person_id:
                    return RoofzCompleteApplicationResult(
                        "complete_application_validation_failed",
                        "OSRE application did not include a personId for document uploads.",
                    )

                questionnaire = _build_questionnaire_payload(self.settings)
                update_response = await client.put(
                    f"{self.settings.api_base.rstrip('/')}/portal/applications/{application_id}",
                    headers={**headers, "Content-Type": "application/json"},
                    json={
                        "application": {
                            "questionnaire": questionnaire,
                            "verifiedQuestionnaire": None,
                        },
                        "hasPreApplication": None,
                        "applicationProgress": None,
                    },
                )
                if not 200 <= update_response.status_code < 300:
                    return _api_failure("complete_application_update_failed", update_response, self.settings)

                await self._upload_missing_documents(client, headers, application_id, str(person_id), attributes)

                finalize_payload = dict(questionnaire)
                finalize_payload["comment"] = self.settings.comment
                finalize_response = await client.put(
                    f"{self.settings.api_base.rstrip('/')}/portal/applications/{application_id}/finalize",
                    headers={**headers, "Content-Type": "application/json"},
                    json={"confirm_finalization": True, "application": finalize_payload},
                )
                if not 200 <= finalize_response.status_code < 300:
                    return _api_failure("complete_application_finalize_failed", finalize_response, self.settings)

                completed = await self._wait_until_completed(client, headers, application_id)
                if completed:
                    return RoofzCompleteApplicationResult(
                        "complete_application_sent",
                        "Roofz OSRE accepted and completed the full application.",
                        sent_at=datetime.now(timezone.utc),
                    )
                return RoofzCompleteApplicationResult(
                    "complete_application_pending_verification",
                    "OSRE accepted the finalize request, but the application status did not become completed in time.",
                    sent_at=datetime.now(timezone.utc),
                )
        except httpx.HTTPError as exc:
            return RoofzCompleteApplicationResult("complete_application_api_error", _trim_detail(str(exc), self.settings))

    async def _resolve_application_id(self, application_url: str) -> str | None:
        application_id = _parse_application_id(application_url)
        if application_id:
            return application_id

        resolved_url = await _resolve_url(application_url, self.settings.timeout_seconds, self.transport)
        application_id = _parse_application_id(resolved_url)
        if application_id:
            return application_id

        invitation_id = _parse_invitation_id(resolved_url) or _parse_invitation_id(application_url)
        if not invitation_id:
            return None
        return await _resolve_application_id_from_invitation(
            invitation_id,
            self.settings,
            self.transport,
        )

    async def _login(self, client: httpx.AsyncClient) -> str:
        response = await client.post(
            self.settings.login_url,
            json={
                "accountId": self.settings.account_id,
                "email": self.settings.email,
                "password": self.settings.password,
            },
            headers={"Accept": "application/json", "User-Agent": OSRE_USER_AGENT},
        )
        response.raise_for_status()
        return response.json()["data"]["attributes"]["session"]

    def _api_headers(self, session: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {session}",
            "Accept": "application/json",
            "Origin": "https://roofz.onosre.com",
            "Referer": "https://roofz.onosre.com/",
            "User-Agent": OSRE_USER_AGENT,
        }

    async def _get_application(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        application_id: str,
    ) -> dict[str, Any]:
        response = await client.get(
            f"{self.settings.api_base.rstrip('/')}/portal/applications/{application_id}",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()["data"]

    async def _upload_missing_documents(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        application_id: str,
        person_id: str,
        attributes: dict[str, Any],
    ) -> None:
        uploaded_names = _attachment_file_names(attributes.get("attachments") or [])
        for file_type, path in self.settings.document_uploads():
            if path.name.casefold() in uploaded_names:
                continue
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            with path.open("rb") as file_handle:
                response = await client.post(
                    f"{self.settings.api_base.rstrip('/')}/portal/applications/{application_id}/files",
                    headers=headers,
                    files=[
                        ("fileUpload", (path.name, file_handle, content_type)),
                        ("personId", (None, person_id)),
                        ("type", (None, file_type)),
                    ],
                )
            if not 200 <= response.status_code < 300:
                raise httpx.HTTPStatusError(
                    f"OSRE file upload failed for {path.name}: {response.status_code} {response.text}",
                    request=response.request,
                    response=response,
                )

    async def _wait_until_completed(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        application_id: str,
    ) -> bool:
        deadline = time.monotonic() + max(0, self.settings.finalize_poll_seconds)
        while True:
            application = await self._get_application(client, headers, application_id)
            status = (application.get("attributes") or {}).get("status")
            if status == "full_application_completed":
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(2)

    async def _complete_with_browser(
        self,
        application_url: str,
        api_result: RoofzCompleteApplicationResult | None,
    ) -> RoofzCompleteApplicationResult:
        if self.settings.dry_run:
            return RoofzCompleteApplicationResult(
                "complete_application_dry_run_ready",
                _with_api_detail("Browser fallback would fill the complete application; submit was skipped.", api_result),
            )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.settings.headless)
            page = await browser.new_page(viewport={"width": 1440, "height": 1400})
            page.set_default_timeout(self.settings.timeout_seconds * 1000)
            try:
                await page.goto(application_url, wait_until="domcontentloaded", timeout=self.settings.timeout_seconds * 1000)
                await _login_if_needed(page, self.settings, application_url)
                await _wait_for_osre_ready(page)
                await _fill_name_and_address(page, self.settings)
                await _fill_work_and_financial(page, self.settings)
                await _fill_documents(page, self.settings)
                await _fill_comments_and_send(page, self.settings)
                return RoofzCompleteApplicationResult(
                    "complete_application_sent",
                    _with_api_detail("Roofz complete application was submitted with browser fallback.", api_result),
                    sent_at=datetime.now(timezone.utc),
                )
            except PlaywrightTimeoutError as exc:
                return RoofzCompleteApplicationResult(
                    "complete_application_browser_timeout",
                    _with_api_detail(str(exc), api_result),
                )
            except Exception as exc:
                logger.exception("Roofz complete-application browser fallback failed for %s", application_url)
                return RoofzCompleteApplicationResult(
                    "complete_application_browser_error",
                    _with_api_detail(str(exc), api_result),
                )
            finally:
                await browser.close()


def _parse_application_id(application_url: str) -> str | None:
    parsed = urlparse(application_url)
    parts = [part for part in parsed.path.split("/") if part]
    try:
        index = parts.index("application")
    except ValueError:
        return None
    if index + 1 >= len(parts):
        return None
    return parts[index + 1] or None


def _parse_invitation_id(application_url: str) -> str | None:
    parsed = urlparse(application_url)
    parts = [part for part in parsed.path.split("/") if part]
    try:
        index = parts.index("invitation")
    except ValueError:
        return None
    if index + 1 >= len(parts):
        return None
    return parts[index + 1] or None


def _build_questionnaire_payload(settings: RoofzCompleteApplicationSettings) -> dict[str, Any]:
    return {
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
                "livingSituation": _normalize_token(settings.living_situation),
            },
            "address": {
                "country": _normalize_country(settings.address_country),
                "street": settings.street,
                "houseNumber": int(_parse_amount(settings.house_number)),
                "houseNumberExtension": settings.house_number_extension or None,
                "postalCode": settings.postal_code,
                "city": settings.city,
            },
            "idDocument": {
                "idDocumentType": _normalize_id_document_type(settings.id_document_type),
                "idDocumentNumber": settings.id_document_number,
                "idIssueDate": _normalize_date(settings.id_issue_date),
                "idExpirationDate": _normalize_date(settings.id_expiration_date),
                "idIssueCountry": settings.id_issue_country,
                "cityOfBirth": settings.city_of_birth,
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
                "financialCredits": _parse_amount(settings.financial_obligations),
                "bankName": settings.bank_name,
                "bankAccount": settings.bank_account.replace(" ", ""),
                "otherBankName": None,
                "otherBankAccount": None,
                "annualIncomeYear": None,
                "annualIncome": 0,
            },
        },
        "partnerSituation": False,
        "partner": None,
        "currentHousingSituation": _normalize_token(settings.household_situation),
        "familyComposition": _normalize_token(settings.family_composition),
        "maritalState": _normalize_token(settings.marital_state),
    }


def _normalize_token(value: str) -> str:
    normalized = value.casefold().strip()
    known = {
        "renting": "renting",
        "alone": "alone",
        "single": "single",
        "single without children": "single_without_children",
        "single with children": "single_with_children",
        "couple without children": "couple_without_children",
        "couple with children": "couple_with_children",
    }
    return known.get(normalized, re.sub(r"[^a-z0-9]+", "_", normalized).strip("_"))


def _normalize_country(value: str) -> str:
    normalized = value.casefold().strip()
    if normalized in {"netherlands", "nl", "nederland"}:
        return "NL"
    if normalized in {"italy", "it", "italia"}:
        return "IT"
    return value


def _normalize_id_document_type(value: str) -> str:
    normalized = value.casefold().strip()
    if "identity" in normalized or normalized in {"id", "id card"}:
        return "identity_card"
    if "passport" in normalized:
        return "passport"
    if "residence" in normalized:
        return "residence_permit"
    if "driver" in normalized:
        return "drivers_license"
    return _normalize_token(value)


async def _resolve_url(
    url: str,
    timeout_seconds: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=True,
            transport=transport,
        ) as client:
            response = await client.get(url, headers={"User-Agent": OSRE_USER_AGENT})
            return str(response.url)
    except httpx.HTTPError:
        return url


async def _resolve_application_id_from_invitation(
    invitation_id: str,
    settings: RoofzCompleteApplicationSettings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str | None:
    try:
        async with httpx.AsyncClient(
            timeout=settings.timeout_seconds,
            follow_redirects=True,
            transport=transport,
        ) as client:
            response = await client.get(
                f"{settings.api_base.rstrip('/')}/portal/invitations/{invitation_id}",
                headers={
                    "Accept": "application/json",
                    "Origin": "https://roofz.onosre.com",
                    "Referer": "https://roofz.onosre.com/",
                    "User-Agent": OSRE_USER_AGENT,
                },
            )
            response.raise_for_status()
    except httpx.HTTPError:
        return None

    try:
        payload = response.json()
    except ValueError:
        return None
    attributes = ((payload.get("data") or {}).get("attributes") or {}) if isinstance(payload, dict) else {}
    application_id = attributes.get("applicationId")
    return str(application_id).strip() if application_id else None


def _attachment_file_names(attachments: list[dict[str, Any]]) -> set[str]:
    names = set()
    for attachment in attachments:
        raw = str(attachment.get("fileName") or "")
        parsed = urlparse(raw)
        name = Path(parsed.path or raw).name.casefold()
        if name:
            names.add(name)
    return names


def _api_failure(
    status: str,
    response: httpx.Response,
    settings: RoofzCompleteApplicationSettings,
) -> RoofzCompleteApplicationResult:
    detail = _trim_detail(response.text, settings)
    if response.status_code == 400:
        status = "complete_application_validation_failed"
    elif response.status_code in {401, 403, 429}:
        status = "complete_application_blocked"
    return RoofzCompleteApplicationResult(status, f"{response.status_code}: {detail}")


def _complete_application_status_ok(status: str) -> bool:
    return status in {
        "complete_application_sent",
        "complete_application_dry_run_ready",
        "complete_application_pending_verification",
    }


def _with_api_detail(detail: str, api_result: RoofzCompleteApplicationResult | None) -> str:
    if not api_result:
        return detail
    return f"Browser fallback after API failure ({api_result.status}: {api_result.detail}): {detail}"


def _trim_detail(detail: str, settings: RoofzCompleteApplicationSettings, limit: int = 1000) -> str:
    sanitized = detail or ""
    for secret in (
        settings.email,
        settings.password,
        settings.phone_number,
        settings.bank_account,
        settings.id_document_number,
    ):
        if secret:
            sanitized = sanitized.replace(secret, "<redacted>")
    return sanitized[:limit]


async def _login_if_needed(page, settings: RoofzCompleteApplicationSettings, application_url: str) -> None:
    if await page.locator('input[type="email"]').count():
        await page.locator('input[type="email"]').fill(settings.email)
        await page.locator('input[type="password"]').fill(settings.password)
        await page.get_by_role("button", name=re.compile(r"log in", re.I)).click()
        await page.wait_for_timeout(3500)
        if _parse_application_id(application_url) and _parse_application_id(page.url) != _parse_application_id(application_url):
            await page.goto(application_url, wait_until="domcontentloaded")
    if "/login" in page.url:
        raise RuntimeError("OSRE login failed")


async def _wait_for_osre_ready(page) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        pass
    await page.locator("mat-expansion-panel").first.wait_for(state="attached", timeout=10_000)
    await page.wait_for_timeout(500)


async def _click_section(page, name: str, ready_selector: str) -> bool:
    if await _is_visible(page.locator(ready_selector).first):
        return True
    button = page.get_by_role("button", name=name)
    if await button.count() == 0:
        return False
    await button.first.click()
    await page.wait_for_timeout(500)
    return await _is_visible(page.locator(ready_selector).first)


async def _is_visible(locator) -> bool:
    try:
        return await locator.is_visible(timeout=500)
    except Exception:
        return False


async def _fill_name_and_address(page, settings: RoofzCompleteApplicationSettings) -> None:
    if not await _click_section(page, "Name and address", 'mat-select[aria-label="living situation"]'):
        return
    await page.locator(f'input[type="radio"][value="{_normalize_gender(settings.gender)}"]').check(force=True)
    await _fill_input(page, ("Initials",), settings.initials)
    await _fill_input(page, ("firstName",), settings.first_name)
    await _fill_input(page, ("insertion",), "")
    await _fill_input(page, ("lastName",), settings.last_name)
    await _fill_input(page, ("Email",), settings.email)
    if await page.locator('input[type="tel"]').count():
        await page.locator('input[type="tel"]').fill(_phone_without_country_prefix(settings.phone_number))
    await _fill_input(page, ("Birth date",), settings.birth_date)
    await _select_mat(page, "living situation", settings.living_situation)
    await _select_mat(page, "household situation", settings.household_situation)
    await _select_mat(page, "family composition", settings.family_composition)
    await _select_mat(page, "marital state", settings.marital_state)
    await _select_mat(page, "document type", settings.id_document_type)
    await _fill_input(page, ("document number",), settings.id_document_number)
    await _fill_input(page, ("issue date",), settings.id_issue_date)
    await _fill_input(page, ("expiration date",), settings.id_expiration_date)
    await _select_mat(page, "id issuing country", settings.id_issue_country)
    await _fill_input(page, ("city of birth",), settings.city_of_birth)
    await _select_mat(page, "country", settings.address_country)
    await _fill_input(page, ("Street",), settings.street)
    await _fill_input(page, ("houseNumber",), settings.house_number)
    await _fill_input(page, ("Addition",), settings.house_number_extension)
    await _fill_input(page, ("Postal code", "postalCode"), settings.postal_code)
    await _fill_input(page, ("City",), settings.city)
    await _click_visible_save(page, "name and address")


async def _fill_work_and_financial(page, settings: RoofzCompleteApplicationSettings) -> None:
    if not await _click_section(page, "Work and financial", 'input[aria-label="Bank account"]'):
        return
    await _select_mat(page, "workSituation", settings.work_situation, required=False)
    await _fill_input(page, ("gross monthly income",), settings.monthly_income)
    await _fill_input(page, ("Savings, equity",), settings.savings)
    await _fill_input(page, ("Credits",), settings.financial_obligations)
    await _fill_input(page, ("Bank account",), settings.bank_account.replace(" ", ""))
    await _click_visible_save(page, "work and financial")


async def _fill_documents(page, settings: RoofzCompleteApplicationSettings) -> None:
    if not await _click_section(page, "Documents", 'input[type="file"]'):
        return
    inputs = page.locator('input[type="file"]:visible')
    if await inputs.count() < 10:
        inputs = page.locator('mat-expansion-panel.mat-expanded input[type="file"]')
    if await inputs.count() < 10:
        raise RuntimeError(f"Expected at least 10 document file inputs, found {await inputs.count()}")
    uploads = {
        0: Path(settings.id_document_path).expanduser(),
        2: Path(settings.educational_registration_path).expanduser(),
        3: Path(settings.salary_slip_paths[0]).expanduser(),
        4: Path(settings.salary_slip_paths[1]).expanduser(),
        5: Path(settings.salary_slip_paths[2]).expanduser(),
        6: Path(settings.bank_statement_paths[0]).expanduser(),
        7: Path(settings.bank_statement_paths[1]).expanduser(),
        8: Path(settings.bank_statement_paths[2]).expanduser(),
        9: Path(settings.deed_of_guarantee_path).expanduser(),
    }
    for index, path in uploads.items():
        await inputs.nth(index).set_input_files(str(path))
        await page.wait_for_timeout(1000)
    await _click_visible_save(page, "documents")


async def _fill_comments_and_send(page, settings: RoofzCompleteApplicationSettings) -> None:
    textarea = page.locator("textarea")
    if await textarea.count():
        await textarea.first.fill(settings.comment)
    checkbox = page.locator('input[type="checkbox"]')
    await checkbox.first.wait_for(state="attached", timeout=10_000)
    await checkbox.first.check(force=True)
    send = page.get_by_role("button", name="Send application", exact=True)
    await send.wait_for(state="visible", timeout=10_000)
    if await send.is_disabled():
        raise RuntimeError("Send application button stayed disabled after filling the form.")
    await send.click()
    await page.wait_for_timeout(2000)
    for label in ("Send application", "Confirm", "Yes", "OK", "Ok"):
        candidate = page.get_by_role("button", name=label, exact=True)
        if await candidate.count() and await candidate.first.is_visible() and await candidate.first.is_enabled():
            await candidate.first.click()
            break
    await page.wait_for_timeout(3000)


async def _fill_input(page, names: tuple[str, ...], value: str) -> None:
    selectors = []
    for name in names:
        selectors.extend([
            f'input[aria-label="{name}"]',
            f'textarea[aria-label="{name}"]',
            f'input[formcontrolname="{name}"]',
            f'textarea[formcontrolname="{name}"]',
        ])
    locator = page.locator(", ".join(selectors))
    count = await locator.count()
    if count == 0:
        return
    target = locator.first
    state = await target.evaluate("el => ({ readonly: Boolean(el.readOnly), disabled: Boolean(el.disabled), value: el.value || '' })")
    if state["readonly"] or state["disabled"]:
        return
    await target.fill(value)
    try:
        await target.press("Tab", timeout=1000)
    except Exception:
        pass


async def _select_mat(page, aria: str, option: str, *, required: bool = True) -> None:
    select = page.locator(f'mat-select[aria-label="{aria}"]')
    count = await select.count()
    if count == 0:
        if required:
            raise RuntimeError(f"Expected mat-select with aria-label {aria!r}")
        return
    current = await select.first.text_content(timeout=1000) or ""
    if option.casefold() in current.casefold():
        return
    await select.first.scroll_into_view_if_needed()
    trigger = select.first.locator(".mat-select-trigger")
    if await trigger.count():
        await trigger.click(force=True)
    else:
        await select.first.click(force=True)
    await page.locator("mat-option").first.wait_for(state="visible", timeout=5000)
    clicked = await page.evaluate(
        """option => {
          const normalize = value => (value || "").trim().replace(/\\s+/g, " ");
          const options = Array.from(document.querySelectorAll("mat-option"));
          const exact = options.find(element => normalize(element.textContent) === option);
          if (exact) {
            exact.click();
            return true;
          }
          const lower = option.toLowerCase();
          const partial = options.find(element => normalize(element.textContent).toLowerCase().includes(lower));
          if (partial) {
            partial.click();
            return true;
          }
          return false;
        }""",
        option,
    )
    if not clicked:
        raise RuntimeError(f"Could not find option {option!r} for {aria!r}.")
    await page.wait_for_timeout(250)


async def _click_visible_save(page, section: str) -> None:
    last_candidates = []
    for _ in range(40):
        buttons = page.locator("button:visible").filter(has_text="Save")
        candidates = []
        for index in range(await buttons.count()):
            button = buttons.nth(index)
            candidates.append({
                "index": index,
                "text": (await button.inner_text(timeout=500)).strip(),
                "enabled": await button.is_enabled(timeout=500),
            })
        last_candidates = candidates
        for index, candidate in enumerate(candidates):
            if candidate.get("enabled"):
                await buttons.nth(index).scroll_into_view_if_needed()
                await buttons.nth(index).click()
                await page.wait_for_timeout(2500)
                return
        await page.wait_for_timeout(500)
    raise RuntimeError(f"Could not click visible enabled Save button for {section}. Candidates: {last_candidates}")


def _phone_without_country_prefix(phone_number: str) -> str:
    normalized = re.sub(r"\s+", "", phone_number)
    if normalized.startswith("+39"):
        return normalized[3:]
    return normalized.lstrip("+")
