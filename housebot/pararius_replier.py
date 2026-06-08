from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

from housebot.scrapers._resilient_fetch import fetch_html
from housebot.scrapers.base import Listing

logger = logging.getLogger(__name__)

try:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession

    _USE_CURL = True
except ImportError:
    CurlAsyncSession = None
    _USE_CURL = False


_BASE_URL = "https://www.pararius.nl"
_LOGIN_URL = f"{_BASE_URL}/inloggen"
_DEFAULT_HEADERS = {
    "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Cache-Control": "max-age=0",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}
_PARARIUS_CURL_IMPERSONATIONS = (
    "safari18_0",
    "safari17_0",
    "safari15_5",
    "chrome133a",
    "chrome124",
)
_CONTACT_HREF_RE = re.compile(r"^/contact/[0-9a-f-]{8,}$", re.I)
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f-]{27,}$", re.I)
_CHALLENGE_RE = re.compile(
    r"(just a moment|enable javascript and cookies|__cf_chl|cf-challenge|"
    r"beveiliging wordt geverifieerd|beveiligingsservice|controleert of u geen bot bent|ray id:)",
    re.I,
)
_LOGIN_PAGE_RE = re.compile(r"\b(inloggen|log in|login)\b", re.I)
_SUCCESS_RE = re.compile(
    r"(reactie is verzonden|bericht is verzonden|message has been sent|response has been sent|successfully sent|bedankt)",
    re.I,
)
_ERROR_RE = re.compile(r"(verplicht|required|ongeldig|invalid|probeer het opnieuw|try again|fout|error)", re.I)
_FIELD_HINTS = {
    "email": re.compile(r"(e-?mail|email)", re.I),
    "first_name": re.compile(r"(first.?name|voornaam|given.?name)", re.I),
    "last_name": re.compile(r"(last.?name|surname|achternaam|family.?name)", re.I),
    "full_name": re.compile(r"(^|[^a-z])(name|naam)([^a-z]|$)", re.I),
    "phone": re.compile(r"(phone|mobile|telephone|telefoon|mobiel)", re.I),
    "message": re.compile(r"(message|bericht|motivation|motivatie|reaction|reactie|comment|toelichting)", re.I),
}
_ACCEPTANCE_RE = re.compile(r"(privacy|terms|conditions|voorwaarden|akkoord|agree|accept)", re.I)


