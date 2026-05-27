import asyncio
import logging
import random
import re
from urllib.parse import urlparse

from .base import BaseScraper, Listing, parse_euro_amount, parse_first_int

logger = logging.getLogger(__name__)


class RoofzScraper(BaseScraper):
    SOURCE = "roofz"
    BASE_URL = "https://www.roofz.eu"

    def _urls(self) -> list[str]:
        return [
            f"{self.BASE_URL}/huur/woningen?filter=location:{self.city.lower()}",
            f"{self.BASE_URL}/huur/woningen",
            self.BASE_URL,
        ]

    async def scrape(self) -> list[Listing]:
        self.last_error = ""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.last_error = "playwright is not installed. Run: playwright install chromium"
            logger.error("Roofz: %s", self.last_error)
            return []

        await asyncio.sleep(random.uniform(1.0, 3.0))
        listings: list[Listing] = []
        browser = None
        playwright = None
        try:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(headless=True, timeout=30000)
            page = await browser.new_page(viewport={"width": 1440, "height": 1200})

            for url in self._urls():
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await self._settle_page(page)
                await self._try_select_city(page)
                listings = await self._extract_listings(page)
                listings = [listing for listing in listings if self._matches_filters(listing)]
                if listings:
                    break

        except asyncio.CancelledError:
            logger.warning("Roofz scrape was cancelled before completion.")
            raise
        except Exception as exc:
            self.last_error = str(exc)
            logger.error("Roofz scrape error: %s", exc)
            return []
        finally:
            if browser:
                try:
                    await asyncio.wait_for(browser.close(), timeout=10)
                except Exception as exc:
                    logger.warning("Roofz browser cleanup failed: %s", exc)
            if playwright:
                try:
                    await asyncio.wait_for(playwright.stop(), timeout=10)
                except Exception as exc:
                    logger.warning("Roofz Playwright cleanup failed: %s", exc)

        logger.info("Roofz: found %d matching listings", len(listings))
        return listings

    async def _settle_page(self, page) -> None:
        for label in ("Accept", "Akkoord", "Allow all", "Alles accepteren"):
            try:
                await page.get_by_role("button", name=re.compile(label, re.I)).click(timeout=1200)
                break
            except Exception:
                pass
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

    async def _try_select_city(self, page) -> None:
        city = self.city.lower()
        try:
            await page.evaluate(
                """
                async (city) => {
                    for (const select of document.querySelectorAll("select")) {
                        const option = [...select.options].find((item) =>
                            item.textContent.toLowerCase().includes(city)
                        );
                        if (!option) continue;
                        select.value = option.value;
                        select.dispatchEvent(new Event("input", { bubbles: true }));
                        select.dispatchEvent(new Event("change", { bubbles: true }));
                    }
                }
                """,
                city,
            )
            for label in ("Get results", "Search", "Zoeken"):
                try:
                    await page.get_by_role("button", name=re.compile(label, re.I)).click(timeout=1200)
                    await self._settle_page(page)
                    break
                except Exception:
                    pass
        except Exception:
            pass

    async def _extract_listings(self, page) -> list[Listing]:
        rows = await page.evaluate(
            """
            () => {
                const properties = {};
                const seenObjects = new Set();
                const walk = (value) => {
                    if (!value || typeof value !== "object" || seenObjects.has(value)) return;
                    seenObjects.add(value);
                    if (
                        Number.isInteger(value.id)
                        && typeof value.slug === "string"
                        && typeof value.title === "string"
                        && value.project
                    ) {
                        properties[value.slug] = {
                            propertyId: String(value.id),
                            stage: value.stage || "",
                            status: value.status?.code || "",
                        };
                    }
                    if (Array.isArray(value)) {
                        value.forEach(walk);
                    } else {
                        Object.values(value).forEach(walk);
                    }
                };
                walk(window.__NUXT__);

                const links = [...document.querySelectorAll("a[href]")];
                return links.map((link) => {
                    const href = link.href;
                    const path = new URL(href, window.location.href).pathname.replace(/\\/$/, "");
                    const slug = path.split("/").pop() || "";
                    const property = properties[slug] || {};
                    const container =
                        link.closest("article, li, [class*='card'], [class*='offer'], [class*='property']")
                        || link.parentElement;
                    return {
                        href,
                        linkText: link.textContent || "",
                        text: container ? container.textContent || "" : link.textContent || "",
                        image: container?.querySelector("img")?.src || null,
                        propertyId: property.propertyId || "",
                        stage: property.stage || "",
                        status: property.status || "",
                    };
                });
            }
            """
        )

        listings: list[Listing] = []
        seen: set[str] = set()
        for row in rows:
            listing = self._parse_row(row)
            if not listing or listing.id in seen:
                continue
            seen.add(listing.id)
            listings.append(listing)
        return listings

    def _parse_row(self, row: dict) -> Listing | None:
        text = _clean_text(row.get("text") or row.get("linkText") or "")
        href = row.get("href") or ""
        city = self.city.lower()
        parsed_url = urlparse(href)
        path = parsed_url.path.rstrip("/")

        if city not in text.lower() and city not in href.lower():
            return None
        if not href or ("roofz.eu" not in href and href.startswith("http")):
            return None
        if path == "/huur/woningen" or not path.startswith("/huur/woningen/"):
            return None
        if not _looks_like_listing(text, href):
            return None

        title = _normalize_title(_extract_title(text) or href.rstrip("/").split("/")[-1] or "Roofz listing")
        if title.lower().startswith("from:"):
            title = href.rstrip("/").split("/")[-1].replace("-", " ").title()
        listing_id = _listing_id(href, title)
        price_eur = parse_euro_amount(text)
        size_value = _extract_size(text)
        bedrooms = _extract_bedrooms(text)

        return Listing(
            id=listing_id,
            source=self.SOURCE,
            title=title,
            price=f"EUR {price_eur}/month" if price_eur else "Price unavailable",
            address=_extract_address(text, self.city),
            url=href,
            image_url=row.get("image"),
            rooms=f"{bedrooms} bedrooms" if bedrooms else None,
            size_m2=f"{size_value} m2" if size_value else None,
            price_eur=price_eur,
            bedrooms=bedrooms,
            size_m2_value=size_value,
            reply_data={
                "property_id": _clean_text(row.get("propertyId") or ""),
                "stage": _clean_text(row.get("stage") or ""),
                "status": _clean_text(row.get("status") or ""),
            },
        )


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _looks_like_listing(text: str, href: str) -> bool:
    lower = text.lower()
    return (
        "/huur/woningen" in href.lower()
        or "view apartment" in lower
        or "rent price" in lower
        or "p/m" in lower
        or "m2" in lower
        or "m²" in lower
    )


