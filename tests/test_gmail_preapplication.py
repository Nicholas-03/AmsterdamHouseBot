import base64
import unittest
from pathlib import Path

from gmail_preapplication import (
    GmailPreApplicationSettings,
    build_roofz_preapplication_query,
    extract_preapplication_links,
)


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


class GmailPreApplicationTests(unittest.TestCase):
    def test_build_query_is_limited_to_unread_roofz_preapplications(self):
        settings = GmailPreApplicationSettings(
            credentials_path=Path("credentials.json"),
            token_path=Path("token.json"),
            sender="living@rockfieldrealestate.com",
            subject_prefix="Start your pre-application",
        )

        self.assertEqual(
            build_roofz_preapplication_query(settings, "Jan van Galenstraat 502"),
            'from:living@rockfieldrealestate.com subject:"Start your pre-application" is:unread '
            '"Jan van Galenstraat 502"',
        )

    def test_extract_preapplication_link_from_html_body(self):
        message = {
            "payload": {
                "mimeType": "multipart/alternative",
                "parts": [
                    {
                        "mimeType": "text/html",
                        "body": {
                            "data": _encoded(
                                '<html><body><a href="https://www.roofz.eu/pre-application/abc">'
                                "Start pre-application</a></body></html>"
                            )
                        },
                    }
                ],
            }
        }

        self.assertEqual(
            extract_preapplication_links(message),
            ["https://www.roofz.eu/pre-application/abc"],
        )


if __name__ == "__main__":
    unittest.main()
