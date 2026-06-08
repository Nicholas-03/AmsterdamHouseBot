import os
import tempfile
import unittest
from pathlib import Path

import httpx

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

from housebot.roofz_complete_application import (
    RoofzCompleteApplicationCompleter,
    RoofzCompleteApplicationSettings,
    _build_questionnaire_payload,
    _parse_application_id,
    _parse_invitation_id,
)


def _write_file(path: Path, content: bytes = b"test") -> str:
    path.write_bytes(content)
    return str(path)


def _settings(tmp: Path, **overrides) -> RoofzCompleteApplicationSettings:
    values = {
        "enabled": True,
        "dry_run": False,
        "api_enabled": True,
        "browser_fallback_enabled": True,
        "email": "housing@example.com",
        "password": "secret",
        "account_id": "account-id",
        "login_url": "https://entree.example.test/login",
        "api_base": "https://relet.example.test",
        "headless": True,
        "timeout_seconds": 10,
        "finalize_poll_seconds": 1,
        "first_name": "Alex",
        "last_name": "Tenant",
        "initials": "A.T.",
        "phone_number": "+391234567890",
        "birth_date": "01-01-2000",
        "gender": "Male",
        "living_situation": "Renting",
        "household_situation": "Alone",
        "family_composition": "Single without children",
        "marital_state": "Single",
        "id_document_type": "Identity card",
        "id_document_number": "TEST12345",
        "id_issue_date": "18-03-2024",
        "id_expiration_date": "01-01-2030",
        "id_issue_country": "Netherlands",
        "city_of_birth": "Amsterdam",
        "address_country": "Netherlands",
        "street": "Teststraat",
        "house_number": "10",
        "house_number_extension": "",
        "postal_code": "1234 AB",
        "city": "Amsterdam",
        "work_situation": "Student",
        "monthly_income": "1000",
        "savings": "100000",
        "financial_obligations": "0",
        "bank_name": "Example Bank",
        "bank_account": "NL00TEST0123456789",
        "comment": "I am interested.",
        "id_document_path": _write_file(tmp / "CI.png"),
        "educational_registration_path": _write_file(tmp / "ProofOfEnrolment.pdf"),
        "salary_slip_paths": (
            _write_file(tmp / "PayslipApr.pdf"),
            _write_file(tmp / "PayslipMarch.pdf"),
            _write_file(tmp / "PayslipFeb.pdf"),
        ),
        "bank_statement_paths": (
            _write_file(tmp / "BankStatementApr.jpg"),
            _write_file(tmp / "BankStatementMarch.jpg"),
            _write_file(tmp / "Liquidity-proof.jpeg"),
        ),
        "deed_of_guarantee_path": _write_file(tmp / "Guarantee-form.pdf"),
    }
    values.update(overrides)
    return RoofzCompleteApplicationSettings(**values)


class RoofzCompleteApplicationPayloadTests(unittest.TestCase):
    def test_parse_application_id_from_osre_link(self):
        self.assertEqual(
            _parse_application_id("https://roofz.onosre.com/application/abc-123?x=1"),
            "abc-123",
        )

    def test_parse_invitation_id_from_osre_link(self):
        self.assertEqual(
            _parse_invitation_id("https://roofz.onosre.com/invitation/invite-123?x=1"),
            "invite-123",
        )

    def test_build_questionnaire_payload_matches_osre_shape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = _settings(Path(tmpdir))

            payload = _build_questionnaire_payload(settings)

        person = payload["person"]
        financial = person["financialSituation"]
        self.assertEqual(person["personalDetails"]["firstName"], "Alex")
        self.assertEqual(person["personalDetails"]["phoneNumber"], "+391234567890")
        self.assertEqual(person["personalDetails"]["dateOfBirth"], "2000-01-01")
        self.assertEqual(person["personalDetails"]["livingSituation"], "renting")
        self.assertEqual(person["address"]["country"], "NL")
        self.assertEqual(person["address"]["houseNumber"], 10)
        self.assertEqual(person["idDocument"]["idDocumentType"], "identity_card")
        self.assertEqual(person["workSituation"]["workSituation"], "student")
        self.assertEqual(person["workSituation"]["workMonthlySalary"], 1000)
        self.assertEqual(financial["financialSavings"], 100000)
        self.assertEqual(financial["financialCredits"], 0)
        self.assertEqual(financial["bankName"], "Example Bank")
        self.assertEqual(financial["bankAccount"], "NL00TEST0123456789")
        self.assertEqual(payload["currentHousingSituation"], "alone")
        self.assertEqual(payload["familyComposition"], "single_without_children")
        self.assertEqual(payload["maritalState"], "single")

    def test_ready_error_requires_existing_documents_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = _settings(Path(tmpdir), id_document_path=str(Path(tmpdir) / "missing.png"))

            self.assertEqual(
                settings.ready_error(),
                "ROOFZ_COMPLETE_ID_DOCUMENT_PATH does not exist.",
            )

    def test_ready_error_requires_three_salary_slips_for_browser_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = _settings(Path(tmpdir), salary_slip_paths=())

            self.assertEqual(
                settings.ready_error(),
                "ROOFZ_COMPLETE_SALARY_SLIP_PATHS must contain at least 3 files.",
            )


class RoofzCompleteApplicationApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_completion_updates_uploads_and_finalizes_application(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = _settings(Path(tmpdir))
            calls = []
            get_count = 0

            def handler(request: httpx.Request) -> httpx.Response:
                nonlocal get_count
                calls.append((request.method, str(request.url), request.read()))
                if request.url.path == "/login":
                    return httpx.Response(200, json={"data": {"attributes": {"session": "session-token"}}})
                if request.method == "GET" and request.url.path == "/portal/applications/app-1":
                    get_count += 1
                    status = "full_application_completed" if get_count > 1 else "awaiting_full_application"
                    return httpx.Response(
                        200,
                        json={
                            "data": {
                                "id": "app-1",
                                "attributes": {
                                    "personId": "person-1",
                                    "status": status,
                                    "attachments": [],
                                },
                            },
                        },
                    )
                if request.method == "PUT" and request.url.path == "/portal/applications/app-1":
                    return httpx.Response(200, json={"data": {"id": "app-1", "attributes": {}}})
                if request.method == "POST" and request.url.path == "/portal/applications/app-1/files":
                    return httpx.Response(201, json={"data": {"id": "app-1", "attributes": {}}})
                if request.method == "PUT" and request.url.path == "/portal/applications/app-1/finalize":
                    return httpx.Response(200, json={"data": {"id": "app-1", "attributes": {}}})
                return httpx.Response(404)

            completer = RoofzCompleteApplicationCompleter(
                settings,
                transport=httpx.MockTransport(handler),
            )

            result = await completer.complete_application("https://roofz.onosre.com/application/app-1")

        self.assertEqual(result.status, "complete_application_sent")
        methods_paths = [(method, httpx.URL(url).path) for method, url, _ in calls]
        self.assertIn(("POST", "/login"), methods_paths)
        self.assertIn(("PUT", "/portal/applications/app-1"), methods_paths)
        self.assertEqual(methods_paths.count(("POST", "/portal/applications/app-1/files")), 9)
        self.assertIn(("PUT", "/portal/applications/app-1/finalize"), methods_paths)
        upload_bodies = [body for method, url, body in calls if method == "POST" and url.endswith("/files")]
        self.assertTrue(any(b"identityDocument" in body for body in upload_bodies))
        self.assertTrue(any(b"deedOfGuarantee" in body for body in upload_bodies))

    async def test_api_completion_resolves_tracking_invitation_to_application(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = _settings(Path(tmpdir))
            calls = []
            get_count = 0

            def handler(request: httpx.Request) -> httpx.Response:
                nonlocal get_count
                calls.append((request.method, str(request.url), request.read()))
                if request.url.host == "tracking.osre.nl":
                    return httpx.Response(
                        302,
                        headers={"Location": "https://roofz.onosre.com/invitation/invite-1"},
                    )
                if request.method == "GET" and request.url.path == "/portal/invitations/invite-1":
                    return httpx.Response(
                        200,
                        json={
                            "data": {
                                "id": "invite-1",
                                "attributes": {"applicationId": "app-1"},
                            },
                        },
                    )
                if request.url.path == "/login":
                    return httpx.Response(200, json={"data": {"attributes": {"session": "session-token"}}})
                if request.method == "GET" and request.url.path == "/portal/applications/app-1":
                    get_count += 1
                    status = "full_application_completed" if get_count > 1 else "awaiting_full_application"
                    return httpx.Response(
                        200,
                        json={
                            "data": {
                                "id": "app-1",
                                "attributes": {
                                    "personId": "person-1",
                                    "status": status,
                                    "attachments": [],
                                },
                            },
                        },
                    )
                if request.method == "PUT" and request.url.path == "/portal/applications/app-1":
                    return httpx.Response(200, json={"data": {"id": "app-1", "attributes": {}}})
                if request.method == "POST" and request.url.path == "/portal/applications/app-1/files":
                    return httpx.Response(201, json={"data": {"id": "app-1", "attributes": {}}})
                if request.method == "PUT" and request.url.path == "/portal/applications/app-1/finalize":
                    return httpx.Response(200, json={"data": {"id": "app-1", "attributes": {}}})
                return httpx.Response(404)

            completer = RoofzCompleteApplicationCompleter(
                settings,
                transport=httpx.MockTransport(handler),
            )

            result = await completer.complete_application("http://tracking.osre.nl/ls/click?x=1")

        self.assertEqual(result.status, "complete_application_sent")
        methods_paths = [(method, httpx.URL(url).path) for method, url, _ in calls]
        self.assertIn(("GET", "/portal/invitations/invite-1"), methods_paths)
        self.assertIn(("PUT", "/portal/applications/app-1"), methods_paths)
