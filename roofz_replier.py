from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

from gmail_preapplication import (
    GmailPreApplicationSettings,
    build_gmail_service,
    find_unread_preapplication_messages,
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
    preapplication_poll_seconds: int
    preapplication_poll_interval_seconds: int
    gmail: GmailPreApplicationSettings
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
            preapplication_poll_seconds=config.ROOFZ_PREAPPLICATION_POLL_SECONDS,
            preapplication_poll_interval_seconds=max(1, config.ROOFZ_PREAPPLICATION_POLL_INTERVAL_SECONDS),
            gmail=GmailPreApplicationSettings.from_config(),
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
            return self.gmail.ready_error()
        return None


@dataclass(frozen=True)
class RoofzReplyResult:
    status: str
    detail: str = ""


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

        response_result = await self._send_initial_interest(listing, payload)
        if response_result.status != "sent":
            return response_result

        if not self.settings.preapplication_enabled:
            return response_result

        return await self._complete_preapplication_from_gmail(listing)

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
            return RoofzReplyResult("sent", f"Roofz accepted the contact request ({response.status_code}).")

        detail = response.text[:1000]
        if response.status_code == 400:
            return RoofzReplyResult("validation_failed", detail)
        if response.status_code in {401, 403, 429}:
            return RoofzReplyResult("blocked", detail)
        return RoofzReplyResult("submit_failed", f"{response.status_code}: {detail}")

    async def _complete_preapplication_from_gmail(self, listing: Listing) -> RoofzReplyResult:
        service = build_gmail_service(self.settings.gmail)
        deadline = time.monotonic() + self.settings.preapplication_poll_seconds
        last_detail = "No matching unread Roofz pre-application email arrived yet."
        while True:
            messages = await asyncio.to_thread(
                find_unread_preapplication_messages,
                service,
                self.settings.gmail,
                listing.title,
            )
            if messages:
                result = await self.complete_preapplication(messages[0].links[0])
                if result.status == "preapplication_sent":
                    return result
                return RoofzReplyResult(
                    "sent_preapplication_failed",
                    f"Initial contact was sent, but pre-application failed: {result.detail}",
                )

            if time.monotonic() >= deadline:
                return RoofzReplyResult("sent_preapplication_pending", last_detail)
            await asyncio.sleep(self.settings.preapplication_poll_interval_seconds)

    async def complete_preapplication(self, application_url: str) -> RoofzReplyResult:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.settings.headless)
            page = await browser.new_page(viewport={"width": 1440, "height": 1200})
            page.set_default_timeout(self.settings.timeout_seconds * 1000)
            try:
                await page.goto(application_url, wait_until="domcontentloaded", timeout=self.settings.timeout_seconds * 1000)
                await _accept_cookies(page)
                await _fill_preapplication_form(page, self.settings)

                submit_button = await _find_submit_button(page)
                if not submit_button:
                    return RoofzReplyResult("preapplication_submit_not_found", "No safe submit button was detected.")
                if self.settings.dry_run:
                    return RoofzReplyResult("preapplication_dry_run_ready", "Pre-application form was filled; submit was skipped.")

                await submit_button.click()
                await _wait_for_quiet(page)
                text = await _body_text(page)
                if re.search(r"(thank you|submitted|application.*received|success|bedankt|verzonden)", text, re.I):
                    return RoofzReplyResult("preapplication_sent", "Roofz showed a pre-application confirmation.")
                if re.search(r"(required|invalid|error|failed|verplicht|ongeldig)", text, re.I):
                    return RoofzReplyResult("preapplication_validation_failed", "Roofz showed a validation error.")
                return RoofzReplyResult(
                    "preapplication_submitted_unconfirmed",
                    "Submit was clicked, but no confirmation text was detected.",
                )
            except PlaywrightTimeoutError as exc:
                return RoofzReplyResult("preapplication_timeout", str(exc))
            except Exception as exc:
                logger.exception("Roofz pre-application failed for %s", application_url)
                return RoofzReplyResult("preapplication_error", str(exc))
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


async def _fill_preapplication_form(page, settings: RoofzReplySettings) -> None:
    values = {
        "first name": settings.first_name,
        "voornaam": settings.first_name,
        "last name": settings.last_name,
        "achternaam": settings.last_name,
        "e-mail": settings.email,
        "email": settings.email,
        "phone": settings.phone_number,
        "telefoon": settings.phone_number,
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
    }
    controls = page.locator("input:not([type='hidden']), textarea, select")
    count = await controls.count()
    for index in range(count):
        control = controls.nth(index)
        try:
            control_type = (await control.get_attribute("type") or "").casefold()
            if control_type in {"checkbox", "radio", "submit", "button"}:
                continue
            label_text = await _control_label_text(control)
            value = _value_for_label(label_text, values)
            if not value:
                continue
            tag_name = (await control.evaluate("el => el.tagName")).casefold()
            if tag_name == "select":
                await _select_best_option(control, value)
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


async def _control_label_text(control) -> str:
    return await control.evaluate(
        """
        (el) => {
            const bits = [
                el.getAttribute("aria-label"),
                el.getAttribute("placeholder"),
                el.name,
                el.id,
            ];
            const id = el.id;
            if (id) {
                const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                if (label) bits.push(label.textContent);
            }
            const wrapper = el.closest("label, .q-field, .input-field, .form-group, .field");
            if (wrapper) bits.push(wrapper.textContent);
            return bits.filter(Boolean).join(" ");
        }
        """
    )


def _value_for_label(label: str, values: dict[str, str]) -> str:
    normalized = re.sub(r"\s+", " ", label.casefold())
    for key, value in values.items():
        if key in normalized:
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


async def _find_submit_button(page):
    button = page.get_by_role("button", name=re.compile(r"^(send|submit|apply|next|start|verstuur|indienen|aanvragen)$", re.I))
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
