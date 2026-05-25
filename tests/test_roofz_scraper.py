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


if __name__ == "__main__":
    unittest.main()