def _extract_title(text: str) -> str | None:
    for marker in ("Available", "Under option", "Available per"):
        if marker in text:
            tail = text.split(marker, 1)[1].strip()
            if tail:
                return tail.split(" Rent price:", 1)[0].split("€", 1)[0].strip(" -")
    before_price = re.split(r"€|EUR|Rent price:", text, maxsplit=1, flags=re.I)[0].strip()
    return before_price[:120] if before_price else None


def _normalize_title(title: str) -> str:
    title = _clean_text(title)
    postcode_match = re.match(r"^(.*?\d+(?:\s*[A-Za-z])?(?:-\d+)?)\s*\d{4}\s?[A-Z]{2}\b", title)
    if postcode_match:
        return _clean_text(postcode_match.group(1))
    return title


def _extract_address(text: str, city: str) -> str:
    match = re.search(r"\d{4}\s?[A-Z]{2},?\s+[A-Za-z-]+", text)
    if match:
        return match.group(0)
    return city


def _extract_size(text: str) -> int | None:
    match = re.search(r"(\d+)\s*(?:m2|m²)", text, re.I)
    return int(match.group(1)) if match else None


def _extract_bedrooms(text: str) -> int | None:
    match = re.search(r"(\d+)\s*bedrooms?", text, re.I)
    if match:
        return int(match.group(1))

    size_match = re.search(r"(?:m2|m²)\s+[A-Z+]+\s+(\d+)\b", text)
    return int(size_match.group(1)) if size_match else None


def _listing_id(href: str, title: str) -> str:
    slug = href.rstrip("/").split("/")[-1]
    if slug and slug not in {"woningen", "huur"}:
        return slug
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80]
