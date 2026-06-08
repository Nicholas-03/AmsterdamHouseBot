from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import Parser
from html import unescape
import quopri
import re
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

from bs4 import BeautifulSoup

from housebot import config
from housebot.cloudflare_mailbox import CloudflareMailboxAuthSettings, CloudflareMailboxClient
from housebot.scrapers._resilient_fetch import fetch_html
from housebot.scrapers.base import Listing, parse_euro_amount, parse_first_int
from housebot.pararius_replier import _extract_contact_url


_HEADERS = {
    "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Cache-Control": "max-age=0",
    "Upgrade-Insecure-Requests": "1",
}
_PARARIUS_PLUS_RE = re.compile(r"pararius\s*(?:\+|=2b|&#43;|&plus;)", re.I)
_PARARIUS_LISTING_PATH_RE = re.compile(
    r"/(?:apartment|house|room|studio)-for-rent/|/(?:appartement|huis|kamer|studio)-te-huur/",
    re.I,
)


@dataclass(frozen=True)
class ParariusAlertEmail:
    message_id: str
    subject: str
    sender: str
    created_at: datetime | None
    seen: bool
    listings: tuple[Listing, ...]


@dataclass(frozen=True)
class _ParariusAlertCandidate:
    message_id: str
    subject: str
    sender: str
    created_at: datetime | None
    seen: bool
    links: tuple[str, ...]


async def find_new_pararius_plus_alert_emails() -> list[ParariusAlertEmail]:
    settings = _settings()
    ready_error = settings.ready_error()
    if ready_error:
        raise RuntimeError(ready_error)

    candidates = await asyncio.to_thread(_read_pararius_plus_candidates, settings)
    found: list[ParariusAlertEmail] = []
    for candidate in candidates:
        listings = []
        for link in candidate.links:
            listing = await listing_from_pararius_alert_link(link, fallback_title=candidate.subject)
            if listing:
                listings.append(listing)
        found.append(
            ParariusAlertEmail(
                message_id=candidate.message_id,
                subject=candidate.subject,
                sender=candidate.sender,
                created_at=candidate.created_at,
                seen=candidate.seen,
                listings=tuple(listings),
            )
        )
    return found


async def mark_pararius_plus_alert_email_seen(message_id: str) -> None:
    settings = _settings()
    ready_error = settings.ready_error()
    if ready_error:
        raise RuntimeError(ready_error)

    def _mark_seen() -> None:
        with CloudflareMailboxClient(settings) as client:
            client.mark_seen(message_id)

    await asyncio.to_thread(_mark_seen)


async def listing_from_pararius_alert_link(url: str, fallback_title: str = "") -> Listing | None:
    normalized_url = normalize_pararius_listing_url(url)
    if not normalized_url:
        return None

    listing_id = listing_id_from_url(normalized_url)
    title = _title_from_url(normalized_url) or fallback_title or "Pararius+ alert"
    price = "Price unavailable"
    address = "Amsterdam"
    image_url = None
    rooms = None
    size_m2 = None
    price_eur = None
    bedrooms = None
    size_m2_value = None
    contact_url = None
    available_at = ""

    try:
        html = await fetch_html(normalized_url, _HEADERS, source="pararius", timeout=30)
    except Exception:
        html = ""

    if html:
        parsed = parse_pararius_detail_html(html, normalized_url, fallback_title=fallback_title)
        title = parsed.title or title
        price = parsed.price or price
        address = parsed.address or address
        image_url = parsed.image_url
        rooms = parsed.rooms
        size_m2 = parsed.size_m2
        price_eur = parsed.price_eur
        bedrooms = parsed.bedrooms
        size_m2_value = parsed.size_m2_value
        contact_url = parsed.contact_url
        available_at = parsed.reply_data.get("source_available_at", "")

    return Listing(
        id=listing_id,
        source="pararius",
        title=title,
        price=price,
        address=address,
        url=normalized_url,
        image_url=image_url,
        rooms=rooms,
        size_m2=size_m2,
        price_eur=price_eur,
        bedrooms=bedrooms,
        size_m2_value=size_m2_value,
        contact_url=contact_url,
        reply_data={
            "mailbox_source": "pararius_plus_alert",
            "available_at": available_at,
        },
    )


