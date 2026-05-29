import os
import unittest

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

from mailtm_preapplication import MailTmSettings
from cloudflare_mailbox import CloudflareMailboxSettings
from roofz_replier import (
    RoofzReplier,
    RoofzReplyResult,
    RoofzReplySettings,
    _build_contact_payload,
    _build_preapplication_payload,
    _parse_osre_invitation,
    _radio_option_matches,
)
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
        "preapplication_api_enabled": True,
        "preapplication_poll_seconds": 1,
        "preapplication_poll_interval_seconds": 1,
        "preapplication_api_url": "https://relet.portal.prd.osre.eu/portal/applications/pre-application",
        "preapplication_availability_api_base": (
            "https://financial-check.portal.prd.osre.eu/portal/financial-check/check-availability"
        ),
        "mailbox_provider": "mailtm",
        "mailtm": MailTmSettings(
            api_base="https://api.mail.tm",
            address="tenant@example.com",
            password="secret",
            preapplication_sender="living@rockfieldrealestate.com",
            forwarder_address="tenant@gmail.example",
            preapplication_subject_prefix="Start your pre-application",
            confirmation_sender="living@rockfieldrealestate.com",
            confirmation_subject_patterns=("confirmation", "confirmed", "received", "bevestiging", "ontvangen"),
        ),
        "cloudflare_mailbox": CloudflareMailboxSettings(
            api_base="https://mailbox.example.com",
            api_token="secret",
            address="housing@example.com",
            preapplication_sender="living@rockfieldrealestate.com",
            forwarder_address="tenant@gmail.example",
            preapplication_subject_prefix="Start your pre-application",
            confirmation_sender="living@rockfieldrealestate.com",
            confirmation_subject_patterns=("confirmation", "confirmed", "received", "bevestiging", "ontvangen"),
        ),
        "initials": "N.G.",
        "birth_date": "01-01-2003",
        "rent_together": False,
        "current_living_situation": "Single without children",
        "work_situation": "Student",
        "monthly_income": "1000",
        "annual_income": "12000",
        "savings": "100000",
        "bank_name": "Test Bank",
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

    def test_preapplication_ready_error_requires_birth_date_when_enabled(self):
        self.assertEqual(
            _settings(preapplication_enabled=True, birth_date="").ready_error(),
            "ROOFZ_BIRTH_DATE is missing.",
        )

    def test_preapplication_ready_error_requires_monthly_income_when_enabled(self):
        self.assertEqual(
            _settings(preapplication_enabled=True, monthly_income="").ready_error(),
            "ROOFZ_MONTHLY_INCOME is missing.",
        )

    def test_build_contact_payload_matches_roofz_api_shape(self):
        payload = _build_contact_payload(_settings(), "12532581")

        self.assertEqual(payload["candidate"], {"email": "tenant@example.com"})
        self.assertEqual(payload["subscription"]["firstname"], "Nicholas")
        self.assertEqual(payload["subscription"]["lastname"], "Boidi")
        self.assertEqual(payload["subscription"]["phone"], "+391234567890")
        self.assertEqual(payload["subscription"]["property_id"], 12532581)
        self.assertIn("_ts", payload["subscription"]["metadata"])

    def test_parse_osre_invitation_url(self):
        parsed = _parse_osre_invitation(
            "https://roofz.onosre.com/invitation/invite-id/token/token-id/greeting?x=1"
        )

        self.assertEqual(
            parsed,
            {
                "origin": "https://roofz.onosre.com",
                "invitation_id": "invite-id",
                "token": "token-id",
            },
        )

    def test_build_preapplication_payload_matches_osre_api_shape(self):
        payload = _build_preapplication_payload(
            _settings(
                birth_date="21-04-2003",
                monthly_income="800",
                savings="300000",
                bank_name="Intesa san Paolo",
            ),
            "invite-id",
        )

        person = payload["application"]["person"]
        financial = person["financialSituation"]
        self.assertEqual(payload["invitationId"], "invite-id")
        self.assertEqual(person["personalDetails"]["dateOfBirth"], "2003-04-21")
        self.assertEqual(person["personalDetails"]["gender"], "male")
        self.assertEqual(person["workSituation"]["workSituation"], "student")
        self.assertEqual(person["workSituation"]["workMonthlySalary"], 800)
        self.assertEqual(financial["financialSavings"], 300000)
        self.assertEqual(financial["bankName"], "Intesa san Paolo")

    def test_radio_option_matching_does_not_match_male_inside_female(self):
        self.assertTrue(_radio_option_matches("male", "male", "Male"))
        self.assertFalse(_radio_option_matches("female", "female", "Male"))
        self.assertTrue(_radio_option_matches("false", "no", "No"))


class RoofzReplierTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_requires_property_id(self):
        async with RoofzReplier(_settings()) as replier:
            result = await replier.reply_to_listing(_listing())

        self.assertEqual(result.status, "dry_run_ready")

    async def test_missing_property_id_is_reported_before_submit(self):
        async with RoofzReplier(_settings()) as replier:
            result = await replier.reply_to_listing(_listing(reply_data={"property_id": ""}))

        self.assertEqual(result.status, "missing_contact_data")

    async def test_preapplication_completion_tries_next_link_after_failure(self):
        class Message:
            message_id = "message-1"
            links = ["bad-link", "good-link"]

        async with RoofzReplier(_settings()) as replier:
            calls = []

            async def fake_complete(link):
                calls.append(link)
                if link == "good-link":
                    return RoofzReplyResult("preapplication_sent", "ok")
                return RoofzReplyResult("preapplication_api_unavailable", "bad")

            replier.complete_preapplication = fake_complete
            result = await replier._complete_first_working_preapplication_link([Message()])

        self.assertEqual(calls, ["bad-link", "good-link"])
        self.assertEqual(result.status, "preapplication_sent")


if __name__ == "__main__":
    unittest.main()
