import unittest

from scrapers.roofz import RoofzScraper


class RoofzScraperTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
