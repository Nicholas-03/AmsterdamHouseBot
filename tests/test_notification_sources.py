import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import scanner
from notification_sources import ALL_SOURCES, normalize_sources, parse_source_tokens


class NotificationSourceParsingTests(unittest.TestCase):
    def test_parse_source_tokens_accepts_aliases_and_commas(self):
        sources, invalid = parse_source_tokens(["funda,", "roofz.eu", "huur"])

        self.assertEqual(sources, ["funda", "roofz", "huurwoningen"])
        self.assertEqual(invalid, [])

    def test_parse_source_tokens_reports_unknown_sites(self):
        sources, invalid = parse_source_tokens(["funda", "unknown"])

        self.assertEqual(sources, ["funda"])
        self.assertEqual(invalid, ["unknown"])

    def test_normalize_sources_keeps_canonical_order(self):
        self.assertEqual(normalize_sources('["roofz", "funda"]'), ("funda", "roofz"))

    def test_empty_source_value_defaults_to_all_sources(self):
        self.assertEqual(normalize_sources(""), ALL_SOURCES)


def _fake_scraper(source: str, calls: list[str]):
    class FakeScraper:
        SOURCE = source
        last_error = ""

        def __init__(self, *args, **kwargs):
            pass

        async def scrape(self):
            calls.append(source)
            return []

        def _build_url(self):
            return f"https://{source}.test"

    return FakeScraper


class ScannerSourceSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_scan_only_instantiates_enabled_sources(self):
        calls: list[str] = []
        user_filters = {
            "chat_id": 123,
            "city": "Amsterdam",
            "max_price": 1500,
            "min_bedrooms": 1,
            "min_size_m2": 25,
            "kamernet_property_type": "studio",
            "auto_reply_enabled": False,
            "enabled_sources": ("funda", "roofz"),
            "active": True,
            "setup_in_progress": False,
        }

        with (
            patch.object(scanner, "ParariusScraper", _fake_scraper("pararius", calls)),
            patch.object(scanner, "FundaScraper", _fake_scraper("funda", calls)),
            patch.object(scanner, "KamernetScraper", _fake_scraper("kamernet", calls)),
            patch.object(scanner, "HuurwoningenScraper", _fake_scraper("huurwoningen", calls)),
            patch.object(scanner, "RoofzScraper", _fake_scraper("roofz", calls)),
            patch.object(scanner.db, "get_filters", AsyncMock(return_value=user_filters)),
            patch.object(scanner.db, "log_event", AsyncMock()),
        ):
            count = await scanner.run_scan_for_user(AsyncMock(), user_filters)

        self.assertEqual(count, 0)
        self.assertEqual(calls, ["funda", "roofz"])


if __name__ == "__main__":
    unittest.main()