def parse_pararius_detail_html(html: str, url: str, fallback_title: str = "") -> Listing:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    title_tag = soup.select_one("h1")
    title = _clean_title(title_tag.get_text(" ", strip=True) if title_tag else fallback_title)
    price = _first_match_text(
        text,
        (
            r"€\s*[\d.,]+(?:\s*(?:pcm|per\s+month|per\s+maand))?",
            r"Rental price\s*\n\s*(€\s*[\d.,]+[^\n]*)",
            r"Huurprijs\s*\n\s*(€\s*[\d.,]+[^\n]*)",
        ),
    )
    size_m2 = _first_match_text(text, (r"\b\d+\s*m[²2]\b",))
    rooms = _first_match_text(text, (r"\b\d+\s*(?:rooms?|kamers?)\b",))
    address = _address_from_title(title) or "Amsterdam"
    image = soup.select_one('meta[property="og:image"], img[src]')
    image_url = (image.get("content") or image.get("src")) if image else None
    available_at = _first_match_text(
        text,
        (
            r"Offered since\s*\n\s*([0-9]{2}-[0-9]{2}-[0-9]{4})",
            r"Aangeboden sinds\s*\n\s*([0-9]{2}-[0-9]{2}-[0-9]{4})",
        ),
    )

    return Listing(
        id=listing_id_from_url(url),
        source="pararius",
        title=title or fallback_title or _title_from_url(url),
        price=price or "",
        address=address,
        url=url,
        image_url=image_url,
        rooms=rooms,
        size_m2=size_m2,
        price_eur=parse_euro_amount(price),
        bedrooms=parse_first_int(rooms),
        size_m2_value=parse_first_int(size_m2),
        contact_url=_extract_contact_url(html, url),
        reply_data={"source_available_at": _parse_pararius_date(available_at)},
    )


def is_pararius_plus_message(message: dict) -> bool:
    return bool(_PARARIUS_PLUS_RE.search("\n".join(_message_bodies(message))))


def extract_pararius_listing_links(message: dict) -> list[str]:
    links: list[str] = []
    for link in message.get("links", []):
        if isinstance(link, str):
            links.append(link)
    for body in _message_bodies(message):
        soup = BeautifulSoup(body, "html.parser")
        for anchor in soup.find_all("a", href=True):
            links.append(anchor["href"])
        links.extend(re.findall(r"https?://[^\s<>\"]+", body))

    cleaned = []
    for link in links:
        normalized = normalize_pararius_listing_url(link)
        if normalized:
            cleaned.append(normalized)
    return list(dict.fromkeys(cleaned))


def normalize_pararius_listing_url(url: str) -> str:
    cleaned = _clean_link(url)
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    if parsed.netloc.casefold() in {"www.google.com", "google.com"} and parsed.path == "/url":
        target = parse_qs(parsed.query).get("q", [""])[0]
        if target:
            return normalize_pararius_listing_url(target)
    if not parsed.netloc.casefold().endswith("pararius.com") and not parsed.netloc.casefold().endswith("pararius.nl"):
        return ""
    if not _PARARIUS_LISTING_PATH_RE.search(parsed.path):
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))


def listing_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    for index, part in enumerate(parts):
        if part.casefold() == "amsterdam" and index + 1 < len(parts):
            return parts[index + 1]
    if len(parts) >= 2:
        return parts[-2]
    return parts[-1] if parts else re.sub(r"\W+", "-", url).strip("-")


def _settings() -> CloudflareMailboxAuthSettings:
    return CloudflareMailboxAuthSettings(
        api_base=config.CLOUDFLARE_MAILBOX_API_BASE,
        api_token=config.CLOUDFLARE_MAILBOX_API_TOKEN,
        address=config.CLOUDFLARE_MAILBOX_ADDRESS,
        max_results=max(1, config.PARARIUS_ALERT_MAILBOX_MAX_RESULTS),
    )


