import unittest
from unittest.mock import AsyncMock, patch

from housebot.scrapers.roofz import RoofzScraper


class RoofzScraperTests(unittest.TestCase):
    def test_parse_api_item_keeps_property_id_and_available_at(self):
        listing = RoofzScraper(city="Amsterdam")._parse_api_item(
            {
                "id": 12580611,
                "slug": "spaklerweg-14-c-10",
                "title": "Spaklerweg 14 C-10",
                "stage": "available",
                "created_at": "2026-06-01T15:13:09.000000Z",
                "status": {"code": "available"},
                "handover": {"price": 1298},
                "address": {
                    "street": "Spaklerweg",
                    "house_number": "14",
                    "house_number_extension": "C-10",
                    "postal_code": "1096 BA",
                    "location": "Amsterdam",
                },
                "characteristic": {
                    "living_area": 32,
                    "layout": {"number_of_bedrooms": 1},
                },
                "media": {"primary_photo": "https://cdn.example/spaklerweg.webp"},
            }
        )

        self.assertIsNotNone(listing)
        self.assertEqual(listing.id, "spaklerweg-14-c-10")
        self.assertEqual(listing.title, "Spaklerweg 14 C-10")
        self.assertEqual(listing.price_eur, 1298)
        self.assertEqual(listing.size_m2_value, 32)
        self.assertEqual(listing.bedrooms, 1)
        self.assertEqual(listing.address, "Spaklerweg 14 C-10, 1096 BA, Amsterdam")
        self.assertEqual(listing.reply_data["property_id"], "12580611")
        self.assertEqual(listing.reply_data["available_at"], "2026-06-01T15:13:09.000000Z")

    def test_parse_row_keeps_property_id_for_replies(self):
        listing = RoofzScraper(city="Amsterdam")._parse_row(
            {
                "href": "https://www.roofz.eu/huur/woningen/jan-van-galenstraat-502",
                "text": "Under option Jan van Galenstraat 502 1061 AZ, AMSTERDAM € 1.389 p/m 37 m² A 1",
                "linkText": "Jan van Galenstraat 502",
                "image": "https://images.example/roofz.webp",
                "propertyId": "12532581",
                "stage": "option",
                "status": "available",
            }
        )

        self.assertEqual(listing.id, "jan-van-galenstraat-502")
        self.assertEqual(listing.source, "roofz")
        self.assertEqual(listing.price_eur, 1389)
        self.assertEqual(listing.size_m2_value, 37)
        self.assertEqual(
            listing.reply_data,
            {"property_id": "12532581", "stage": "option", "status": "available"},
        )

    def test_parse_row_removes_postcode_from_concatenated_title(self):
        listing = RoofzScraper(city="Amsterdam")._parse_row(
            {
                "href": "https://www.roofz.eu/huur/woningen/jan-van-galenstraat-738",
                "text": "Available Jan van Galenstraat 7381061 AZ, AMSTERDAM Rent price: EUR 775 p/m 24 m2",
                "propertyId": "12367539",
            }
        )

        self.assertIsNotNone(listing)
        self.assertEqual(listing.title, "Jan van Galenstraat 738")

    def test_parse_row_keeps_unit_suffix_before_postcode(self):
        listing = RoofzScraper(city="Amsterdam")._parse_row(
            {
                "href": "https://www.roofz.eu/huur/woningen/spaklerweg-14-e-4",
                "text": "Available Spaklerweg 14 E-41096 BA, AMSTERDAM Rent price: EUR 1298 p/m 31 m2",
                "propertyId": "12440190",
            }
        )

        self.assertIsNotNone(listing)
        self.assertEqual(listing.title, "Spaklerweg 14 E-4")


class RoofzNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_goto_retries_once_after_transient_navigation_error(self):
        class FakePage:
            attempts = 0

            async def goto(self, *_args, **_kwargs):
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError("temporary timeout")

        page = FakePage()
        with patch("housebot.scrapers.roofz.asyncio.sleep", AsyncMock()) as sleep:
            await RoofzScraper()._goto_with_retries(page, "https://www.roofz.eu/huur/woningen")

        self.assertEqual(page.attempts, 2)
        sleep.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
