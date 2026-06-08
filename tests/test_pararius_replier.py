import os
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

from housebot.pararius_replier import (
    ParariusReplier,
    ParariusReplySettings,
    _build_contact_payload,
    _extract_contact_url,
    _parse_contact_form,
)
from housebot.scrapers.base import Listing


def _settings(**overrides) -> ParariusReplySettings:
    values = {
        "enabled": True,
        "dry_run": True,
        "email": "tenant@example.com",
        "password": "secret",
        "first_name": "Alex",
        "last_name": "Tenant",
        "phone_number": "+391234567890",
        "message": "Dear property manager\n\nI am very interested in the available property.",
        "max_per_scan": 1,
        "salutation": "male",
        "date_of_birth": "2000-01-01",
        "work_situation": "student",
        "monthly_income": "1200",
        "guarantor": "abroad",
        "preferred_living_situation": "alone",
        "number_of_tenants": "1",
        "pets": "no",
        "rent_start_date": "01/07/2026",
        "preferred_contract_period": "1-2 years",
        "current_housing_situation": "renting",
        "headless": True,
        "timeout_seconds": 10,
        "storage_state_path": Path("missing-pararius-storage-state.json"),
        "browser_fallback_enabled": False,
    }
    values.update(overrides)
    return ParariusReplySettings(**values)


def _listing(**overrides) -> Listing:
    values = {
        "id": "eerste-oosterparkstraat",
        "source": "pararius",
        "title": "Appartement Eerste Oosterparkstraat",
        "price": "EUR 1278/month",
        "address": "Amsterdam",
        "url": "https://www.pararius.nl/appartement-te-huur/amsterdam/74f0f129/eerste-oosterparkstraat",
    }
    values.update(overrides)
    return Listing(**values)


class ParariusReplySettingsTests(unittest.TestCase):
    def test_ready_error_requires_contact_fields_and_message(self):
        self.assertEqual(_settings(email="").ready_error(), "PARARIUS_EMAIL is missing.")
        self.assertEqual(
            _settings(password="", dry_run=False, storage_state_path=Path("missing-state.json")).ready_error(),
            "PARARIUS_PASSWORD is missing, or run scripts/pararius_save_session.py once.",
        )
        self.assertEqual(_settings(first_name="").ready_error(), "PARARIUS_FIRST_NAME is missing.")
        self.assertEqual(_settings(last_name="").ready_error(), "PARARIUS_LAST_NAME is missing.")
        self.assertEqual(_settings(phone_number="").ready_error(), "PARARIUS_PHONE_NUMBER is missing.")
        self.assertEqual(_settings(message="").ready_error(), "PARARIUS_REPLY_MESSAGE is missing.")


