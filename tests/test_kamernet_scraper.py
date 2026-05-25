import unittest

from scrapers.base import Listing
from scrapers.kamernet import KamernetScraper


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


if __name__ == "__main__":
    unittest.main()
