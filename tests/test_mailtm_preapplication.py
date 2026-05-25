from datetime import datetime, timezone
import unittest

from mailtm_preapplication import (
    MailTmSettings,
    extract_preapplication_links,
    find_confirmation_messages,
    find_preapplication_messages,
)


class FakeMailTmClient:
    def __init__(self, summaries, full_messages):
        self.summaries = summaries
        self.full_messages = full_messages

    def list_messages(self):
        return self.summaries

    def get_message(self, message_id):
        return self.full_messages[message_id]


def _settings() -> MailTmSettings:
    return MailTmSettings(
        api_base="https://api.mail.tm",
        address="tenant@example.com",
        password="secret",
        preapplication_sender="living@rockfieldrealestate.com",
        forwarder_address="tenant@gmail.example",
        preapplication_subject_prefix="Start your pre-application",
        confirmation_sender="living@rockfieldrealestate.com",
        confirmation_subject_patterns=("confirmation", "confirmed", "received", "bevestiging", "ontvangen"),
    )


class MailTmPreApplicationTests(unittest.TestCase):
    def test_extract_preapplication_link_from_mailtm_html(self):
        message = {
            "html": [
                '<html><body><a href="https://tracking.osre.nl/ls/click?id=123">'
                "Start pre-application</a></body></html>"
            ],
            "text": "",
        }

        self.assertEqual(
            extract_preapplication_links(message),
            ["https://tracking.osre.nl/ls/click?id=123"],
        )

    def test_extract_preapplication_link_ignores_other_tracking_links(self):
        message = {
            "html": [
                '<a href="https://tracking.osre.nl/ls/click?id=start">Start pre-application</a>'
                '<a href="https://tracking.osre.nl/ls/click?id=faq">FAQ</a>'
                '<a href="https://roofz.onosre.com/invitation/abc/token/def">Open form</a>'
            ],
        }

        self.assertEqual(
            extract_preapplication_links(message),
            [
                "https://tracking.osre.nl/ls/click?id=start",
                "https://roofz.onosre.com/invitation/abc/token/def",
            ],
        )

    def test_find_preapplication_filters_sender_subject_seen_and_title(self):
        client = FakeMailTmClient(
            [
                {
                    "id": "1",
                    "subject": "Start your pre-application for Jan van Galenstraat 502, Amsterdam.",
                    "from": {"address": "living@rockfieldrealestate.com"},
                    "seen": False,
                    "createdAt": "2026-05-25T19:16:40+00:00",
                },
                {
                    "id": "2",
                    "subject": "Start your pre-application for Other",
                    "from": {"address": "living@rockfieldrealestate.com"},
                    "seen": True,
                    "createdAt": "2026-05-25T19:16:40+00:00",
                },
            ],
            {
                "1": {"html": ['<a href="https://tracking.osre.nl/ls/click">Start pre-application</a>']},
                "2": {"html": ['<a href="https://tracking.osre.nl/ls/click">Start pre-application</a>']},
            },
        )

        messages = find_preapplication_messages(
            client,
            _settings(),
            listing_title="Jan van Galenstraat 502",
            since=datetime(2026, 5, 25, 19, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].message_id, "1")

    def test_find_preapplication_accepts_forwarded_gmail_sender(self):
        client = FakeMailTmClient(
            [
                {
                    "id": "1",
                    "subject": "Fwd: Start your pre-application for Jan van Galenstraat 502, Amsterdam.",
                    "from": {"address": "tenant@gmail.example"},
                    "seen": False,
                    "createdAt": "2026-05-25T19:16:40+00:00",
                },
            ],
            {
                "1": {"html": ['<a href="https://tracking.osre.nl/ls/click">Start pre-application</a>']},
            },
        )

        messages = find_preapplication_messages(
            client,
            _settings(),
            listing_title="Jan van Galenstraat 502",
            since=datetime(2026, 5, 25, 19, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].sender, "tenant@gmail.example")

    def test_confirmation_does_not_match_start_preapplication_subject(self):
        client = FakeMailTmClient(
            [
                {
                    "id": "1",
                    "subject": "Start your pre-application for Jan van Galenstraat 502, Amsterdam.",
                    "from": {"address": "living@rockfieldrealestate.com"},
                    "seen": False,
                    "createdAt": "2026-05-25T19:20:40+00:00",
                },
            ],
            {"1": {"text": "Thanks", "html": []}},
        )

        messages = find_confirmation_messages(
            client,
            _settings(),
            listing_title="Jan van Galenstraat 502",
            since=datetime(2026, 5, 25, 19, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(messages, [])

    def test_find_confirmation_matches_configured_subject_patterns(self):
        client = FakeMailTmClient(
            [
                {
                    "id": "1",
                    "subject": "Application received for Jan van Galenstraat 502",
                    "from": {"address": "living@rockfieldrealestate.com"},
                    "seen": False,
                    "createdAt": "2026-05-25T19:20:40+00:00",
                },
            ],
            {"1": {"text": "Thanks", "html": []}},
        )

        messages = find_confirmation_messages(
            client,
            _settings(),
            listing_title="Jan van Galenstraat 502",
            since=datetime(2026, 5, 25, 19, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].message_id, "1")


if __name__ == "__main__":
    unittest.main()