class ParariusContactFormTests(unittest.TestCase):
    def test_extract_contact_url_from_listing_detail(self):
        html = """
        <a class="button" href="/contact/74f0f129-ff40-53a0-bd34-f33efac5f629">
          Contact met de makelaar
        </a>
        """

        self.assertEqual(
            _extract_contact_url(
                html,
                "https://www.pararius.nl/appartement-te-huur/amsterdam/74f0f129/eerste-oosterparkstraat",
            ),
            "https://www.pararius.nl/contact/74f0f129-ff40-53a0-bd34-f33efac5f629",
        )

    def test_parse_and_fill_contact_form_payload(self):
        html = """
        <form action="/contact/abc" method="post">
          <input type="hidden" name="_token" value="csrf-token">
          <input name="contact[firstName]">
          <input name="contact[lastName]">
          <input name="contact[email]">
          <input name="contact[phoneNumber]">
          <textarea name="contact[message]"></textarea>
          <input type="checkbox" name="contact[privacyAccepted]" value="1">
        </form>
        """

        form = _parse_contact_form(html, "https://www.pararius.nl/contact/abc")
        payload = _build_contact_payload(form, _settings())

        self.assertEqual(payload["_token"], "csrf-token")
        self.assertEqual(payload["contact[firstName]"], "Alex")
        self.assertEqual(payload["contact[lastName]"], "Tenant")
        self.assertEqual(payload["contact[email]"], "tenant@example.com")
        self.assertEqual(payload["contact[phoneNumber]"], "+391234567890")
        self.assertIn("available property", payload["contact[message]"])
        self.assertEqual(payload["contact[privacyAccepted]"], "1")

    def test_build_contact_payload_fills_pararius_profile_fields(self):
        html = """
        <form action="/contact/abc" method="post">
          <input type="hidden" name="contact_agent_huurprofiel_form[_token]" value="csrf-token">
          <textarea name="contact_agent_huurprofiel_form[motivation]"></textarea>
          <select name="contact_agent_huurprofiel_form[salutation]">
            <option value=""></option><option value="0">Heer</option><option value="1">Mevrouw</option>
          </select>
          <input name="contact_agent_huurprofiel_form[first_name]">
          <input name="contact_agent_huurprofiel_form[last_name]">
          <input name="contact_agent_huurprofiel_form[phone_number]">
          <input name="contact_agent_huurprofiel_form[date_of_birth]" type="date">
          <select name="contact_agent_huurprofiel_form[work_situation]">
            <option value=""></option><option value="1">Werkzaam bij werkgever</option><option value="3">Student</option>
          </select>
          <select name="contact_agent_huurprofiel_form[gross_annual_household_income]">
            <option value=""></option><option value="[1000,1500]">EUR 1000 - EUR 1500 per maand</option>
          </select>
          <select name="contact_agent_huurprofiel_form[guarantor]">
            <option value=""></option><option value="1">Geen garantsteller</option><option value="3">Garantsteller woonachtig in het buitenland</option>
          </select>
          <select name="contact_agent_huurprofiel_form[preferred_living_situation]">
            <option value=""></option><option value="1">Nee</option><option value="2">Ja, met partner</option>
          </select>
          <input name="contact_agent_huurprofiel_form[number_of_tenants]" type="number">
          <select name="contact_agent_huurprofiel_form[pets]">
            <option value=""></option><option value="1">Ja</option><option value="0">Nee</option>
          </select>
          <input name="contact_agent_huurprofiel_form[rent_start_date]" type="date">
          <select name="contact_agent_huurprofiel_form[preferred_contract_period]">
            <option value=""></option><option value="4">6 - 12 maanden</option><option value="5">1 - 2 jaar</option>
          </select>
          <select name="contact_agent_huurprofiel_form[current_housing_situation]">
            <option value=""></option><option value="i_rent_a_roof">Ik huur een huis</option>
          </select>
        </form>
        """

        form = _parse_contact_form(html, "https://www.pararius.nl/contact/abc")
        payload = _build_contact_payload(form, _settings())

        self.assertEqual(payload["contact_agent_huurprofiel_form[motivation]"], _settings().message)
        self.assertEqual(payload["contact_agent_huurprofiel_form[salutation]"], "0")
        self.assertEqual(payload["contact_agent_huurprofiel_form[first_name]"], "Alex")
        self.assertEqual(payload["contact_agent_huurprofiel_form[last_name]"], "Tenant")
        self.assertEqual(payload["contact_agent_huurprofiel_form[phone_number]"], "+391234567890")
        self.assertEqual(payload["contact_agent_huurprofiel_form[date_of_birth]"], "2000-01-01")
        self.assertEqual(payload["contact_agent_huurprofiel_form[work_situation]"], "3")
        self.assertEqual(payload["contact_agent_huurprofiel_form[gross_annual_household_income]"], "[1000,1500]")
        self.assertEqual(payload["contact_agent_huurprofiel_form[guarantor]"], "3")
        self.assertEqual(payload["contact_agent_huurprofiel_form[preferred_living_situation]"], "1")
        self.assertEqual(payload["contact_agent_huurprofiel_form[number_of_tenants]"], "1")
        self.assertEqual(payload["contact_agent_huurprofiel_form[pets]"], "0")
        self.assertEqual(payload["contact_agent_huurprofiel_form[rent_start_date]"], "2026-07-01")
        self.assertEqual(payload["contact_agent_huurprofiel_form[preferred_contract_period]"], "5")
        self.assertEqual(payload["contact_agent_huurprofiel_form[current_housing_situation]"], "i_rent_a_roof")


class ParariusReplierTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_is_ready_when_contact_url_is_known(self):
        async with ParariusReplier(_settings()) as replier:
            result = await replier.reply_to_listing(
                _listing(contact_url="https://www.pararius.nl/contact/74f0f129-ff40-53a0-bd34-f33efac5f629")
            )

        self.assertEqual(result.status, "dry_run_ready")

    async def test_missing_contact_url_is_reported_before_submit(self):
        async with ParariusReplier(_settings()) as replier:
            with patch.object(replier, "_fetch_listing_html", AsyncMock(return_value="<main>No contact link</main>")):
                result = await replier.reply_to_listing(_listing())

        self.assertEqual(result.status, "missing_contact_data")


if __name__ == "__main__":
    unittest.main()
