import unittest
from types import SimpleNamespace

from scrapers.funda import FundaScraper


def _raw_listing(**overrides):
    values = {
        "url": "https://www.funda.nl/detail/huur/amsterdam/example/12345678/",
        "detail_url": "/detail/huur/amsterdam/example/12345678/",
        "title": "Prinsengracht 1",
        "city": "Amsterdam",
        "price": SimpleNamespace(amount=1850, formatted=""),
        "rooms_count": 2,
        "bedrooms": 1,
        "living_area": 55,
        "media": SimpleNamespace(photo_urls=("https://images.example/funda.jpg",)),
        "broker": SimpleNamespace(id=60557),
        "tiny_id": "12345678",
        "global_id": 87654321,
        "id": "12345678",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FundaScraperTests(unittest.TestCase):
    def test_convert_listing_maps_pyfunda_fields(self):
        listing = FundaScraper()._convert_listing(_raw_listing())

        self.assertEqual(listing.id, "12345678")
        self.assertEqual(listing.source, "funda")
        self.assertEqual(listing.title, "Prinsengracht 1")
        self.assertEqual(listing.address, "Prinsengracht 1, Amsterdam")
        self.assertEqual(listing.price, "EUR 1850")
        self.assertEqual(listing.price_eur, 1850)
        self.assertEqual(listing.rooms, "2 rooms, 1 bedroom")
        self.assertEqual(listing.bedrooms, 2)
        self.assertEqual(listing.size_m2, "55 m2")
        self.assertEqual(listing.size_m2_value, 55)
        self.assertEqual(listing.image_url, "https://images.example/funda.jpg")
        self.assertEqual(
            listing.contact_url,
            "https://www.funda.nl/makelaar-contact/?listingId=87654321",
        )
        self.assertEqual(listing.reply_data, {"global_id": "87654321", "office_id": "60557"})

    def test_scrape_sync_uses_rental_filters_and_deduplicates(self):
        raw_listing = _raw_listing()

        class FakeClient:
            location = None
            filters = None

            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def search(self, location, **filters):
                FakeClient.location = location
                FakeClient.filters = filters
                return [raw_listing, raw_listing]

        scraper = FundaScraper(
            city="Amsterdam",
            max_price=2000,
            min_bedrooms=2,
            min_size_m2=50,
        )

        listings = scraper._scrape_sync(FakeClient)

        self.assertEqual(len(listings), 1)
        self.assertEqual(FakeClient.location, "amsterdam")
        self.assertEqual(
            FakeClient.filters,
            {
                "category": "rent",
                "sort": "newest",
                "max_price": 2000,
                "min_rooms": 2,
                "min_area": 50,
            },
        )

    def test_scrape_sync_filters_by_funda_keywords_from_detail_payload(self):
        student_listing = _raw_listing(
            url="https://www.funda.nl/detail/huur/amsterdam/student/11111111/",
            detail_url="/detail/huur/amsterdam/student/11111111/",
            title="Student studio",
            tiny_id="11111111",
            id="11111111",
            global_id=11111111,
        )
        regular_listing = _raw_listing(
            url="https://www.funda.nl/detail/huur/amsterdam/regular/22222222/",
            detail_url="/detail/huur/amsterdam/regular/22222222/",
            title="Regular apartment",
            tiny_id="22222222",
            id="22222222",
            global_id=22222222,
        )

        class FakeClient:
            detail_ids = []

            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def search(self, location, **filters):
                return [student_listing, regular_listing]

            def listing(self, listing_id):
                FakeClient.detail_ids.append(listing_id)
                if listing_id == "11111111":
                    return SimpleNamespace(
                        title="Student studio",
                        description="Only available for a full-time student in Amsterdam.",
                        highlight="",
                        city="Amsterdam",
                    )
                return SimpleNamespace(
                    title="Regular apartment",
                    description="Available for working professionals.",
                    highlight="",
                    city="Amsterdam",
                )

        listings = FundaScraper(keywords="student")._scrape_sync(FakeClient)

        self.assertEqual([listing.id for listing in listings], ["11111111"])
        self.assertEqual(FakeClient.detail_ids, ["11111111", "22222222"])

    def test_scrape_sync_requires_all_configured_funda_keywords(self):
        raw_listing = _raw_listing()

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def search(self, location, **filters):
                return [raw_listing]

            def listing(self, listing_id):
                return SimpleNamespace(
                    title="Student studio",
                    description="Only available for students.",
                    highlight="",
                    city="Amsterdam",
                )

        listings = FundaScraper(keywords=("student", "guarantor"))._scrape_sync(FakeClient)

        self.assertEqual(listings, [])

    def test_convert_listing_expands_relative_urls(self):
        listing = FundaScraper()._convert_listing(
            _raw_listing(url=None, detail_url="/detail/huur/amsterdam/example/12345678/")
        )

        self.assertEqual(
            listing.url,
            "https://www.funda.nl/detail/huur/amsterdam/example/12345678/",
        )


if __name__ == "__main__":
    unittest.main()
