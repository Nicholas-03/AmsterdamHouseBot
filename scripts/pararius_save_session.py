import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.async_api import async_playwright

from housebot.pararius_replier import (
    ParariusReplySettings,
    _browser_accept_cookies,
    _browser_is_login_page,
    _browser_login,
)

DEFAULT_URL = "https://www.pararius.nl/inloggen"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Pararius storage state with a visible login browser.")
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help="Pararius URL to open before saving the session.",
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
        help="Do not fill email/password automatically; complete the login in the browser.",
    )
    args = parser.parse_args()

    settings = ParariusReplySettings.from_config()
    storage_state_path = settings.storage_state_path
    storage_state_path.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(locale="nl-NL", timezone_id="Europe/Amsterdam")
        page = await context.new_page()
        page.set_default_timeout(settings.timeout_seconds * 1000)

        print("Opening Pararius. Complete login in the browser window if needed.")
        await page.goto(args.url, wait_until="domcontentloaded")
        await _browser_accept_cookies(page)

        if not args.manual and os.getenv("PARARIUS_EMAIL") and os.getenv("PARARIUS_PASSWORD"):
            await _browser_login(page, settings)

        deadline = asyncio.get_running_loop().time() + args.timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if await _looks_logged_in(page):
                await context.storage_state(path=str(storage_state_path))
                print(f"Saved Pararius session to {storage_state_path}")
                await browser.close()
                return 0
            await page.wait_for_timeout(1000)

        print("Timed out before Pararius looked logged in. No session was saved.")
        await browser.close()
        return 1


async def _looks_logged_in(page) -> bool:
    if await _browser_is_login_page(page):
        return False

    try:
        body = await page.locator("body").inner_text(timeout=2000)
    except Exception:
        return False

    return re.search(
        r"(mijn account|uitloggen|profiel|reacties|zoekopdracht|saved searches|my account|log out|responses)",
        body,
        re.I,
    ) is not None


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
