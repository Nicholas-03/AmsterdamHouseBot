from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

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

KAMERNET_REPLY_SENT_STATUSES = {
    "sent",
    "submitted_unconfirmed",
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
    r"(message sent|sent successfully|your message has been sent|bericht verzonden|reactie verzonden)",
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
    headless: bool
    timeout_seconds: int
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
            headless=config.KAMERNET_BROWSER_HEADLESS,
            timeout_seconds=max(5, config.KAMERNET_BROWSER_TIMEOUT_SECONDS),
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
            await page.goto(listing.url, wait_until="domcontentloaded", timeout=self.settings.timeout_seconds * 1000)
            await _accept_cookies(page)

            if await _needs_manual_verification(page):
                return KamernetReplyResult("needs_verification", "Kamernet asked for manual verification.")

            opened = await _open_contact_form(page)
            if not opened:
                return KamernetReplyResult("contact_button_not_found", "Could not find the Contact landlord button.")

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

            if await _needs_manual_verification(page):
                return KamernetReplyResult("needs_verification", "Kamernet asked for manual verification.")

            await _fill_structured_answers(page, self.settings)

            message_input = await _find_message_input(page)
            if not message_input:
                return KamernetReplyResult(
                    "message_field_not_found",
                    "Contact form opened, but no message field was detected.",
                )

            await message_input.fill(self.settings.message)
            if self.settings.dry_run:
                return KamernetReplyResult("dry_run_ready", "Message field was found and filled; submit was skipped.")

            submit_button = await _first_visible(page.get_by_role("button", name=_SUBMIT_BUTTON_RE))
            if not submit_button:
                return KamernetReplyResult("submit_button_not_found", "No safe send button was detected.")

            await submit_button.click()
            await _wait_for_quiet(page)
            body_text = await _body_text(page)
            if _SUCCESS_RE.search(body_text):
                return KamernetReplyResult("sent", "Kamernet showed a send confirmation.")
            if _SUBMIT_ERROR_RE.search(body_text):
                return KamernetReplyResult("submit_failed", "Kamernet showed an error after submit.")
            return KamernetReplyResult(
                "submitted_unconfirmed",
                "Submit was clicked, but no confirmation text was detected.",
            )
        except PlaywrightTimeoutError as exc:
            return KamernetReplyResult("timeout", str(exc))
        except Exception as exc:
            logger.exception("Kamernet reply failed for %s", listing.url)
            return KamernetReplyResult("error", str(exc))
        finally:
            await page.close()

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


async def _accept_cookies(page: Page) -> None:
    for pattern in (re.compile(r"accept all", re.I), re.compile(r"accept", re.I)):
        button = await _first_visible(page.get_by_role("button", name=pattern))
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


async def _find_message_input(page: Page) -> Locator | None:
    label_match = await _first_visible(page.get_by_label(_MESSAGE_FIELD_RE))
    if label_match:
        return label_match

    textarea = await _first_visible(page.locator("textarea"))
    if textarea:
        return textarea

    editable = await _first_visible(page.locator("[contenteditable='true']"))
    if editable:
        return editable

    return None


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
