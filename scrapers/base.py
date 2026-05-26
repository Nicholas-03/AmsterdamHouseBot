from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import re


@dataclass
class Listing:
    id: str
    source: str
    title: str
    price: str
    address: str
    url: str
    image_url: str | None = None
    rooms: str | None = None
    size_m2: str | None = None
    price_eur: int | None = None
    bedrooms: int | None = None
    size_m2_value: int | None = None
    contact_url: str | None = None
    reply_data: dict[str, str] = field(default_factory=dict)


class BaseScraper(ABC):
    SOURCE = ""
    BASE_URL = ""

    def __init__(
        self,
        city: str = "Amsterdam",
        max_price: int = 2000,
        min_bedrooms: int = 1,
        min_size_m2: int = 0,
    ):
        self.city = city.strip() or "Amsterdam"
        self.max_price = max_price
        self.min_bedrooms = min_bedrooms
        self.min_size_m2 = min_size_m2
        self.last_error = ""

    @abstractmethod
    async def scrape(self) -> list[Listing]:
        pass

    def _matches_filters(self, listing: Listing) -> bool:
        if self.max_price and listing.price_eur and listing.price_eur > self.max_price:
            return False
        if self.min_bedrooms and listing.bedrooms is not None and listing.bedrooms < self.min_bedrooms:
            return False
        if self.min_size_m2 and listing.size_m2_value and listing.size_m2_value < self.min_size_m2:
            return False
        return True


def parse_euro_amount(text: str | None) -> int | None:
    if not text:
        return None

    normalized = text.replace("\xa0", " ")
    patterns = (
        r"(?:€|EUR)\s*(\d[\d.,\s]*)",
        r"rent\s*price:?\s*(?:€|EUR)?\s*(\d[\d.,\s]*)",
        r"(\d[\d.,\s]*)\s*(?:pcm|p/m|per\s+maand|per\s+month|/month)",
    )

    match = None
    for pattern in patterns:
        match = re.search(pattern, normalized, re.I)
        if match:
            break
    if not match:
        match = re.search(r"\d[\d.,\s]*", normalized)
    if not match:
        return None

    value = match.group(1) if match.lastindex else match.group(0)
    digits = _normalize_amount_digits(value)
    return int(digits) if digits else None


def _normalize_amount_digits(value: str) -> str:
    value = re.sub(r"\s+", "", value)
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").split(",", 1)[0]
        else:
            value = value.replace(",", "").split(".", 1)[0]
    elif "," in value:
        parts = value.split(",")
        if len(parts[-1]) == 3 and all(part.isdigit() for part in parts):
            value = "".join(parts)
        else:
            value = parts[0]
    elif "." in value:
        parts = value.split(".")
        if len(parts[-1]) == 3 and all(part.isdigit() for part in parts):
            value = "".join(parts)
        else:
            value = parts[0]

    return re.sub(r"\D", "", value)


def parse_first_int(text: str | None) -> int | None:
    if not text:
        return None

    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None
