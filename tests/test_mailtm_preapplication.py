from datetime import datetime, timezone
import unittest

from mailtm_preapplication import (
    MailTmSettings,
    extract_complete_application_links,
    extract_preapplication_links,
    find_complete_application_messages,
    find_confirmation_messages,
    find_mailtm_messages,
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

    def test_extract_preapplication_link_decodes_quoted_printable_html(self):
        message = {
            "html": [
                (
                    '<a href=3D"http://tracking.osre.nl/ls/click?upn=3Du001.kPEKN1Gs'
                    'HZ-2BVb6l2c2A6M0N6Ws">Start pre-application</a>'
                )
            ],
        }

        self.assertEqual(
            extract_preapplication_links(message),
            ["http://tracking.osre.nl/ls/click?upn=u001.kPEKN1GsHZ-2BVb6l2c2A6M0N6Ws"],
        )

    def test_extract_preapplication_link_skips_raw_quoted_printable_artifacts(self):
        message = {
            "html": [
                (
                    '<a href=3D"http://tracking.osre.nl/ls/click?upn=3Du001.bad">'
                    "Start pre-application</a>"
                    '<a href="https://roofz.onosre.com/invitation/abc/token/def">Open form</a>'
                )
            ],
        }

        self.assertEqual(
            extract_preapplication_links(message),
            [
                "http://tracking.osre.nl/ls/click?upn=u001.bad",
                "https://roofz.onosre.com/invitation/abc/token/def",
            ],
        )

    def test_extract_complete_application_link_from_html(self):
        message = {
            "html": [
                '<html><body><a href="https://tracking.osre.nl/ls/click?id=complete">'
                "Complete application</a></body></html>"
            ],
        }

        self.assertEqual(
            extract_complete_application_links(message),
            ["https://tracking.osre.nl/ls/click?id=complete"],
        )

    def test_find_complete_application_messages_matches_forwarded_roofz_email(self):
        client = FakeMailTmClient(
            [
                {
                    "id": "1",
                    "subject": "Fwd: Complete application for Spaklerweg 14-E-4, Amsterdam",
                    "from": {"address": "tenant@gmail.example"},
                    "seen": False,
                    "createdAt": "2026-05-28T10:38:00+00:00",
                },
            ],
            {
                "1": {
                    "html": [
                        '<a href="https://roofz.onosre.com/application/abc">'
                        "Complete application</a>"
                    ]
                },
            },
        )

        messages = find_complete_application_messages(client, _settings())

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].message_id, "1")
        self.assertEqual(messages[0].links, ["https://roofz.onosre.com/application/abc"])

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

    def test_find_preapplication_matches_hyphenated_unit_when_title_uses_spaces(self):
        client = FakeMailTmClient(
            [
                {
                    "id": "1",
                    "subject": "Start your pre-application for Spaklerweg 14 C-10, Amsterdam.",
                    "from": {"address": "living@rockfieldrealestate.com"},
                    "seen": True,
                    "createdAt": "2026-06-01T15:25:00+00:00",
                },
            ],
            {
                "1": {"html": ['<a href="https://tracking.osre.nl/ls/click">Start pre-application</a>']},
            },
        )

        messages = find_preapplication_messages(
            client,
            _settings(),
            listing_title="Spaklerweg 14 C 10",
            since=datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc),
            unread_only=False,
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].message_id, "1")

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

    def test_find_mailtm_messages_matches_forwarded_funda_confirmation(self):
        client = FakeMailTmClient(
            [
                {
                    "id": "1",
                    "subject": "Fwd: Bevestiging van je reactie op John Blankensteinstraat 127-B Amsterdam",
                    "from": {"address": "tenant@gmail.example"},
                    "seen": True,
                    "createdAt": "2026-05-25T19:20:40+00:00",
                },
            ],
            {"1": {"text": "Thanks", "html": []}},
        )

        messages = find_mailtm_messages(
            client,
            senders=("tenant@gmail.example",),
            subject_patterns=("bevestiging", "confirmation"),
            listing_title="John Blankensteinstraat 127-B",
            since=datetime(2026, 5, 25, 19, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].message_id, "1")


if __name__ == "__main__":
    unittest.main()
