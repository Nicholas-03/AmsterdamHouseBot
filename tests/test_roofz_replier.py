import os
import unittest
from pathlib import Path

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

from gmail_preapplication import GmailPreApplicationSettings
from roofz_replier import RoofzReplier, RoofzReplySettings, _build_contact_payload
from scrapers.base import Listing


def _settings(**overrides) -> RoofzReplySettings:
    values = {
        "enabled": True,
        "dry_run": True,
        "email": "tenant@example.com",
        "first_name": "Nicholas",
        "last_name": "Boidi",
        "phone_number": "+391234567890",
        "message": "Dear property manager\n\nI am interested in this property.",
        "max_per_scan": 1,
        "contact_api_url": "https://www.roofz.eu/api/ms/subscription/candidate",
        "headless": True,
        "timeout_seconds": 10,
        "preapplication_enabled": False,
        "preapplication_poll_seconds": 1,
        "preapplication_poll_interval_seconds": 1,
        "gmail": GmailPreApplicationSettings(
            credentials_path=Path("missing_credentials.json"),
            token_path=Path("missing_token.json"),
            sender="living@rockfieldrealestate.com",
            subject_prefix="Start your pre-application",
        ),
        "expected_stay_duration": "1 year",
        "expected_move_date": "01/07/2026",
        "gender": "Male",
        "age": "23",
        "occupation": "Working student",
        "languages": "Dutch, English, Italian",
        "pets": "No",
        "people_moving": "1",
    }
    values.update(overrides)
    return RoofzReplySettings(**values)


def _listing(**overrides) -> Listing:
    values = {
        "id": "jan-van-galenstraat-502",
        "source": "roofz",
        "title": "Jan van Galenstraat 502",
        "price": "EUR 1389/month",
        "address": "Amsterdam",
        "url": "https://www.roofz.eu/huur/woningen/jan-van-galenstraat-502",
        "reply_data": {"property_id": "12532581"},
    }
    values.update(overrides)
    return Listing(**values)


class RoofzReplySettingsTests(unittest.TestCase):
    def test_ready_error_requires_contact_fields(self):
        self.assertEqual(_settings(email="").ready_error(), "ROOFZ_EMAIL is missing.")
        self.assertEqual(_settings(first_name="").ready_error(), "ROOFZ_FIRST_NAME is missing.")
        self.assertEqual(_settings(last_name="").ready_error(), "ROOFZ_LAST_NAME is missing.")
        self.assertEqual(_settings(phone_number="").ready_error(), "ROOFZ_PHONE_NUMBER is missing.")
        self.assertEqual(_settings(message="").ready_error(), "ROOFZ_REPLY_MESSAGE is missing.")

    def test_preapplication_ready_error_requires_gmail_token_when_enabled(self):
        self.assertIn("missing_credentials.json", _settings(preapplication_enabled=True).ready_error())

    def test_build_contact_payload_matches_roofz_api_shape(self):
        payload = _build_contact_payload(_settings(), "12532581")

        self.assertEqual(payload["candidate"], {"email": "tenant@example.com"})
        self.assertEqual(payload["subscription"]["firstname"], "Nicholas")
        self.assertEqual(payload["subscription"]["lastname"], "Boidi")
        self.assertEqual(payload["subscription"]["phone"], "+391234567890")
        self.assertEqual(payload["subscription"]["property_id"], 12532581)
        self.assertIn("_ts", payload["subscription"]["metadata"])


class RoofzReplierTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_requires_property_id(self):
        async with RoofzReplier(_settings()) as replier:
            result = await replier.reply_to_listing(_listing())

        self.assertEqual(result.status, "dry_run_ready")

    async def test_missing_property_id_is_reported_before_submit(self):
        async with RoofzReplier(_settings()) as replier:
            result = await replier.reply_to_listing(_listing(reply_data={"property_id": ""}))

        self.assertEqual(result.status, "missing_contact_data")


if __name__ == "__main__":
    unittest.main()
