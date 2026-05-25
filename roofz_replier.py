from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

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
    preapplication_poll_seconds: int
    preapplication_poll_interval_seconds: int
    mailtm: MailTmSettings
    initials: str
    birth_date: str
    rent_together: bool
    current_living_situation: str
    monthly_income: str
    annual_income: str
    savings: str
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
            mailtm=MailTmSettings.from_config(),
            initials=config.ROOFZ_INITIALS,
            birth_date=config.ROOFZ_BIRTH_DATE,
            rent_together=config.ROOFZ_RENT_TOGETHER,
            current_living_situation=config.ROOFZ_CURRENT_LIVING_SITUATION,
            monthly_income=config.ROOFZ_MONTHLY_INCOME,
            annual_income=config.ROOFZ_ANNUAL_INCOME,
            savings=config.ROOFZ_SAVINGS,
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
            return self.mailtm.ready_error()
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

        started_at = datetime.now(timezone.utc)
        response_result = await self._send_initial_interest(listing, payload)
        if response_result.status != "sent":
            return response_result

        if not self.settings.preapplication_enabled:
            return response_result

        return await self._complete_preapplication_from_mailtm(listing, started_at)

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

    async def _complete_preapplication_from_mailtm(self, listing: Listing, started_at: datetime) -> RoofzReplyResult:
        deadline = time.monotonic() + self.settings.preapplication_poll_seconds
        last_detail = "No matching unread Roofz pre-application email arrived yet."
        with MailTmClient(self.settings.mailtm) as mailtm:
            while True:
                messages = await asyncio.to_thread(
                    find_preapplication_messages,
                    mailtm,
                    self.settings.mailtm,
                    listing.title,
                    started_at,
                    True,
                )
                if messages:
                    confirmation_started_at = datetime.now(timezone.utc)
                    result = await self.complete_preapplication(messages[0].links[0])
                    if result.status not in {"preapplication_sent", "preapplication_submitted_unconfirmed"}:
                        return RoofzReplyResult(
                            "sent_preapplication_failed",
                            f"Initial contact was sent, but pre-application failed: {result.detail}",
                        )

                    confirmation = await self._wait_for_confirmation(mailtm, listing, confirmation_started_at)
                    if confirmation:
                        return RoofzReplyResult("preapplication_confirmed", "Roofz confirmation email arrived.")
                    return RoofzReplyResult(
                        "preapplication_confirmation_missing",
                        "Pre-application was submitted, but no confirmation email arrived in time.",
                    )

                if time.monotonic() >= deadline:
                    return RoofzReplyResult("sent_preapplication_pending", last_detail)
                await asyncio.sleep(self.settings.preapplication_poll_interval_seconds)

    async def _wait_for_confirmation(self, mailtm: MailTmClient, listing: Listing, since: datetime) -> bool:
        deadline = time.monotonic() + self.settings.preapplication_poll_seconds
        while True:
            messages = await asyncio.to_thread(
                find_confirmation_messages,
                mailtm,
                self.settings.mailtm,
                listing.title,
                since,
            )
            if messages:
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(self.settings.preapplication_poll_interval_seconds)

    async def complete_preapplication(self, application_url: str) -> RoofzReplyResult:
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
                        if self.settings.dry_run:
                            return RoofzReplyResult(
                                "preapplication_dry_run_ready",
                                "Pre-application form was filled; final submit was skipped.",
                            )
                        await final_button.click()
                        await _wait_for_quiet(page)
                        text = await _body_text(page)
                        if re.search(r"(thank you|submitted|application.*received|success|bedankt|verzonden|sent)", text, re.I):
                            return RoofzReplyResult("preapplication_sent", "Roofz showed a pre-application confirmation.")
                        return RoofzReplyResult(
                            "preapplication_submitted_unconfirmed",
                            "Submit was clicked, but no confirmation text was detected.",
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

                return RoofzReplyResult("preapplication_too_many_steps", "The pre-application had more steps than expected.")
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
        "work situation": settings.occupation,
        "monthly salary": settings.monthly_income,
        "monthly income": settings.monthly_income,
        "annual income": settings.annual_income,
        "savings": settings.savings,
        "equity": settings.savings,
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

    for radio in await page.locator("input[type='radio']").all():
        try:
            option_text = await _radio_option_text(radio)
            group_text = await _control_label_text(radio)
            value = (await radio.get_attribute("value") or "").casefold()
            normalized_option = option_text.casefold()
            normalized_group = group_text.casefold()
            if value == settings.gender.casefold() or settings.gender.casefold() in normalized_option:
                await radio.check(force=True)
            elif "rent together" in normalized_group or "together with someone" in normalized_group:
                expected = "true" if settings.rent_together else "false"
                expected_text = "yes" if settings.rent_together else "no"
                expected_nl = "ja" if settings.rent_together else "nee"
                if value in {expected, expected_text, expected_nl} or expected_text in normalized_option or expected_nl in normalized_option:
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
