import asyncio
import logging
import random

from bs4 import BeautifulSoup

from .base import BaseScraper, Listing, parse_euro_amount, parse_first_int
from ._resilient_fetch import fetch_html
from .student_compatibility import filter_student_compatible_listings

logger = logging.getLogger(__name__)

_HEADERS = {
    "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Cache-Control": "max-age=0",
    "Upgrade-Insecure-Requests": "1",
}


class ParariusScraper(BaseScraper):
    SOURCE = "pararius"
    BASE_URL = "https://www.pararius.nl"

    def __init__(
        self,
        city: str = "Amsterdam",
        max_price: int = 2000,
        min_bedrooms: int = 1,
        min_size_m2: int = 0,
        student_compatibility_filter_enabled: bool = True,
    ):
        super().__init__(city, max_price, min_bedrooms, min_size_m2)
        self.student_compatibility_filter_enabled = student_compatibility_filter_enabled

    def _build_url(self) -> str:
        city_slug = self.city.lower().replace(" ", "-")
        price_segment = f"/0-{self.max_price}" if self.max_price else ""
        return f"{self.BASE_URL}/huurwoningen/{city_slug}{price_segment}"

    async def scrape(self) -> list[Listing]:
        self.last_error = ""
        try:
            await asyncio.sleep(random.uniform(1.0, 3.0))
            html = await fetch_html(self._build_url(), _HEADERS, source=self.SOURCE, timeout=30)

            soup = BeautifulSoup(html, "lxml")
            listings = [
                listing
                for article in soup.select("section.listing-search-item")
                if (listing := self._parse_article(article)) and self._matches_filters(listing)
            ]
            if self.student_compatibility_filter_enabled:
                listings = await filter_student_compatible_listings(
                    listings,
                    headers=_HEADERS,
                    source=self.SOURCE,
                )
            logger.info("Pararius: found %d matching listings", len(listings))
            return listings
        except Exception as exc:
            self.last_error = str(exc)
            logger.error("Pararius scrape error: %s", exc)
            return []

    def _parse_article(self, article) -> Listing | None:
        try:
            link_tag = article.select_one("a.listing-search-item__link--title")
            if not link_tag:
                return None

            relative_url = link_tag.get("href", "")
            full_url = f"{self.BASE_URL}{relative_url}"
            listing_id = relative_url.strip("/").split("/")[-1]

            title_tag = article.select_one(".listing-search-item__title")
            title = (title_tag or link_tag).get_text(" ", strip=True)

            address_tag = article.select_one(".listing-search-item__sub-title")
            address = address_tag.get_text(" ", strip=True) if address_tag else self.city

            price_tag = article.select_one(".listing-search-item__price")
            price = price_tag.get_text(" ", strip=True) if price_tag else ""

            rooms, bedrooms, size_label, size_value = None, None, None, None
            for feature in article.select(".listing-search-item__features li"):
                text = feature.get_text(" ", strip=True)
                lower = text.lower()
                if "m2" in lower or "m²" in lower:
                    size_label = text
                    size_value = parse_first_int(text)
                elif "kamer" in lower or "slaapkamer" in lower:
                    rooms = text
                    bedrooms = parse_first_int(text)

            image = article.select_one("img")
            image_url = image.get("src") if image else None

            return Listing(
                id=listing_id,
                source=self.SOURCE,
                title=title,
                price=price,
                address=address,
                url=full_url,
                image_url=image_url,
                rooms=rooms,
                size_m2=size_label,
                price_eur=parse_euro_amount(price),
                bedrooms=bedrooms,
                size_m2_value=size_value,
                reply_data={"card_text": article.get_text(" ", strip=True)},
            )
        except Exception as exc:
            logger.warning("Failed to parse Pararius article: %s", exc)
            return None
