import json
import unittest
from unittest.mock import AsyncMock, patch

from housebot.scrapers.base import Listing
from housebot.scrapers.kamernet import KamernetScraper


def _listing(address: str) -> Listing:
    return Listing(
        id="12345",
        source="kamernet",
        title="Leonard Bernsteinstraat",
        price="EUR 1300/month",
        address=address,
        url="https://kamernet.nl/en/for-rent/apartment-almere/example/apartment-12345",
        price_eur=1300,
        size_m2="25 m2",
        size_m2_value=25,
    )


class KamernetScraperTests(unittest.TestCase):
    def test_rejects_listing_outside_requested_city(self):
        scraper = KamernetScraper(city="Amsterdam", max_price=2000)

        self.assertFalse(scraper._matches_filters(_listing("Leonard Bernsteinstraat, Almere")))

    def test_accepts_listing_in_requested_city(self):
        scraper = KamernetScraper(city="Amsterdam", max_price=2000)

        self.assertTrue(scraper._matches_filters(_listing("Prinsengracht, Amsterdam")))

    def test_accepts_postcode_city_format(self):
        scraper = KamernetScraper(city="Amsterdam", max_price=2000)

        self.assertTrue(scraper._matches_filters(_listing("1011 AB Amsterdam")))


class KamernetScrapeFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_scrape_uses_resilient_fetch_for_transient_http_errors(self):
        next_data = {
            "props": {
                "pageProps": {
                    "targetPageProps": {
                        "findListingsResponse": {
                            "listings": [
                                {
                                    "id": 2378072,
                                    "url": "/en/for-rent/studio-amsterdam/example/studio-2378072",
                                    "totalRentalPrice": 1200,
                                    "surfaceArea": 30,
                                    "street": "Example Street",
                                    "city": "Amsterdam",
                                    "title": "Example Studio",
                                    "listingType": 4,
                                }
                            ]
                        }
                    }
                }
            }
        }
        html = f"<html><script id='__NEXT_DATA__'>{json.dumps(next_data)}</script></html>"

        with (
            patch("housebot.scrapers.kamernet.fetch_html", AsyncMock(return_value=html)) as fetch_html,
            patch("housebot.scrapers.kamernet.asyncio.sleep", AsyncMock()),
        ):
            listings = await KamernetScraper(city="Amsterdam", max_price=1500).scrape()

        self.assertEqual([listing.id for listing in listings], ["2378072"])
        self.assertEqual(fetch_html.await_args.kwargs["source"], "kamernet")
        self.assertIn("properties-amsterdam", fetch_html.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
