from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from scrapers.base import Listing

logger = logging.getLogger(__name__)

_KAMERNET_API_BASE = "https://kamernet.nl"
_KAMERNET_AUTH_REFRESH_URL = f"{_KAMERNET_API_BASE}/en"
_KAMERNET_LISTING_REACTION_URL = f"{_KAMERNET_API_BASE}/services/api/conversation/listing-reaction"
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_PERSISTED_AUTH_COOKIE_NAMES = {
    "__ha_at",
    "__ha_rt",
    "__ha_rtp",
    "ASP.NET_SessionId",
    "USER_EMAIL",
    "logonUser",
}

KAMERNET_REPLY_SENT_STATUSES = {
    "sent",
    "submitted_unconfirmed",
    "confirmation_confirmed",
    "confirmation_error",
    "confirmation_missing",
    "sent_preapplication_pending",
    "sent_preapplication_failed",
    "preapplication_confirmed",
    "preapplication_confirmation_missing",
    "preapplication_sent",
    "preapplication_submitted_unconfirmed",
}

_CONTACT_BUTTON_RE = re.compile(r"\b(contact landlord|respond|apply|reageer)\b", re.I)
_LOGIN_LINK_RE = re.compile(r"^log in$", re.I)
_LOGIN_BUTTON_RE = re.compile(r"^(log in|login|sign in)$", re.I)
_SUBMIT_BUTTON_RE = re.compile(
    r"^(send|send message|send reply|submit|apply|verstuur|bericht versturen|reageer)$",
    re.I,
)
_MESSAGE_FIELD_RE = re.compile(r"(message|bericht|motivation|introduce|reaction|response)", re.I)
_SUCCESS_RE = re.compile(
    r"(message sent|sent successfully|your message has been sent|continue conversation|bericht verzonden|reactie verzonden)",
    re.I,
)
_VERIFICATION_RE = re.compile(
    r"(captcha|verify that you are human|verification code|two-factor|two factor|2fa)",
    re.I,
)
_LOGIN_ERROR_RE = re.compile(
    r"(invalid|incorrect|wrong password|try again|failed|error|could not log|cannot log)",
    re.I,
)
_SUBMIT_ERROR_RE = re.compile(
    r"(required|try again|failed|error|something went wrong|could not send)",
    re.I,
)


@dataclass(frozen=True)
class KamernetReplySettings:
    enabled: bool
    dry_run: bool
    email: str
    password: str
    message: str
    max_per_scan: int
    expected_tenancy_duration: str
    expected_move_date: str
    date_of_birth: str
    expected_tenancy_duration_id: int
    gender_id: int
    status_id: int
    languages_spoken_ids: tuple[int, ...]
    has_pet: bool
    people_moving_in: int
    tenant_language_id: int
    headless: bool
    timeout_seconds: int
    api_reply_enabled: bool
    storage_state_path: Path

    @classmethod
    def from_config(cls) -> KamernetReplySettings:
        import config

        return cls(
            enabled=config.KAMERNET_AUTO_REPLY_ENABLED,
            dry_run=config.KAMERNET_REPLY_DRY_RUN,
            email=config.KAMERNET_EMAIL.strip(),
            password=config.KAMERNET_PASSWORD,
            message=config.KAMERNET_REPLY_MESSAGE.strip(),
            max_per_scan=config.KAMERNET_REPLY_MAX_PER_SCAN,
            expected_tenancy_duration=config.KAMERNET_EXPECTED_TENANCY_DURATION,
            expected_move_date=config.KAMERNET_EXPECTED_MOVE_DATE,
            date_of_birth=config.KAMERNET_DATE_OF_BIRTH,
            expected_tenancy_duration_id=config.KAMERNET_EXPECTED_TENANCY_DURATION_ID,
            gender_id=config.KAMERNET_GENDER_ID,
            status_id=config.KAMERNET_STATUS_ID,
            languages_spoken_ids=config.KAMERNET_LANGUAGES_SPOKEN_IDS,
            has_pet=config.KAMERNET_HAS_PET,
            people_moving_in=config.KAMERNET_PEOPLE_MOVING_IN,
            tenant_language_id=config.KAMERNET_TENANT_LANGUAGE_ID,
            headless=config.KAMERNET_BROWSER_HEADLESS,
            timeout_seconds=max(5, config.KAMERNET_BROWSER_TIMEOUT_SECONDS),
            api_reply_enabled=config.KAMERNET_API_REPLY_ENABLED,
            storage_state_path=Path(config.KAMERNET_STORAGE_STATE_PATH).expanduser(),
        )

    def ready_error(self) -> str | None:
        if not self.enabled:
            return "Kamernet auto-reply is disabled."
        if not self.message:
            return "KAMERNET_REPLY_MESSAGE is missing."
        if self.storage_state_path.exists():
            return None
        if not self.email:
            return "KAMERNET_EMAIL is missing."
        if not self.password:
            return "KAMERNET_PASSWORD is missing, or run scripts/kamernet_save_session.py once."
        return None


