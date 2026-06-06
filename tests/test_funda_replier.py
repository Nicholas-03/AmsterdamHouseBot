import os
import unittest

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

from funda_replier import (
    FundaConfirmationSettings,
    FundaReplier,
    FundaReplySettings,
    _build_contact_payload,
    find_funda_confirmation_messages,
)
from cloudflare_mailbox import CloudflareMailboxAuthSettings
from mailtm_preapplication import MailTmAuthSettings
from scrapers.base import Listing


def _confirmation(**overrides) -> FundaConfirmationSettings:
    values = {
        "enabled": False,
        "poll_seconds": 1,
        "poll_interval_seconds": 1,
        "mailbox_provider": "mailtm",
        "mailtm": MailTmAuthSettings(
            api_base="https://api.mail.tm",
            address="tenant@example.com",
            password="secret",
        ),
        "cloudflare_mailbox": CloudflareMailboxAuthSettings(
            api_base="https://mailbox.example.com",
            api_token="secret",
            address="housing@example.com",
        ),
        "sender": "",
        "forwarder_address": "tenant@gmail.example",
        "subject_patterns": ("bevestiging", "confirmation", "confirmed"),
    }
    values.update(overrides)
    return FundaConfirmationSettings(**values)


def _settings(**overrides) -> FundaReplySettings:
    values = {
        "enabled": True,
        "dry_run": True,
        "email": "tenant@example.com",
        "first_name": "Nicholas",
        "last_name": "Boidi",
        "phone_number": "+391234567890",
        "message": "Dear property manager\n\nI am interested in this property.",
        "max_per_scan": 1,
        "viewing_request": True,
        "contact_api_base": "https://contacts-bff.funda.io",
        "timeout_seconds": 10,
        "confirmation": _confirmation(),
    }
    values.update(overrides)
    return FundaReplySettings(**values)


def _listing(**overrides) -> Listing:
    values = {
        "id": "80822048",
        "source": "funda",
        "title": "Funda reply test",
        "price": "EUR 1300/month",
        "address": "Amsterdam",
        "url": "https://www.funda.nl/detail/huur/amsterdam/example/80822048/",
        "contact_url": "https://www.funda.nl/makelaar-contact/?listingId=8013049",
        "reply_data": {"global_id": "8013049", "office_id": "60557"},
    }
    values.update(overrides)
    return Listing(**values)


class FundaReplySettingsTests(unittest.TestCase):
    def test_ready_error_requires_contact_fields_and_message(self):
        self.assertEqual(_settings(email="").ready_error(), "FUNDA_EMAIL is missing.")
        self.assertEqual(_settings(first_name="").ready_error(), "FUNDA_FIRST_NAME is missing.")
        self.assertEqual(_settings(last_name="").ready_error(), "FUNDA_LAST_NAME is missing.")
        self.assertEqual(_settings(phone_number="").ready_error(), "FUNDA_PHONE_NUMBER is missing.")
        self.assertEqual(_settings(message="").ready_error(), "FUNDA_REPLY_MESSAGE is missing.")

    def test_confirmation_ready_error_requires_mailtm_when_enabled(self):
        self.assertEqual(
            _settings(
                confirmation=_confirmation(
                    enabled=True,
                    mailtm=MailTmAuthSettings(api_base="https://api.mail.tm", address="", password="secret"),
                )
            ).ready_error(),
            "FUNDA_MAILTM_ADDRESS is missing.",
        )

    def test_build_contact_payload_matches_funda_contact_api_shape(self):
        payload = _build_contact_payload(_settings())

        self.assertEqual(payload["firstName"], "Nicholas")
        self.assertEqual(payload["lastName"], "Boidi")
        self.assertEqual(payload["emailAddress"], "tenant@example.com")
        self.assertEqual(payload["phoneNumber"], "+391234567890")
        self.assertEqual(payload["days"], [])
        self.assertEqual(payload["dayParts"], [])
        self.assertFalse(payload["loggedIn"])


class FundaReplierTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_requires_global_and_office_ids(self):
        async with FundaReplier(_settings()) as replier:
            result = await replier.reply_to_listing(_listing())

        self.assertEqual(result.status, "dry_run_ready")

    async def test_missing_contact_data_is_reported_before_submit(self):
        async with FundaReplier(_settings()) as replier:
            result = await replier.reply_to_listing(_listing(reply_data={"global_id": "", "office_id": ""}))

        self.assertEqual(result.status, "missing_contact_data")


class FundaConfirmationMatchingTests(unittest.TestCase):
    def test_generic_gohome_funda_email_counts_as_confirmation(self):
        client = _FakeMailboxClient(
            [
                {
                    "id": "message-1",
                    "subject": "GoHome | aanvraag voor een bezichtiging via Funda",
                    "from": {"address": '"verhuur@gohome.io" <verhuur@gohome.io>'},
                    "createdAt": "2026-06-03T12:48:37Z",
                    "seen": False,
                }
            ],
            {
                "message-1": {
                    "subject": "GoHome | aanvraag voor een bezichtiging via Funda",
                    "text": (
                        "Hi, Wat leuk dat je interesse hebt in een woning die wij via Funda aanbieden. "
                        "You can plan your own viewing."
                    ),
                    "html": [],
                }
            },
        )

        messages = find_funda_confirmation_messages(
            client,
            senders=("notificaties@service.funda.nl",),
            subject_patterns=("bevestiging", "confirmation", "confirmed"),
            listing_title="Valkenstein 46-1",
            since=None,
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].message_id, "message-1")


class _FakeMailboxClient:
    def __init__(self, summaries, full_messages):
        self._summaries = summaries
        self._full_messages = full_messages

    def list_messages(self):
        return self._summaries

    def get_message(self, message_id):
        return self._full_messages[message_id]


if __name__ == "__main__":
    unittest.main()
