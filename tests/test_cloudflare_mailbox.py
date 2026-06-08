import unittest

from housebot.cloudflare_mailbox import (
    CloudflareMailboxAuthSettings,
    _normalize_full_message,
)
from housebot.mailtm_preapplication import extract_preapplication_links


class CloudflareMailboxTests(unittest.TestCase):
    def test_ready_error_requires_api_base_and_token(self):
        self.assertEqual(
            CloudflareMailboxAuthSettings(api_base="", api_token="token").ready_error(),
            "CLOUDFLARE_MAILBOX_API_BASE is missing.",
        )
        self.assertEqual(
            CloudflareMailboxAuthSettings(api_base="https://mailbox.example.com", api_token="").ready_error(),
            "CLOUDFLARE_MAILBOX_API_TOKEN is missing.",
        )

    def test_normalizes_raw_mime_for_existing_link_extractor(self):
        raw = (
            "From: ROOFZ.eu <living@rockfieldrealestate.com>\r\n"
            "To: housing@example.com\r\n"
            "Subject: Start your pre-application for Jan van Galenstraat 502, Amsterdam.\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            '<a href="https://roofz.onosre.com/invitation/invite-id/token/token-id/greeting">'
            "Start pre-application</a>"
        )

        message = _normalize_full_message(
            {
                "id": "abc",
                "subject": "Start your pre-application",
                "from": {"address": "living@rockfieldrealestate.com"},
                "createdAt": "2026-05-28T10:00:00Z",
                "raw": raw,
            }
        )

        self.assertEqual(message["from"]["address"], "living@rockfieldrealestate.com")
        self.assertTrue(message["html"])
        self.assertEqual(
            extract_preapplication_links(message),
            ["https://roofz.onosre.com/invitation/invite-id/token/token-id/greeting"],
        )

    def test_preserves_forward_status_metadata(self):
        message = _normalize_full_message(
            {
                "id": "abc",
                "subject": "Forward status",
                "from": {"address": "sender@example.com"},
                "forward": {
                    "to": "person@example.com",
                    "status": "failed",
                    "error": "not verified",
                },
            }
        )

        self.assertEqual(message["forward"]["status"], "failed")
        self.assertEqual(message["forward"]["to"], "person@example.com")

    def test_preserves_worker_extracted_links(self):
        message = _normalize_full_message(
            {
                "id": "abc",
                "subject": "Links",
                "links": ["https://www.pararius.com/apartment-for-rent/amsterdam/26928668/kraanspoor"],
            }
        )

        self.assertEqual(
            message["links"],
            ["https://www.pararius.com/apartment-for-rent/amsterdam/26928668/kraanspoor"],
        )

    def test_recovers_missing_subject_from_raw_mime(self):
        message = _normalize_full_message(
            {
                "id": "abc",
                "subject": "",
                "from": {"address": "living@rockfieldrealestate.com"},
                "raw": (
                    "From: ROOFZ.eu <living@rockfieldrealestate.com>\r\n"
                    "Subject: Confirmation of your pre-application for Schipluidenlaan 662,\r\n"
                    " Amsterdam\r\n"
                    "Content-Type: text/plain; charset=utf-8\r\n"
                    "\r\n"
                    "Thank you for your pre-application."
                ),
            }
        )

        self.assertEqual(
            message["subject"],
            "Confirmation of your pre-application for Schipluidenlaan 662, Amsterdam",
        )


if __name__ == "__main__":
    unittest.main()