@dataclass(frozen=True)
class KamernetReplyResult:
    status: str
    detail: str = ""
    sent_at: datetime | None = None
    confirmation_at: datetime | None = None


def should_skip_existing_reply(existing_reply: dict | None, requested_dry_run: bool) -> bool:
    if not existing_reply:
        return False

    status = existing_reply.get("status")
    if status in KAMERNET_REPLY_SENT_STATUSES:
        return True

    if requested_dry_run and existing_reply.get("dry_run") and status == "dry_run_ready":
        return True

    return False


class KamernetReplier:
    def __init__(self, settings: KamernetReplySettings):
        self.settings = settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> KamernetReplier:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.settings.headless)

        context_kwargs = {
            "locale": "en-US",
            "timezone_id": "Europe/Amsterdam",
            "viewport": {"width": 1280, "height": 1200},
        }
        if self.settings.storage_state_path.exists():
            context_kwargs["storage_state"] = str(self.settings.storage_state_path)

        try:
            self._context = await self._browser.new_context(**context_kwargs)
        except Exception as exc:
            logger.warning("Kamernet storage state could not be loaded, starting clean: %s", exc)
            context_kwargs.pop("storage_state", None)
            self._context = await self._browser.new_context(**context_kwargs)

        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def reply_to_listing(self, listing: Listing) -> KamernetReplyResult:
        if not self._context:
            raise RuntimeError("KamernetReplier must be used as an async context manager.")

        page = await self._context.new_page()
        page.set_default_timeout(self.settings.timeout_seconds * 1000)
        try:
            if not self.settings.dry_run and self.settings.api_reply_enabled:
                direct_result = await self._submit_with_direct_api(listing)
                if direct_result.status == "sent":
                    return direct_result
                logger.warning(
                    "Kamernet direct API submit failed for %s (%s); falling back to browser preparation.",
                    listing.url,
                    direct_result.detail,
                )

            prepared = await self._prepare_reply_form(page, listing)
            if isinstance(prepared, KamernetReplyResult):
                return prepared
            submit_button = prepared

            if self.settings.dry_run:
                return KamernetReplyResult("dry_run_ready", "Message field was found and filled; submit was skipped.")

            if self.settings.api_reply_enabled:
                api_result = await self._submit_with_captured_api(page, submit_button)
                if api_result.status == "sent":
                    return api_result
                logger.warning(
                    "Kamernet API submit failed for %s (%s); falling back to browser submit.",
                    listing.url,
                    api_result.detail,
                )
                browser_result = await self._submit_with_browser_fallback(page, listing, api_result)
                return browser_result

            return await _click_browser_submit(page, submit_button)
        except PlaywrightTimeoutError as exc:
            return KamernetReplyResult("timeout", str(exc))
        except Exception as exc:
            logger.exception("Kamernet reply failed for %s", listing.url)
            return KamernetReplyResult("error", str(exc))
        finally:
            await page.close()

    async def _submit_with_direct_api(self, listing: Listing) -> KamernetReplyResult:
        if not self._context:
            return KamernetReplyResult("api_unavailable", "Kamernet browser context is not available.")
        if not self.settings.storage_state_path.exists():
            return KamernetReplyResult("api_unavailable", "Saved Kamernet session is missing.")

        payload, payload_error = _build_direct_api_payload(listing, self.settings)
        if payload_error:
            return KamernetReplyResult("api_unavailable", payload_error)

        try:
            context_cookies = await self._context.cookies(["https://kamernet.nl", "https://id.kamernet.nl"])
            async with httpx.AsyncClient(
                timeout=self.settings.timeout_seconds,
                follow_redirects=True,
                headers={
                    "User-Agent": _DEFAULT_USER_AGENT,
                    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
                },
            ) as client:
                _copy_cookies(client, context_cookies)
                access_token = _cookie_value(client.cookies.jar, "__ha_at")
                if not access_token:
                    refresh_response = await client.get(_KAMERNET_AUTH_REFRESH_URL)
                    if refresh_response.status_code >= 400:
                        detail = _trim_detail(refresh_response.text, self.settings)
                        return KamernetReplyResult(
                            "api_auth_failed",
                            f"Kamernet auth refresh failed ({refresh_response.status_code}): {detail}",
                        )
                    _persist_storage_cookies(self.settings.storage_state_path, client.cookies.jar)
                    access_token = _cookie_value(client.cookies.jar, "__ha_at")

                if not access_token:
                    return KamernetReplyResult(
                        "api_unavailable",
                        "Kamernet auth refresh did not return an access token.",
                    )

                response = await client.post(
                    _KAMERNET_LISTING_REACTION_URL,
                    headers={
                        "accept": "application/json, text/plain, */*",
                        "authorization": f"Bearer {access_token}",
                        "content-type": "application/json",
                        "origin": _KAMERNET_API_BASE,
                        "referer": listing.url,
                    },
                    json=payload,
                )
                _persist_storage_cookies(self.settings.storage_state_path, client.cookies.jar)
        except httpx.HTTPError as exc:
            return KamernetReplyResult("api_error", str(exc))
        except OSError as exc:
            return KamernetReplyResult("api_unavailable", f"Could not read or update Kamernet session: {exc}")

        return _api_response_result(response, self.settings, "direct listing-reaction API request")

    async def _prepare_reply_form(self, page: Page, listing: Listing) -> Locator | KamernetReplyResult:
        await page.goto(listing.url, wait_until="domcontentloaded", timeout=self.settings.timeout_seconds * 1000)
        await _accept_cookies(page)

        if await _needs_manual_verification(page):
            return KamernetReplyResult("needs_verification", "Kamernet asked for manual verification.")

        opened = await _open_contact_form(page)
        if not opened:
            return KamernetReplyResult("contact_button_not_found", "Could not find the Contact landlord button.")
        await _accept_cookies(page)

        if await _is_auth_page(page):
            login_result = await self._login(page)
            if login_result:
                return login_result
            await self._save_storage_state()
            await page.goto(listing.url, wait_until="domcontentloaded", timeout=self.settings.timeout_seconds * 1000)
            await _accept_cookies(page)
            opened = await _open_contact_form(page)
            if not opened:
                return KamernetReplyResult(
                    "contact_button_not_found",
                    "Logged in, but could not reopen the Contact landlord form.",
                )
            await _accept_cookies(page)

        if await _needs_manual_verification(page):
            return KamernetReplyResult("needs_verification", "Kamernet asked for manual verification.")

        await _fill_structured_answers(page, self.settings)

        message_input = await _find_message_input(page, wait_for_visible=True)
        if not message_input:
            return KamernetReplyResult(
                "message_field_not_found",
                "Contact form opened, but no message field was detected.",
            )

        await message_input.fill(self.settings.message)
        submit_button = await _first_visible(page.get_by_role("button", name=_SUBMIT_BUTTON_RE))
        if not submit_button:
            return KamernetReplyResult("submit_button_not_found", "No safe send button was detected.")
        return submit_button

    async def _submit_with_captured_api(self, page: Page, submit_button: Locator) -> KamernetReplyResult:
        if not self._context:
            return KamernetReplyResult("api_unavailable", "Kamernet browser context is not available.")

        captured_request: dict | None = None
        captured = asyncio.Event()
        context_cookies = await self._context.cookies(["https://kamernet.nl", "https://id.kamernet.nl"])

        async def route_handler(route) -> None:
            nonlocal captured_request
            request = route.request
            if captured.is_set():
                try:
                    await route.abort()
                except Exception:
                    pass
                return
            captured_request = {
                "method": request.method,
                "url": request.url,
                "headers": dict(request.headers),
                "post_data": request.post_data,
            }
            captured.set()
            try:
                await route.abort()
            except Exception as exc:
                logger.debug("Kamernet submit route was already handled: %s", exc)

        route_pattern = "**/services/api/conversation/listing-reaction**"
        await self._context.route(route_pattern, route_handler)
        try:
            await submit_button.click()
            try:
                await asyncio.wait_for(captured.wait(), timeout=min(10, self.settings.timeout_seconds))
            except asyncio.TimeoutError:
                return KamernetReplyResult(
                    "api_unavailable",
                    "Kamernet did not emit the listing-reaction API request after submit.",
                )
        finally:
            try:
                await self._context.unroute(route_pattern, route_handler)
            except Exception:
                pass

        if not captured_request:
            return KamernetReplyResult("api_unavailable", "Kamernet API request could not be captured.")

        return await self._send_captured_api_request(captured_request, context_cookies)

    async def _send_captured_api_request(
        self,
        captured_request: dict,
        context_cookies: list[dict] | None = None,
    ) -> KamernetReplyResult:
        if not self._context:
            return KamernetReplyResult("api_unavailable", "Kamernet browser context is not available.")

        headers = _safe_replay_headers(captured_request.get("headers") or {})
        post_data = captured_request.get("post_data") or ""
        if not headers.get("authorization"):
            return KamernetReplyResult("api_unavailable", "Kamernet submit request did not include an access token.")
        if not post_data:
            return KamernetReplyResult("api_unavailable", "Kamernet submit request did not include a JSON body.")

        try:
            json.loads(post_data)
        except json.JSONDecodeError:
            return KamernetReplyResult("api_unavailable", "Kamernet submit request body was not JSON.")

        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout_seconds, follow_redirects=True) as client:
                if context_cookies is None:
                    context_cookies = await self._context.cookies(["https://kamernet.nl", "https://id.kamernet.nl"])
                _copy_cookies(client, context_cookies)
                response = await client.request(
                    captured_request.get("method") or "POST",
                    captured_request["url"],
                    headers=headers,
                    content=post_data,
                )
        except httpx.HTTPError as exc:
            return KamernetReplyResult("api_error", str(exc))

        return _api_response_result(response, self.settings, "listing-reaction API request")

    async def _submit_with_browser_fallback(
        self,
        page: Page,
        listing: Listing,
        api_result: KamernetReplyResult,
    ) -> KamernetReplyResult:
        prepared = await self._prepare_reply_form(page, listing)
        if isinstance(prepared, KamernetReplyResult):
            return KamernetReplyResult(
                prepared.status,
                f"API submit failed first ({api_result.status}: {api_result.detail}); "
                f"browser fallback could not prepare the form: {prepared.detail}",
            )

        browser_result = await _click_browser_submit(page, prepared)
        if browser_result.status in {"sent", "submitted_unconfirmed"}:
            return KamernetReplyResult(
                browser_result.status,
                f"Browser fallback after API failure ({api_result.status}): {browser_result.detail}",
            )
        return KamernetReplyResult(
            browser_result.status,
            f"API submit failed first ({api_result.status}: {api_result.detail}); "
            f"browser fallback result: {browser_result.detail}",
        )

    async def _login(self, page: Page) -> KamernetReplyResult | None:
        await _switch_signup_to_login(page)
        if not self.settings.email or not self.settings.password:
            return KamernetReplyResult(
                "login_required",
                "Saved Kamernet session is missing or expired; run scripts/kamernet_save_session.py again.",
            )

        email_field = await _first_visible(
            page.locator("input[type='email'], input[name*='email' i], input[placeholder*='email' i]")
        )
        password_field = await _first_visible(page.locator("input[type='password']"))
        if not email_field or not password_field:
            return KamernetReplyResult("login_form_not_found", "Kamernet login fields were not detected.")

        await email_field.fill(self.settings.email)
        await password_field.fill(self.settings.password)

        login_button = await _first_visible(page.get_by_role("button", name=_LOGIN_BUTTON_RE))
        if not login_button:
            return KamernetReplyResult("login_button_not_found", "Kamernet login button was not detected.")

        before_url = page.url
        await login_button.click()
        await _wait_for_url_change_or_quiet(page, before_url)

        if await _needs_manual_verification(page):
            return KamernetReplyResult("needs_verification", "Kamernet asked for manual verification during login.")

        if await _is_auth_page(page):
            body_text = await _body_text(page)
            if _LOGIN_ERROR_RE.search(body_text):
                return KamernetReplyResult("login_failed", "Kamernet rejected the login credentials.")
            return KamernetReplyResult("login_not_completed", "Kamernet stayed on the login page.")

        return None

    async def _save_storage_state(self) -> None:
        if not self._context:
            return
        self.settings.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        await self._context.storage_state(path=str(self.settings.storage_state_path))


