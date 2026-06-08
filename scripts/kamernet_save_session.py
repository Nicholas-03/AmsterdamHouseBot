import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

from housebot.kamernet_replier import KamernetReplySettings

DEFAULT_LISTING_URL = (
    "https://kamernet.nl/en/for-rent/studio-amsterdam/"
    "merce-cunninghamplantsoen/studio-2378731"
)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Kamernet storage state with a visible login browser.")
    parser.add_argument(
        "--url",
        default=DEFAULT_LISTING_URL,
        help="Kamernet listing URL used to trigger the login flow.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="How long to wait for login.",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Do not fill email/password automatically; you complete the login in the browser.",
    )
    args = parser.parse_args()

    settings = KamernetReplySettings.from_config()
    storage_state_path = settings.storage_state_path
    storage_state_path.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context(locale="en-US", timezone_id="Europe/Amsterdam")
        page = await context.new_page()
        page.set_default_timeout(settings.timeout_seconds * 1000)

        print("Opening Kamernet. Complete login in the browser window if needed.")
        await page.goto(args.url, wait_until="domcontentloaded")
        await _accept_cookies(page)
        await _click_contact_landlord(page)

        if not args.manual and os.getenv("KAMERNET_EMAIL") and os.getenv("KAMERNET_PASSWORD"):
            await _fill_password_login(page, os.environ["KAMERNET_EMAIL"], os.environ["KAMERNET_PASSWORD"])

        deadline = asyncio.get_running_loop().time() + args.timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if await _looks_logged_in(page):
                await context.storage_state(path=str(storage_state_path))
                print(f"Saved Kamernet session to {storage_state_path}")
                await browser.close()
                return 0
            await page.wait_for_timeout(1000)

        print("Timed out before Kamernet looked logged in. No session was saved.")
        await browser.close()
        return 1


async def _accept_cookies(page) -> None:
    for pattern in (re.compile("accept all", re.I), re.compile("accept", re.I)):
        button = page.get_by_role("button", name=pattern)
        try:
            first_button = button.first
            if await button.count() and await first_button.is_visible():
                await first_button.click()
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue


async def _click_contact_landlord(page) -> None:
    button = page.get_by_role("button", name=re.compile("contact landlord|respond|apply|reageer", re.I))
    link = page.get_by_role("link", name=re.compile("contact landlord|respond|apply|reageer", re.I))
    target = button if await button.count() else link
    try:
        first_target = target.first
        if await target.count() and await first_target.is_visible():
            before_url = page.url
            await first_target.click()
            await _wait_for_quiet(page)
            if page.url == before_url:
                try:
                    await page.wait_for_url(lambda url: url != before_url, timeout=10_000)
                except PlaywrightTimeoutError:
                    pass
    except Exception:
        pass


async def _fill_password_login(page, email: str, password: str) -> None:
    await _switch_signup_to_login(page)

    email_field = page.locator("input[type='email'], input[name*='email' i], input[placeholder*='email' i]").first
    password_field = page.locator("input[type='password']").first
    try:
        if await email_field.is_visible() and await password_field.is_visible():
            await email_field.fill(email)
            await password_field.fill(password)
            login_button = page.get_by_role("button", name=re.compile(r"^(log in|login|sign in)$", re.I)).first
            if await login_button.is_visible():
                await login_button.click()
                await _wait_for_quiet(page)
    except Exception:
        pass


async def _switch_signup_to_login(page) -> None:
    if "signup" not in page.url.casefold():
        return

    login_link = page.get_by_role("link", name=re.compile(r"^log in$", re.I)).first
    try:
        if await login_link.is_visible():
            await login_link.click()
            await _wait_for_quiet(page)
    except Exception:
        pass


async def _looks_logged_in(page) -> bool:
    url = page.url.casefold()
    if "id.kamernet.nl" in url:
        return False

    try:
        body = await page.locator("body").inner_text(timeout=2000)
    except Exception:
        return False

    if re.search(r"(welcome back|create a free account|continue with google|\blog in\b|\bsign up\b)", body, re.I):
        return False

    return "kamernet.nl" in url and re.search(
        r"(my account|log out|logout|dashboard|messages|message landlord|contact landlord)",
        body,
        re.I,
    ) is not None


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


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