def _read_pararius_plus_candidates(settings: CloudflareMailboxAuthSettings) -> list[_ParariusAlertCandidate]:
    candidates: list[_ParariusAlertCandidate] = []
    with CloudflareMailboxClient(settings) as client:
        for summary in client.list_messages():
            message_id = summary.get("id") or ""
            if not message_id or summary.get("seen"):
                continue
            full = client.get_message(message_id)
            if not is_pararius_plus_message(full):
                continue
            links = extract_pararius_listing_links(full)
            candidates.append(
                _ParariusAlertCandidate(
                    message_id=message_id,
                    subject=full.get("subject") or summary.get("subject") or "",
                    sender=(full.get("from") or {}).get("address") or "",
                    created_at=_parse_datetime(full.get("createdAt") or summary.get("createdAt")),
                    seen=bool(full.get("seen") or summary.get("seen")),
                    links=tuple(links),
                )
            )
    return candidates


def _message_bodies(message: dict) -> list[str]:
    bodies: list[str] = []
    for key in ("subject", "text", "raw"):
        value = message.get(key)
        if isinstance(value, str):
            bodies.extend(_body_variants(value))
    html = message.get("html")
    if isinstance(html, list):
        for part in html:
            if isinstance(part, str):
                bodies.extend(_body_variants(part))
    elif isinstance(html, str):
        bodies.extend(_body_variants(html))
    return list(dict.fromkeys(body for body in bodies if body))


def _body_variants(body: str) -> list[str]:
    variants = [body]
    decoded_qp = _decode_quoted_printable(body)
    if decoded_qp and decoded_qp not in variants:
        variants.append(decoded_qp)
    decoded_mime = _decode_mime_body(body)
    if decoded_mime and decoded_mime not in variants:
        variants.append(decoded_mime)
    return variants


def _decode_quoted_printable(body: str) -> str:
    if "=" not in body:
        return body
    try:
        return quopri.decodestring(body.encode("utf-8", errors="replace")).decode("utf-8", errors="replace")
    except Exception:
        return body


def _decode_mime_body(raw: str) -> str:
    if "\nContent-" not in raw and "\r\nContent-" not in raw:
        return ""
    try:
        parsed = Parser(policy=policy.default).parsestr(raw)
    except Exception:
        return ""
    parts = parsed.walk() if parsed.is_multipart() else [parsed]
    bodies = []
    for part in parts:
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        try:
            bodies.append(part.get_content())
        except Exception:
            continue
    return "\n".join(part for part in bodies if part)


def _clean_link(href: str) -> str:
    raw = str(href or "").strip()
    if not raw:
        return ""
    decoded = _decode_quoted_printable(unescape(raw)).strip().strip("<>'\"")
    decoded = decoded.rstrip(").,;'\"")
    if decoded.startswith(("3D", "=3D")):
        return ""
    return decoded if re.match(r"^https?://", decoded, re.I) else ""


def _clean_title(title: str) -> str:
    cleaned = re.sub(r"^(?:for rent|te huur)\s*:\s*", "", title or "", flags=re.I).strip()
    return re.sub(r"\s+", " ", cleaned)


def _title_from_url(url: str) -> str:
    slug = urlparse(url).path.rstrip("/").split("/")[-1].replace("-", " ")
    return slug.title() if slug else "Pararius+ alert"


def _address_from_title(title: str) -> str:
    match = re.search(r"\bin\s+(.+)$", title or "", re.I)
    return match.group(1).strip() if match else "Amsterdam"


def _first_match_text(text: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)
        if match:
            return re.sub(r"\s+", " ", match.group(1) if match.lastindex else match.group(0)).strip()
    return ""


def _parse_pararius_date(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.strptime(value, "%d-%m-%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    return parsed.isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