async def _click_browser_submit(page: Page, submit_button: Locator) -> KamernetReplyResult:
    await submit_button.click()
    await _wait_for_quiet(page)
    sent_at = datetime.now(timezone.utc)
    body_text = await _body_text(page)
    if _SUCCESS_RE.search(body_text):
        return KamernetReplyResult("sent", "Kamernet showed a send confirmation.", sent_at=sent_at)
    if _SUBMIT_ERROR_RE.search(body_text):
        return KamernetReplyResult("submit_failed", "Kamernet showed an error after submit.")
    return KamernetReplyResult(
        "submitted_unconfirmed",
        "Submit was clicked, but no confirmation text was detected.",
        sent_at=sent_at,
    )


def _safe_replay_headers(headers: dict[str, str]) -> dict[str, str]:
    allowed = {
        "accept",
        "authorization",
        "content-type",
        "origin",
        "referer",
        "x-csrf-token",
        "x-requested-with",
        "x-xsrf-token",
    }
    replay_headers = {
        key: value
        for key, value in headers.items()
        if key.casefold() in allowed and value
    }
    replay_headers.setdefault(
        "User-Agent",
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    return replay_headers


def _copy_cookies(client: httpx.AsyncClient, cookies: list[dict]) -> None:
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        domain = (cookie.get("domain") or "kamernet.nl").lstrip(".")
        client.cookies.set(name, value, domain=domain, path=cookie.get("path") or "/")


def _api_response_result(
    response: httpx.Response,
    settings: KamernetReplySettings,
    action_label: str,
) -> KamernetReplyResult:
    detail = _trim_detail(response.text, settings)
    if 200 <= response.status_code < 300:
        return KamernetReplyResult(
            "sent",
            f"Kamernet accepted the {action_label} ({response.status_code}).",
            sent_at=datetime.now(timezone.utc),
        )
    if response.status_code in {400, 409} and re.search(r"(already|conversation|sent)", detail, re.I):
        return KamernetReplyResult(
            "sent",
            "Kamernet API says this listing already has a conversation.",
            sent_at=datetime.now(timezone.utc),
        )
    if response.status_code in {401, 403}:
        return KamernetReplyResult("api_auth_failed", f"{response.status_code}: {detail}")
    if response.status_code == 429:
        return KamernetReplyResult("api_rate_limited", f"429: {detail}")
    if response.status_code == 400:
        return KamernetReplyResult("api_validation_failed", detail)
    return KamernetReplyResult("api_submit_failed", f"{response.status_code}: {detail}")


def _build_direct_api_payload(
    listing: Listing,
    settings: KamernetReplySettings,
) -> tuple[dict | None, str]:
    listing_id = _numeric_listing_id(listing)
    if listing_id is None:
        return None, "Kamernet listing id is not numeric."

    duration_id = settings.expected_tenancy_duration_id or _expected_tenancy_duration_id(
        settings.expected_tenancy_duration
    )
    if not duration_id:
        return None, "KAMERNET_EXPECTED_TENANCY_DURATION_ID is missing and duration text is unknown."

    date_of_birth = _kamernet_api_datetime(settings.date_of_birth, prefer_month_first=False)
    if not date_of_birth:
        return None, "KAMERNET_DATE_OF_BIRTH is missing or invalid."

    expected_move_date = _kamernet_api_datetime(settings.expected_move_date, prefer_month_first=True)
    if not expected_move_date:
        return None, "KAMERNET_EXPECTED_MOVE_DATE is missing or invalid."

    if not settings.gender_id:
        return None, "KAMERNET_GENDER_ID is missing."
    if not settings.status_id:
        return None, "KAMERNET_STATUS_ID is missing."
    if not settings.languages_spoken_ids:
        return None, "KAMERNET_LANGUAGES_SPOKEN_IDS is missing."
    if not settings.people_moving_in:
        return None, "KAMERNET_PEOPLE_MOVING_IN is missing."
    if not settings.tenant_language_id:
        return None, "KAMERNET_TENANT_LANGUAGE_ID is missing."

    return (
        {
            "listingID": listing_id,
            "message": settings.message,
            "genderID": settings.gender_id,
            "dateOfBirth": date_of_birth,
            "expectedTenancyDurationID": duration_id,
            "statusID": settings.status_id,
            "languagesSpokenID": list(settings.languages_spoken_ids),
            "hasPet": settings.has_pet,
            "expectedMoveInDate": expected_move_date,
            "peopleMovingIn": settings.people_moving_in,
            "tenantLanguageID": settings.tenant_language_id,
        },
        "",
    )


def _numeric_listing_id(listing: Listing) -> int | None:
    for candidate in (listing.id, listing.url):
        match = re.search(r"(\d{5,})", str(candidate or ""))
        if match:
            return int(match.group(1))
    return None


def _expected_tenancy_duration_id(value: str) -> int | None:
    normalized = _normalize_text(value)
    mapping = {
        "less than 6 months": 1,
        "6 months": 2,
        "1 year": 3,
        "2 years or more": 4,
        "2 years": 4,
        "more than 2 years": 4,
    }
    return mapping.get(normalized)


def _kamernet_api_datetime(value: str, *, prefer_month_first: bool) -> str:
    parsed = _parse_date(value, prefer_month_first=prefer_month_first)
    if not parsed:
        return ""
    return f"{parsed.isoformat()}T20:00:00"


def _parse_date(value: str, *, prefer_month_first: bool) -> date | None:
    stripped = (value or "").strip()
    if not stripped:
        return None

    slash_formats = ["%m/%d/%Y", "%d/%m/%Y"] if prefer_month_first else ["%d/%m/%Y", "%m/%d/%Y"]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", *slash_formats):
        try:
            return datetime.strptime(stripped, fmt).date()
        except ValueError:
            continue
    return None


def _cookie_value(cookie_jar, name: str) -> str:
    for cookie in cookie_jar:
        if cookie.name == name and "kamernet.nl" in (cookie.domain or ""):
            return cookie.value
    return ""


def _persist_storage_cookies(storage_state_path: Path, cookie_jar) -> None:
    if not storage_state_path.exists():
        return

    try:
        state = json.loads(storage_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not load Kamernet storage state for cookie persistence.", exc_info=True)
        return

    cookies = state.setdefault("cookies", [])
    by_key = {
        (cookie.get("domain"), cookie.get("path") or "/", cookie.get("name")): cookie
        for cookie in cookies
        if isinstance(cookie, dict)
    }

    changed = False
    for jar_cookie in cookie_jar:
        if jar_cookie.name not in _PERSISTED_AUTH_COOKIE_NAMES:
            continue
        domain = jar_cookie.domain or "kamernet.nl"
        if "kamernet.nl" not in domain:
            continue
        key = (domain, jar_cookie.path or "/", jar_cookie.name)
        existing = by_key.get(key)
        cookie_data = {
            "name": jar_cookie.name,
            "value": jar_cookie.value,
            "domain": domain,
            "path": jar_cookie.path or "/",
            "expires": jar_cookie.expires if jar_cookie.expires is not None else -1,
            "httpOnly": bool(
                getattr(jar_cookie, "_rest", {}).get("HttpOnly")
                or getattr(jar_cookie, "_rest", {}).get("httponly")
            ),
            "secure": bool(jar_cookie.secure),
            "sameSite": "Lax",
        }
        if existing:
            cookie_data["httpOnly"] = existing.get("httpOnly", cookie_data["httpOnly"])
            cookie_data["secure"] = existing.get("secure", cookie_data["secure"])
            cookie_data["sameSite"] = existing.get("sameSite", cookie_data["sameSite"])
            if existing != cookie_data:
                existing.update(cookie_data)
                changed = True
        else:
            cookies.append(cookie_data)
            by_key[key] = cookie_data
            changed = True

    if changed:
        mode = storage_state_path.stat().st_mode
        temp_path = storage_state_path.with_suffix(storage_state_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(storage_state_path)
        storage_state_path.chmod(mode & 0o777)


def _trim_detail(detail: str, settings: KamernetReplySettings, limit: int = 1000) -> str:
    sanitized = detail or ""
    for secret in (settings.message, settings.email, settings.password):
        if secret:
            sanitized = sanitized.replace(secret, "<redacted>")
    return sanitized[:limit]


async def _accept_cookies(page: Page) -> None:
    for pattern in (re.compile(r"accept all", re.I), re.compile(r"accept", re.I)):
        for locator in (
            page.get_by_role("button", name=pattern),
            page.locator("button").filter(has_text=pattern),
        ):
            button = await _first_visible(locator)
            if button:
                await button.click()
                await page.wait_for_timeout(500)
                return


async def _open_contact_form(page: Page) -> bool:
    if await _find_message_input(page):
        return True

    button = await _first_visible(page.get_by_role("button", name=_CONTACT_BUTTON_RE))
    if not button:
        button = await _first_visible(page.get_by_role("link", name=_CONTACT_BUTTON_RE))
    if not button:
        button = await _first_visible(page.locator("a[href*='/react-to-listing/']"))
    if not button:
        return False

    await button.click()
    await _wait_for_quiet(page)
    return True


async def _switch_signup_to_login(page: Page) -> None:
    if "signup" not in page.url.casefold():
        return
    login_link = await _first_visible(page.get_by_role("link", name=_LOGIN_LINK_RE))
    if login_link:
        await login_link.click()
        await _wait_for_quiet(page)


async def _is_auth_page(page: Page) -> bool:
    url = page.url.casefold()
    if "id.kamernet.nl/login" in url or "id.kamernet.nl/signup" in url:
        return True

    auth_heading = page.get_by_role("heading", name=re.compile(r"(welcome back|create a free account)", re.I))
    return await _first_visible(auth_heading) is not None


async def _find_message_input(page: Page, wait_for_visible: bool = False) -> Locator | None:
    locators = (
        page.get_by_label(_MESSAGE_FIELD_RE),
        page.locator("#message"),
        page.locator("textarea[name='message']"),
        page.locator("textarea"),
        page.locator("[contenteditable='true']"),
    )

    for locator in locators:
        match = await _first_visible(locator)
        if match:
            return match

    if wait_for_visible:
        for locator in locators[1:]:
            match = await _first_visible_after_wait(locator)
            if match:
                return match

    return None


async def _first_visible_after_wait(locator: Locator, timeout_ms: int = 3000) -> Locator | None:
    try:
        first = locator.first
        await first.wait_for(state="visible", timeout=timeout_ms)
        return first
    except Exception:
        return await _first_visible(locator)

async def _fill_structured_answers(page: Page, settings: KamernetReplySettings) -> None:
    if settings.expected_tenancy_duration:
        await _select_mui_option(page, "expectedTenancyDurationID", settings.expected_tenancy_duration)

    if settings.expected_move_date:
        move_date_input = await _find_expected_move_date_input(page)
        if move_date_input:
            await move_date_input.fill(settings.expected_move_date)
            await move_date_input.press("Tab")


async def _select_mui_option(page: Page, combobox_id: str, option_text: str) -> bool:
    combobox = await _first_visible(page.locator(f"#{combobox_id}[role='combobox']"))
    if not combobox:
        return False

    try:
        current_text = (await combobox.inner_text()).strip()
    except Exception:
        current_text = ""
    if _normalize_text(current_text) == _normalize_text(option_text):
        return True

    await combobox.click()
    option = await _first_visible(page.get_by_role("option", name=re.compile(rf"^{re.escape(option_text)}$", re.I)))
    if not option:
        await page.keyboard.press("Escape")
        return False
    await option.click()
    await page.wait_for_timeout(300)
    return True


async def _find_expected_move_date_input(page: Page) -> Locator | None:
    date_inputs = page.locator("input[placeholder='MM/DD/YYYY']")
    try:
        count = await date_inputs.count()
    except Exception:
        return None
    if count >= 2:
        return date_inputs.nth(1)
    if count == 1:
        return date_inputs.first
    return None


async def _needs_manual_verification(page: Page) -> bool:
    return _VERIFICATION_RE.search(await _body_text(page)) is not None


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


async def _first_visible(locator: Locator, limit: int = 8) -> Locator | None:
    try:
        count = await locator.count()
    except Exception:
        return None

    for index in range(min(count, limit)):
        item = locator.nth(index)
        try:
            if await item.is_visible():
                return item
        except Exception:
            continue
    return None


async def _body_text(page: Page) -> str:
    try:
        return await page.locator("body").inner_text(timeout=2000)
    except Exception:
        return ""


async def _wait_for_quiet(page: Page) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10_000)
    except PlaywrightTimeoutError:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=5_000)
    except PlaywrightTimeoutError:
        pass
    await page.wait_for_timeout(700)


async def _wait_for_url_change_or_quiet(page: Page, before_url: str) -> None:
    try:
        await page.wait_for_url(lambda url: url != before_url, timeout=10_000)
    except PlaywrightTimeoutError:
        pass
    await _wait_for_quiet(page)