@dataclass(frozen=True)
class ParariusReplySettings:
    enabled: bool
    dry_run: bool
    email: str
    password: str
    first_name: str
    last_name: str
    phone_number: str
    message: str
    max_per_scan: int
    salutation: str
    date_of_birth: str
    work_situation: str
    monthly_income: str
    guarantor: str
    preferred_living_situation: str
    number_of_tenants: str
    pets: str
    rent_start_date: str
    preferred_contract_period: str
    current_housing_situation: str
    headless: bool
    timeout_seconds: int
    storage_state_path: Path
    browser_fallback_enabled: bool

    @classmethod
    def from_config(cls) -> ParariusReplySettings:
        from housebot import config

        return cls(
            enabled=config.PARARIUS_AUTO_REPLY_ENABLED,
            dry_run=config.PARARIUS_REPLY_DRY_RUN,
            email=config.PARARIUS_EMAIL,
            password=config.PARARIUS_PASSWORD,
            first_name=config.PARARIUS_FIRST_NAME,
            last_name=config.PARARIUS_LAST_NAME,
            phone_number=config.PARARIUS_PHONE_NUMBER,
            message=config.PARARIUS_REPLY_MESSAGE,
            max_per_scan=config.PARARIUS_REPLY_MAX_PER_SCAN,
            salutation=config.PARARIUS_SALUTATION,
            date_of_birth=config.PARARIUS_DATE_OF_BIRTH,
            work_situation=config.PARARIUS_WORK_SITUATION,
            monthly_income=config.PARARIUS_MONTHLY_INCOME,
            guarantor=config.PARARIUS_GUARANTOR,
            preferred_living_situation=config.PARARIUS_PREFERRED_LIVING_SITUATION,
            number_of_tenants=config.PARARIUS_NUMBER_OF_TENANTS,
            pets=config.PARARIUS_PETS,
            rent_start_date=config.PARARIUS_RENT_START_DATE,
            preferred_contract_period=config.PARARIUS_PREFERRED_CONTRACT_PERIOD,
            current_housing_situation=config.PARARIUS_CURRENT_HOUSING_SITUATION,
            headless=config.PARARIUS_BROWSER_HEADLESS,
            timeout_seconds=max(5, config.PARARIUS_BROWSER_TIMEOUT_SECONDS),
            storage_state_path=Path(config.PARARIUS_STORAGE_STATE_PATH).expanduser(),
            browser_fallback_enabled=config.PARARIUS_BROWSER_FALLBACK_ENABLED,
        )

    def ready_error(self) -> str | None:
        if not self.enabled:
            return "Pararius auto-reply is disabled."
        if not self.email:
            return "PARARIUS_EMAIL is missing."
        if not self.dry_run and not self.password and not self.storage_state_path.exists():
            return "PARARIUS_PASSWORD is missing, or run scripts/pararius_save_session.py once."
        if not self.first_name:
            return "PARARIUS_FIRST_NAME is missing."
        if not self.last_name:
            return "PARARIUS_LAST_NAME is missing."
        if not self.phone_number:
            return "PARARIUS_PHONE_NUMBER is missing."
        if not self.message:
            return "PARARIUS_REPLY_MESSAGE is missing."
        return None


@dataclass(frozen=True)
class ParariusReplyResult:
    status: str
    detail: str = ""
    sent_at: datetime | None = None
    confirmation_at: datetime | None = None


@dataclass(frozen=True)
class ContactField:
    name: str
    field_type: str
    value: str
    key: str
    checked: bool = False


@dataclass(frozen=True)
class ContactForm:
    action_url: str
    method: str
    fields: tuple[ContactField, ...]


