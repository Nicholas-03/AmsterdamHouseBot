import asyncio
import logging
import re

from bs4 import BeautifulSoup

from ._resilient_fetch import fetch_html
from .base import Listing

logger = logging.getLogger(__name__)

_DETAIL_CONCURRENCY = 4

_POSITIVE_PATTERNS = (
    r"\bstudent(?:s|en)?\b",
    r"\bstudentenwoning(?:en)?\b",
    r"\bstudent\s+(?:housing|room|studio|apartment)\b",
    r"\b(?:suitable|available|intended|only)\s+for\s+(?:a\s+)?student(?:s)?\b",
    r"\bstudent(?:s)?\s+(?:only|welcome|allowed|accepted|considered)\b",
    r"\bstudenten\s+(?:welkom|toegestaan|geaccepteerd)\b",
    r"\b(?:guarantor|guarantee|parental\s+guarantee)\s+(?:accepted|allowed|possible|permitted)\b",
    r"\b(?:garantsteller|garantie)\s+(?:mogelijk|toegestaan|geaccepteerd)\b",
)

_NEGATIVE_PATTERNS = (
    r"\bnot\s+(?:suitable|available|intended)\s+for\s+(?:a\s+)?student(?:s)?\b",
    r"\bnot\s+for\s+student(?:s)?\b",
    r"\bno\s+student(?:s)?\b",
    r"\bstudent(?:s)?\s+(?:are\s+)?not\s+(?:allowed|accepted|permitted|considered)\b",
    r"\bstudent(?:s)?\s+(?:cannot|can't)\s+(?:apply|rent)\b",
    r"\bniet\s+geschikt\s+voor\s+studenten\b",
    r"\bniet\s+voor\s+studenten\b",
    r"\bgeen\s+studenten\b",
    r"\bstudenten\s+niet\s+(?:toegestaan|gewenst|mogelijk|geaccepteerd)\b",
    r"\bno\s+(?:guarantor|guarantors|guarantee|guarantees)\b",
    r"\bguarantor(?:s)?\s+not\s+(?:allowed|accepted|permitted|possible)\b",
    r"\bgeen\s+(?:garantsteller|garantie)\b",
    r"\b(?:garantsteller|garantie)\s+niet\s+(?:mogelijk|toegestaan|geaccepteerd)\b",
    r"\bnot\s+for\s+sharers\b[\s\S]{0,250}\bstudent(?:s)?\b",
    r"\bno\s+sharers\b[\s\S]{0,250}\bstudent(?:s)?\b",
    r"\bnot\s+possible\b[\s\S]{0,180}\bstudent(?:s)?\b",
)


async def filter_student_compatible_listings(
    listings: list[Listing],
    *,
    headers: dict[str, str],
    source: str,
) -> list[Listing]:
    if not listings:
        return []

    semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)
    results = await asyncio.gather(
        *(_is_detail_student_compatible(listing, headers, source, semaphore) for listing in listings)
    )
    filtered = [listing for listing, keep in zip(listings, results) if keep]
    logger.info(
        "%s: %d of %d listings matched student/guarantor compatibility filter",
        source,
        len(filtered),
        len(listings),
    )
    return filtered


def is_student_compatible_text(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if _matches_any(_NEGATIVE_PATTERNS, normalized):
        return False
    return _matches_any(_POSITIVE_PATTERNS, normalized)


def html_to_search_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    content = soup.select_one("main") or soup.select_one("article") or soup.body or soup
    return content.get_text(" ", strip=True)


async def _is_detail_student_compatible(
    listing: Listing,
    headers: dict[str, str],
    source: str,
    semaphore: asyncio.Semaphore,
) -> bool:
    card_text = listing.reply_data.get("card_text", "")
    try:
        async with semaphore:
            html = await fetch_html(listing.url, headers, source=source, timeout=30)
    except Exception as exc:
        logger.warning("%s detail lookup failed for student compatibility filter: %s", source, exc)
        return is_student_compatible_text(_listing_text(listing, card_text))

    return is_student_compatible_text(
        "\n".join(
            (
                _listing_text(listing, card_text),
                html_to_search_text(html),
            )
        )
    )


def _listing_text(listing: Listing, card_text: str) -> str:
    return "\n".join(
        part
        for part in (
            listing.title,
            listing.address,
            listing.price,
            listing.rooms or "",
            listing.size_m2 or "",
            card_text,
        )
        if part
    )


def _matches_any(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def _normalize_text(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split()).casefold()
