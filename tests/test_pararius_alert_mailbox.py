import os
import unittest

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

from housebot.pararius_alert_mailbox import (
    extract_pararius_listing_links,
    is_pararius_plus_message,
    listing_id_from_url,
    normalize_pararius_listing_url,
    parse_pararius_detail_html,
)


class ParariusAlertMailboxTests(unittest.TestCase):
    def test_pararius_plus_detection_requires_plus_signal(self):
        normal = {
            "subject": "Nieuw aanbod op Pararius",
            "text": "Bekijk je normale Pararius melding.",
        }
        plus = {
            "subject": "Nieuw Pararius+ aanbod",
            "text": "Pararius+ heeft een nieuwe woning gevonden.",
        }

        self.assertFalse(is_pararius_plus_message(normal))
        self.assertTrue(is_pararius_plus_message(plus))

    def test_pararius_plus_detection_accepts_quoted_printable_plus(self):
        message = {
            "text": "Pararius=2B heeft een nieuwe woning gevonden.",
        }

        self.assertTrue(is_pararius_plus_message(message))

    def test_extract_listing_link_from_forwarded_html(self):
        message = {
            "html": [
                (
                    '<a href="https://www.pararius.com/apartment-for-rent/amsterdam/26928668/'
                    'kraanspoor?utm_source=direct_property_alert">Open</a>'
                )
            ],
            "links": ["https://www.pararius.com/login"],
        }

        self.assertEqual(
            extract_pararius_listing_links(message),
            [
                "https://www.pararius.com/apartment-for-rent/amsterdam/26928668/"
                "kraanspoor?utm_source=direct_property_alert"
            ],
        )

    def test_normalize_ignores_non_listing_pararius_links(self):
        self.assertEqual(normalize_pararius_listing_url("https://www.pararius.com/login"), "")

    def test_listing_id_uses_pararius_detail_id_not_slug(self):
        self.assertEqual(
            listing_id_from_url("https://www.pararius.com/apartment-for-rent/amsterdam/26928668/kraanspoor"),
            "26928668",
        )
        self.assertEqual(
            listing_id_from_url("https://www.pararius.nl/appartement-te-huur/amsterdam/88c7fe55/lidewijdepad"),
            "88c7fe55",
        )

    def test_parse_detail_html_extracts_core_fields(self):
        html = """
        <html>
          <head><meta property="og:image" content="https://images.example/k.jpg"></head>
          <body>
            <h1>For rent: Flat Kraanspoor 3 P 3 in Amsterdam</h1>
            <p>€1,220 pcm</p>
            <ul><li>46 m²</li><li>2 rooms</li></ul>
            <dl><dt>Offered since</dt><dd>08-06-2026</dd></dl>
            <input name="clickout_contact_form[listing_id]" value="88c7fe55-e8b0-52f1-9d2d-f4b8d63fd04f">
          </body>
        </html>
        """

        listing = parse_pararius_detail_html(
            html,
            "https://www.pararius.com/apartment-for-rent/amsterdam/26928668/kraanspoor",
        )

        self.assertEqual(listing.title, "Flat Kraanspoor 3 P 3 in Amsterdam")
        self.assertEqual(listing.price_eur, 1220)
        self.assertEqual(listing.size_m2_value, 46)
        self.assertEqual(listing.bedrooms, 2)
        self.assertEqual(listing.contact_url, "https://www.pararius.nl/contact/88c7fe55-e8b0-52f1-9d2d-f4b8d63fd04f")
        self.assertEqual(listing.reply_data["source_available_at"], "2026-06-08T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