class ParariusReplier:
    def __init__(self, settings: ParariusReplySettings):
        self.settings = settings

    async def __aenter__(self) -> ParariusReplier:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def reply_to_listing(self, listing: Listing) -> ParariusReplyResult:
        contact_url = await self._resolve_contact_url(listing)
        if not contact_url:
            return ParariusReplyResult("missing_contact_data", "Pararius contact URL could not be found.")

        if self.settings.dry_run:
            return ParariusReplyResult(
                "dry_run_ready",
                "Pararius contact URL and reply settings are complete; submit was skipped.",
            )

        result = await self._submit_contact_request(listing, contact_url)
        if result.status in {"blocked", "login_required", "submit_failed", "error"} and self.settings.browser_fallback_enabled:
            fallback = await self._submit_with_browser(listing, contact_url)
            if fallback.status != "error":
                return fallback
            return ParariusReplyResult(
                result.status,
                f"{result.detail} Browser fallback also failed: {fallback.detail}",
                sent_at=result.sent_at,
            )
        return result

    async def _resolve_contact_url(self, listing: Listing) -> str:
        contact_url = listing.contact_url or listing.reply_data.get("contact_url", "")
        if contact_url:
            return urljoin(_BASE_URL, contact_url)

        html = await self._fetch_listing_html(listing)
        return _extract_contact_url(html, listing.url)

    async def _fetch_listing_html(self, listing: Listing) -> str:
        return await fetch_html(listing.url, _DEFAULT_HEADERS, source="pararius", timeout=self.settings.timeout_seconds)

    async def _submit_contact_request(self, listing: Listing, contact_url: str) -> ParariusReplyResult:
        try:
            if _USE_CURL and CurlAsyncSession is not None:
                return await self._submit_with_curl_fingerprints(listing, contact_url)

            async with httpx.AsyncClient(timeout=self.settings.timeout_seconds, follow_redirects=True) as session:
                return await self._submit_with_httpx_session(session, listing, contact_url)
        except httpx.HTTPError as exc:
            logger.exception("Pararius reply HTTP request failed for %s", listing.url)
            return ParariusReplyResult("error", str(exc))
        except Exception as exc:
            logger.exception("Pararius reply failed for %s", listing.url)
            return ParariusReplyResult("error", str(exc))

    async def _submit_with_curl_fingerprints(self, listing: Listing, contact_url: str) -> ParariusReplyResult:
        if CurlAsyncSession is None:
            return ParariusReplyResult("error", "curl_cffi is not available.")

        last_result: ParariusReplyResult | None = None
        for attempt, impersonate in enumerate(_PARARIUS_CURL_IMPERSONATIONS):
            async with CurlAsyncSession(impersonate=impersonate) as session:
                _load_storage_state_cookies(session, self.settings.storage_state_path)
                result = await self._submit_with_session(session, listing, contact_url)
            if result.status not in {"blocked", "login_required"}:
                if attempt:
                    logger.info(
                        "Pararius reply recovered with %s fingerprint after %d failed attempt(s).",
                        impersonate,
                        attempt,
                    )
                return result
            last_result = result
            logger.debug("Pararius reply failed with %s fingerprint: %s", impersonate, result.detail)

        return last_result or ParariusReplyResult("error", "No Pararius HTTP fingerprint produced a result.")

    async def _submit_with_session(self, session, listing: Listing, contact_url: str) -> ParariusReplyResult:
        response = await session.get(contact_url, headers=_DEFAULT_HEADERS, timeout=self.settings.timeout_seconds)
        html = response.text
        if _is_challenge(html):
            return ParariusReplyResult("blocked", "Pararius returned a Cloudflare challenge on contact page.")
        if _is_login_page(html, response.url):
            login_result = await self._login_with_session(session)
            if login_result:
                return login_result
            response = await session.get(contact_url, headers=_DEFAULT_HEADERS, timeout=self.settings.timeout_seconds)
            html = response.text
            if _is_challenge(html):
                return ParariusReplyResult("blocked", "Pararius returned a Cloudflare challenge after login.")
            if _is_login_page(html, response.url):
                return ParariusReplyResult("login_required", "Pararius login did not create an authenticated session.")

        form = _parse_contact_form(html, str(response.url))
        if not form:
            return _result_for_page_without_form(html, response.url)

        payload = _build_contact_payload(form, self.settings)
        post_headers = {
            **_DEFAULT_HEADERS,
            "Referer": str(response.url),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        submit = await session.post(
            form.action_url,
            headers=post_headers,
            data=payload,
            timeout=self.settings.timeout_seconds,
            allow_redirects=True,
        )
        return _response_to_result(submit.status_code, submit.text, str(submit.url))

    async def _login_with_session(self, session) -> ParariusReplyResult | None:
        if not self.settings.password:
            return ParariusReplyResult("login_required", "Pararius needs a saved session or password login.")

        login_page = await session.get(_LOGIN_URL, headers=_DEFAULT_HEADERS, timeout=self.settings.timeout_seconds)
        if _is_challenge(login_page.text):
            return ParariusReplyResult("blocked", "Pararius returned a Cloudflare challenge on login page.")

        soup = BeautifulSoup(login_page.text, "lxml")
        token = soup.select_one("input[name='_token']")
        data = {
            "_token": token.get("value", "") if token else "",
            "email": self.settings.email,
            "password": self.settings.password,
        }
        response = await session.post(
            _LOGIN_URL,
            headers={**_DEFAULT_HEADERS, "Referer": _LOGIN_URL, "Content-Type": "application/x-www-form-urlencoded"},
            data=data,
            timeout=self.settings.timeout_seconds,
            allow_redirects=True,
        )
        if _is_challenge(response.text):
            return ParariusReplyResult("blocked", "Pararius returned a Cloudflare challenge on login submit.")
        if _is_login_page(response.text, response.url):
            return ParariusReplyResult("login_required", "Pararius rejected the configured email/password.")
        return None

    async def _submit_with_httpx_session(
        self,
        session: httpx.AsyncClient,
        listing: Listing,
        contact_url: str,
    ) -> ParariusReplyResult:
        _load_storage_state_cookies(session, self.settings.storage_state_path)
        response = await session.get(contact_url, headers=_DEFAULT_HEADERS)
        html = response.text
        if _is_challenge(html):
            return ParariusReplyResult("blocked", "Pararius returned a Cloudflare challenge on contact page.")
        if _is_login_page(html, response.url):
            return ParariusReplyResult("login_required", "Pararius needs curl_cffi or saved browser cookies to log in.")

        form = _parse_contact_form(html, str(response.url))
        if not form:
            return _result_for_page_without_form(html, response.url)

        payload = _build_contact_payload(form, self.settings)
        submit = await session.post(
            form.action_url,
            headers={**_DEFAULT_HEADERS, "Referer": str(response.url)},
            data=payload,
        )
        return _response_to_result(submit.status_code, submit.text, str(submit.url))

    async def _submit_with_browser(self, listing: Listing, contact_url: str) -> ParariusReplyResult:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=self.settings.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context_kwargs = {"locale": "nl-NL", "timezone_id": "Europe/Amsterdam"}
            if self.settings.storage_state_path.exists():
                context_kwargs["storage_state"] = str(self.settings.storage_state_path)
            context = await browser.new_context(**context_kwargs)
            page = await context.new_page()
            page.set_default_timeout(self.settings.timeout_seconds * 1000)
            try:
                await page.goto(contact_url, wait_until="domcontentloaded")
                await _browser_accept_cookies(page)
                if _is_challenge(await _browser_page_text(page)):
                    return ParariusReplyResult("blocked", "Pararius browser fallback opened a Cloudflare challenge.")
                if await _browser_is_login_page(page):
                    if not self.settings.password:
                        return ParariusReplyResult("login_required", "Pararius browser fallback needs a saved session or password.")
                    await _browser_login(page, self.settings)
                    await page.goto(contact_url, wait_until="domcontentloaded")
                if await _browser_is_login_page(page):
                    return ParariusReplyResult("login_required", "Pararius browser fallback could not log in.")
                await _browser_fill_contact_form(page, self.settings)
                await _browser_submit_contact_form(page)
                body = await page.locator("body").inner_text(timeout=5000)
                if _SUCCESS_RE.search(body):
                    return ParariusReplyResult(
                        "sent",
                        "Pararius browser fallback reported the response as sent.",
                        sent_at=datetime.now(timezone.utc),
                    )
                if _ERROR_RE.search(body):
                    return ParariusReplyResult("validation_failed", _trim_detail(body))
                reaction_detail = await _browser_reaction_detail(page, listing)
                if reaction_detail:
                    return ParariusReplyResult(
                        "sent",
                        reaction_detail,
                        sent_at=datetime.now(timezone.utc),
                    )
                return ParariusReplyResult(
                    "submitted_unconfirmed",
                    "Pararius browser fallback submitted the form; no confirmation text was found.",
                    sent_at=datetime.now(timezone.utc),
                )
            except PlaywrightTimeoutError as exc:
                return ParariusReplyResult("error", f"Browser fallback timed out: {exc}")
            finally:
                await browser.close()


def _extract_contact_url(html: str, page_url: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for link in soup.select("a[href]"):
        href = (link.get("href") or "").strip()
        text = link.get_text(" ", strip=True).casefold()
        if "meld-een-probleem" in href or "report" in text:
            continue
        if _CONTACT_HREF_RE.match(href):
            return urljoin(page_url, href)

    listing_id = soup.select_one("input[name='clickout_contact_form[listing_id]']")
    if listing_id and _UUID_RE.match(listing_id.get("value", "")):
        return urljoin(_BASE_URL, f"/contact/{listing_id.get('value')}")
    return ""


def _parse_contact_form(html: str, page_url: str) -> ContactForm | None:
    soup = BeautifulSoup(html, "lxml")
    forms = soup.select("form")
    best_form = None
    best_score = 0
    for form in forms:
        score = _contact_form_score(form)
        if score > best_score:
            best_form = form
            best_score = score
    if best_form is None:
        return None

    fields: list[ContactField] = []
    for field in best_form.select("input, textarea, select"):
        name = (field.get("name") or "").strip()
        if not name:
            continue
        field_type = (field.get("type") or field.name or "text").casefold()
        if field_type in {"submit", "button", "reset", "file"}:
            continue
        fields.append(
            ContactField(
                name=name,
                field_type=field_type,
                value=_field_value(field),
                key=_field_key(field),
                checked=field.has_attr("checked"),
            )
        )

    action = best_form.get("action") or page_url
    method = (best_form.get("method") or "post").casefold()
    return ContactForm(action_url=urljoin(page_url, action), method=method, fields=tuple(fields))


def _contact_form_score(form) -> int:
    action = (form.get("action") or "").casefold()
    classes = " ".join(form.get("class") or ()).casefold()
    text = form.get_text(" ", strip=True).casefold()
    names = " ".join((field.get("name") or "") for field in form.select("input, textarea, select")).casefold()
    haystack = f"{action} {classes} {text} {names}"

    if any(skip in haystack for skip in ("manual-search", "favourite", "inloggen", "login-email")):
        return 0

    score = 0
    if "/contact" in action:
        score += 5
    if any(word in haystack for word in ("contact", "makelaar", "bericht", "message", "reactie", "motivation")):
        score += 4
    if any(pattern.search(haystack) for pattern in _FIELD_HINTS.values()):
        score += 3
    if "clickout_contact_form" in names:
        score += 1
    return score


def _field_value(field) -> str:
    if field.name == "textarea":
        return field.get_text()
    if field.name == "select":
        selected = field.select_one("option[selected]") or field.select_one("option")
        return selected.get("value", selected.get_text(" ", strip=True)) if selected else ""
    return field.get("value", "")


def _field_key(field) -> str:
    parts = [
        field.get("name", ""),
        field.get("id", ""),
        field.get("placeholder", ""),
        field.get("aria-label", ""),
    ]
    labels = field.get("id") and field.find_parent().select(f"label[for='{field.get('id')}']")
    if labels:
        parts.extend(label.get_text(" ", strip=True) for label in labels)
    return " ".join(part for part in parts if part).casefold()


def _build_contact_payload(form: ContactForm, settings: ParariusReplySettings) -> dict[str, str]:
    payload: dict[str, str] = {}
    filled_full_name = False
    for field in form.fields:
        profile_value = _pararius_profile_value(field, settings)
        if profile_value is not None:
            payload[field.name] = profile_value
            continue

        value = field.value
        if field.field_type in {"checkbox", "radio"}:
            if field.checked or _ACCEPTANCE_RE.search(field.key):
                value = value or "on"
            else:
                continue
        elif _FIELD_HINTS["email"].search(field.key):
            value = settings.email
        elif _FIELD_HINTS["first_name"].search(field.key):
            value = settings.first_name
        elif _FIELD_HINTS["last_name"].search(field.key):
            value = settings.last_name
        elif _FIELD_HINTS["phone"].search(field.key):
            value = settings.phone_number
        elif _FIELD_HINTS["message"].search(field.key):
            value = settings.message
        elif _FIELD_HINTS["full_name"].search(field.key) and not filled_full_name:
            value = f"{settings.first_name} {settings.last_name}".strip()
            filled_full_name = True

        payload[field.name] = value
    return payload


def _pararius_profile_value(field: ContactField, settings: ParariusReplySettings) -> str | None:
    name = field.name.casefold()
    if "contact_agent_huurprofiel_form[" not in name:
        return None

    if "[_token]" in name:
        return field.value
    if "[motivation]" in name:
        return settings.message
    if "[salutation]" in name:
        return _mapped_choice(settings.salutation, {"male": "0", "heer": "0", "female": "1", "mevrouw": "1"}, "0")
    if "[first_name]" in name:
        return settings.first_name
    if "[last_name]" in name:
        return settings.last_name
    if "[phone_number]" in name:
        return settings.phone_number
    if "[date_of_birth]" in name:
        return _iso_date(settings.date_of_birth)
    if "[work_situation]" in name:
        return _mapped_choice(
            settings.work_situation,
            {
                "employed": "1",
                "employee": "1",
                "werkzaam": "1",
                "entrepreneur": "2",
                "zzp": "2",
                "student": "3",
                "gepensioneerd": "4",
                "retired": "4",
                "unemployed": "5",
                "niet werkzaam": "5",
            },
            "3",
        )
    if "[gross_annual_household_income]" in name:
        return _monthly_income_bucket(settings.monthly_income)
    if "[guarantor]" in name:
        return _mapped_choice(
            settings.guarantor,
            {
                "none": "1",
                "no": "1",
                "geen": "1",
                "netherlands": "2",
                "nl": "2",
                "dutch": "2",
                "abroad": "3",
                "foreign": "3",
                "buitenland": "3",
                "italy": "3",
            },
            "3",
        )
    if "[preferred_living_situation]" in name:
        return _mapped_choice(
            settings.preferred_living_situation,
            {
                "alone": "1",
                "single": "1",
                "no": "1",
                "nee": "1",
                "partner": "2",
                "family": "3",
                "friends": "4",
                "friend": "4",
            },
            "1",
        )
    if "[number_of_tenants]" in name:
        return _digits_or_default(settings.number_of_tenants, "1")
    if "[pets]" in name:
        return _mapped_choice(
            settings.pets,
            {"yes": "1", "ja": "1", "true": "1", "1": "1", "no": "0", "nee": "0", "false": "0", "0": "0"},
            "0",
        )
    if "[rent_start_date]" in name:
        return _iso_date(settings.rent_start_date)
    if "[preferred_contract_period]" in name:
        return _mapped_choice(
            settings.preferred_contract_period,
            {
                "indefinite": "1",
                "onbepaalde tijd": "1",
                "0-3": "2",
                "0 - 3": "2",
                "3-6": "3",
                "3 - 6": "3",
                "6-12": "4",
                "6 - 12": "4",
                "1 year": "5",
                "1-2": "5",
                "1 - 2": "5",
                "2 years": "5",
            },
            "5",
        )
    if "[current_housing_situation]" in name:
        return _mapped_choice(
            settings.current_housing_situation,
            {
                "renting": "i_rent_a_roof",
                "rent": "i_rent_a_roof",
                "huur": "i_rent_a_roof",
                "owning": "i_own_a_roof",
                "own": "i_own_a_roof",
                "inwonend": "i_dont_rent_or_own_a_roof_yet",
                "living with": "i_dont_rent_or_own_a_roof_yet",
            },
            "i_rent_a_roof",
        )
    return None


def _mapped_choice(raw_value: str, choices: dict[str, str], default: str) -> str:
    value = " ".join((raw_value or "").strip().casefold().split())
    if not value:
        return default
    if value in choices.values():
        return value
    for key, mapped in choices.items():
        if key in value:
            return mapped
    return default


def _monthly_income_bucket(raw_value: str) -> str:
    digits = _digits_or_default(raw_value, "")
    if not digits:
        return ""
    income = int(digits)
    if income <= 0:
        return "(,0]"
    if income >= 10000:
        return "[10000,)"
    lower = ((income - 1) // 500) * 500
    upper = lower + 500
    return f"[{lower},{upper}]"


def _digits_or_default(raw_value: str, default: str) -> str:
    digits = re.sub(r"\D", "", raw_value or "")
    return digits or default


def _iso_date(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if not value:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def _response_to_result(status_code: int, html: str, url: str) -> ParariusReplyResult:
    if _is_challenge(html):
        return ParariusReplyResult("blocked", "Pararius returned a Cloudflare challenge on submit.")
    if status_code in {401, 403, 429}:
        return ParariusReplyResult("blocked", f"Pararius submit was blocked with HTTP {status_code}.")
    if status_code in {400, 422}:
        return ParariusReplyResult("validation_failed", _trim_detail(_page_text(html)))
    if not 200 <= status_code < 400:
        return ParariusReplyResult("submit_failed", f"{status_code}: {_trim_detail(_page_text(html))}")

    text = _page_text(html)
    if _is_login_page(html, url):
        return ParariusReplyResult("login_required", "Pararius redirected to login during submit.")
    if _ERROR_RE.search(text) and not _SUCCESS_RE.search(text):
        return ParariusReplyResult("validation_failed", _trim_detail(text))
    if _SUCCESS_RE.search(text):
        return ParariusReplyResult("sent", "Pararius reported the response as sent.", sent_at=datetime.now(timezone.utc))
    return ParariusReplyResult(
        "submitted_unconfirmed",
        "Pararius accepted the submit response, but no confirmation text was found.",
        sent_at=datetime.now(timezone.utc),
    )


def _result_for_page_without_form(html: str, url: object) -> ParariusReplyResult:
    text = _page_text(html)
    if _is_login_page(html, url):
        return ParariusReplyResult("login_required", "Pararius contact page requires login.")
    if _SUCCESS_RE.search(text):
        return ParariusReplyResult("sent", "Pararius page already shows a sent response.", sent_at=datetime.now(timezone.utc))
    return ParariusReplyResult("missing_contact_data", "Pararius contact form could not be found.")


def _load_storage_state_cookies(session, path: Path) -> None:
    if not path.exists():
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read Pararius storage state %s: %s", path, exc)
        return
    for cookie in state.get("cookies", []):
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        domain = cookie.get("domain") or ".pararius.nl"
        path_value = cookie.get("path") or "/"
        try:
            session.cookies.set(name, value, domain=domain, path=path_value)
        except Exception:
            logger.debug("Could not import Pararius cookie %s into HTTP session.", name)


def _is_challenge(html: str) -> bool:
    return bool(_CHALLENGE_RE.search(html or ""))


def _is_login_page(html: str, url: object) -> bool:
    url_text = str(url).casefold()
    if "/inloggen" in url_text or "/login" in url_text:
        return True
    soup = BeautifulSoup(html or "", "lxml")
    has_login_form = soup.select_one("form.form--login-email") or soup.select_one("input[name='password']")
    h1 = soup.select_one("h1")
    return bool(has_login_form and h1 and _LOGIN_PAGE_RE.search(h1.get_text(" ", strip=True)))


def _page_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    return soup.get_text(" ", strip=True)


def _trim_detail(detail: str, limit: int = 1000) -> str:
    return " ".join((detail or "").split())[:limit]


async def _browser_accept_cookies(page) -> None:
    for pattern in (re.compile("akkoord", re.I), re.compile("accept", re.I)):
        try:
            button = page.get_by_role("button", name=pattern).first
            if await button.is_visible(timeout=1500):
                await button.click()
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue


async def _browser_is_login_page(page) -> bool:
    if "inloggen" in page.url.casefold() or "login" in page.url.casefold():
        return True
    try:
        return await page.locator("form.form--login-email, input[name='password']").count() > 0
    except Exception:
        return False


async def _browser_login(page, settings: ParariusReplySettings) -> None:
    await _browser_accept_cookies(page)
    await page.locator("input[name='email']").first.fill(settings.email)
    await page.locator("input[name='password']").first.fill(settings.password)
    await page.get_by_role("button", name=re.compile(r"^inloggen$|^log in$|^login$", re.I)).last.click()
    await _browser_wait_for_quiet(page)


async def _browser_fill_contact_form(page, settings: ParariusReplySettings) -> None:
    html = await page.content()
    form = _parse_contact_form(html, page.url)
    if form:
        payload = _build_contact_payload(form, settings)
        for field in form.fields:
            if field.name not in payload:
                continue
            locator = page.locator(f'[name="{_css_string(field.name)}"]').first
            if not await locator.count():
                continue
            value = payload[field.name]
            if field.field_type in {"checkbox", "radio"}:
                await locator.evaluate(
                    """(el, checked) => {
                        el.checked = Boolean(checked);
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    bool(value),
                )
            else:
                await locator.evaluate(
                    """(el, value) => {
                        el.value = value;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    value,
                )
        return

    await _fill_first_visible(page, "input[type='email'], input[name*='email' i]", settings.email)
    await _fill_first_visible(page, "input[name*='first' i], input[name*='voornaam' i]", settings.first_name)
    await _fill_first_visible(page, "input[name*='last' i], input[name*='achternaam' i]", settings.last_name)
    await _fill_first_visible(page, "input[name*='phone' i], input[name*='telefoon' i], input[type='tel']", settings.phone_number)
    await _fill_first_visible(page, "textarea, input[name*='message' i], input[name*='bericht' i]", settings.message)


async def _fill_first_visible(page, selector: str, value: str) -> None:
    locator = page.locator(selector)
    count = await locator.count()
    for index in range(count):
        field = locator.nth(index)
        try:
            if await field.is_visible(timeout=1000) and await field.is_enabled():
                current = await field.input_value()
                if not current:
                    await field.fill(value)
                return
        except Exception:
            continue


async def _browser_submit_contact_form(page) -> None:
    before_url = page.url
    submit = page.locator("form button[type='submit'], form input[type='submit'], button[type='submit'], input[type='submit']").last
    if await submit.count():
        await submit.scroll_into_view_if_needed()
        try:
            await submit.click(timeout=5000)
        except Exception:
            await submit.evaluate("(el) => el.click()")
        await _browser_wait_for_quiet(page)
        return

    button = page.get_by_role("button", name=re.compile(r"(verstuur|verzenden|reageer|send|submit|contact)", re.I)).last
    await button.click()
    await _browser_wait_for_quiet(page)
    try:
        await page.wait_for_url(lambda url: url != before_url, timeout=5000)
    except PlaywrightTimeoutError:
        pass


async def _browser_wait_for_quiet(page) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10_000)
    except PlaywrightTimeoutError:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=5_000)
    except PlaywrightTimeoutError:
        pass
    await page.wait_for_timeout(700)


async def _browser_page_text(page) -> str:
    try:
        return await page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


async def _browser_reaction_detail(page, listing: Listing) -> str:
    try:
        await page.goto(f"{_BASE_URL}/profiel/reacties", wait_until="domcontentloaded")
        await _browser_wait_for_quiet(page)
        body = await _browser_page_text(page)
    except Exception:
        return ""

    normalized_body = _compact_match_text(body)
    if not normalized_body:
        return ""
    markers = [
        listing.title,
        listing.address,
        listing.url.rstrip("/").split("/")[-1].replace("-", " "),
    ]
    if not any(_compact_match_text(marker) in normalized_body for marker in markers if _compact_match_text(marker)):
        return ""

    rank_match = re.search(r"#\s*\d+\s+van\s+\d+", body, re.I)
    if rank_match:
        return f"Pararius reactions dashboard shows the reply ({rank_match.group(0)})."
    return "Pararius reactions dashboard shows the reply."


def _compact_match_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _css_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
