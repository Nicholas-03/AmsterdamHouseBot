import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import scanner
from kamernet_replier import (
    KamernetReplyResult,
    KamernetReplySettings,
    _SUCCESS_RE,
    _build_direct_api_payload,
    _kamernet_api_datetime,
    _normalize_text,
    should_skip_existing_reply,
)
from scrapers.base import Listing


def _settings(**overrides) -> KamernetReplySettings:
    missing_storage_state_path = Path(tempfile.gettempdir()) / "kamernet_storage_state_missing_for_tests.json"
    missing_storage_state_path.unlink(missing_ok=True)
    values = {
        "enabled": True,
        "dry_run": True,
        "email": "tenant@example.com",
        "password": "secret",
        "message": "Hello, I am interested in this property.",
        "max_per_scan": 1,
        "expected_tenancy_duration": "1 year",
        "expected_move_date": "07/01/2026",
        "date_of_birth": "21-04-2003",
        "expected_tenancy_duration_id": 0,
        "gender_id": 1,
        "status_id": 2,
        "languages_spoken_ids": (1, 2, 16),
        "has_pet": False,
        "people_moving_in": 1,
        "tenant_language_id": 2,
        "headless": True,
        "timeout_seconds": 10,
        "api_reply_enabled": True,
        "storage_state_path": missing_storage_state_path,
    }
    values.update(overrides)
    return KamernetReplySettings(**values)


def _listing() -> Listing:
    return Listing(
        id="2378731",
        source="kamernet",
        title="Merce Cunninghamplantsoen",
        price="EUR 1400/month",
        address="Merce Cunninghamplantsoen, Amsterdam",
        url="https://kamernet.nl/en/for-rent/studio-amsterdam/example/studio-2378731",
    )


class KamernetReplySettingsTests(unittest.TestCase):
    def test_ready_error_requires_credentials_and_message(self):
        self.assertEqual(_settings(email="").ready_error(), "KAMERNET_EMAIL is missing.")
        self.assertEqual(
            _settings(password="").ready_error(),
            "KAMERNET_PASSWORD is missing, or run scripts/kamernet_save_session.py once.",
        )
        self.assertEqual(_settings(message="").ready_error(), "KAMERNET_REPLY_MESSAGE is missing.")

    def test_zero_max_per_scan_means_no_cap(self):
        self.assertIsNone(_settings(max_per_scan=0).ready_error())

    def test_ready_error_allows_google_storage_state_without_password(self):
        storage_state = Path(__file__)
        self.assertIsNone(_settings(email="", password="", storage_state_path=storage_state).ready_error())

    def test_should_skip_only_final_or_duplicate_dry_run_results(self):
        self.assertFalse(should_skip_existing_reply(None, requested_dry_run=True))
        self.assertTrue(should_skip_existing_reply({"status": "sent", "dry_run": False}, requested_dry_run=False))
        self.assertFalse(should_skip_existing_reply({"status": "attempting", "dry_run": False}, requested_dry_run=False))
        self.assertTrue(
            should_skip_existing_reply({"status": "dry_run_ready", "dry_run": True}, requested_dry_run=True)
        )
        self.assertFalse(
            should_skip_existing_reply({"status": "dry_run_ready", "dry_run": True}, requested_dry_run=False)
        )
        self.assertFalse(should_skip_existing_reply({"status": "submit_failed", "dry_run": False}, requested_dry_run=False))

    def test_normalize_text_ignores_case_and_spacing(self):
        self.assertEqual(_normalize_text("  1   Year "), "1 year")

    def test_success_pattern_accepts_existing_conversation_state(self):
        self.assertRegex("Continue conversation", _SUCCESS_RE)

    def test_kamernet_api_datetime_uses_expected_date_order(self):
        self.assertEqual(_kamernet_api_datetime("07/01/2026", prefer_month_first=True), "2026-07-01T20:00:00")
        self.assertEqual(_kamernet_api_datetime("21-04-2003", prefer_month_first=False), "2003-04-21T20:00:00")

    def test_build_direct_api_payload_matches_captured_shape(self):
        payload, error = _build_direct_api_payload(_listing(), _settings())

        self.assertEqual(error, "")
        self.assertEqual(
            payload,
            {
                "listingID": 2378731,
                "message": "Hello, I am interested in this property.",
                "genderID": 1,
                "dateOfBirth": "2003-04-21T20:00:00",
                "expectedTenancyDurationID": 3,
                "statusID": 2,
                "languagesSpokenID": [1, 2, 16],
                "hasPet": False,
                "expectedMoveInDate": "2026-07-01T20:00:00",
                "peopleMovingIn": 1,
                "tenantLanguageID": 2,
            },
        )


class FakeReplier:
    def __init__(self, settings):
        self.settings = settings

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def reply_to_listing(self, listing):
        return KamernetReplyResult("dry_run_ready", "ok")


class FakeWarningReplier(FakeReplier):
    async def reply_to_listing(self, listing):
        return KamernetReplyResult("confirmation_missing", "no mail")


class FakeLoginFailedReplier(FakeReplier):
    async def reply_to_listing(self, listing):
        return KamernetReplyResult("login_failed", "Kamernet rejected the login credentials.")


class KamernetScannerAutoReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_reply_marks_attempt_and_result(self):
        mark_result = AsyncMock()
        with (
            patch.object(scanner.db, "get_auto_reply", AsyncMock(return_value=None)),
            patch.object(scanner.db, "mark_auto_reply_result", mark_result),
            patch.object(scanner.db, "log_event", AsyncMock()),
            patch.object(scanner, "KamernetReplier", FakeReplier),
        ):
            attempted = await scanner._maybe_auto_reply_to_listing(
                123,
                _listing(),
                _settings(),
                None,
                attempts_so_far=0,
                replier_cls=scanner.KamernetReplier,
                result_cls=KamernetReplyResult,
                source_label="Kamernet",
            )

        self.assertTrue(attempted)
        self.assertEqual(mark_result.await_count, 2)
        self.assertEqual(mark_result.await_args_list[0].args[4], "attempting")
        self.assertEqual(mark_result.await_args_list[1].args[4], "dry_run_ready")

    async def test_auto_reply_skips_existing_sent_reply(self):
        mark_result = AsyncMock()
        with (
            patch.object(
                scanner.db,
                "get_auto_reply",
                AsyncMock(return_value={"status": "sent", "dry_run": False}),
            ),
            patch.object(scanner.db, "mark_auto_reply_result", mark_result),
            patch.object(scanner.db, "log_event", AsyncMock()),
            patch.object(scanner, "KamernetReplier", FakeReplier),
        ):
            attempted = await scanner._maybe_auto_reply_to_listing(
                123,
                _listing(),
                _settings(dry_run=False),
                None,
                attempts_so_far=0,
                replier_cls=scanner.KamernetReplier,
                result_cls=KamernetReplyResult,
                source_label="Kamernet",
            )

        self.assertFalse(attempted)
        mark_result.assert_not_awaited()

    async def test_auto_reply_sends_warning_for_missing_confirmation(self):
        bot = AsyncMock()
        with (
            patch.object(scanner.db, "get_auto_reply", AsyncMock(return_value=None)),
            patch.object(scanner.db, "mark_auto_reply_result", AsyncMock()),
            patch.object(scanner.db, "log_event", AsyncMock()),
        ):
            attempted = await scanner._maybe_auto_reply_to_listing(
                123,
                _listing(),
                _settings(dry_run=False),
                None,
                attempts_so_far=0,
                replier_cls=FakeWarningReplier,
                result_cls=KamernetReplyResult,
                source_label="Funda",
                bot=bot,
            )

        self.assertTrue(attempted)
        bot.send_message.assert_awaited_once()
        self.assertIn("confirmation_missing", bot.send_message.await_args.kwargs["text"])
        self.assertIn(_listing().url, bot.send_message.await_args.kwargs["text"])

    async def test_auto_reply_sends_warning_for_kamernet_login_failure(self):
        bot = AsyncMock()
        with (
            patch.object(scanner.db, "get_auto_reply", AsyncMock(return_value=None)),
            patch.object(scanner.db, "mark_auto_reply_result", AsyncMock()),
            patch.object(scanner.db, "log_event", AsyncMock()),
        ):
            attempted = await scanner._maybe_auto_reply_to_listing(
                123,
                _listing(),
                _settings(dry_run=False),
                None,
                attempts_so_far=0,
                replier_cls=FakeLoginFailedReplier,
                result_cls=KamernetReplyResult,
                source_label="Kamernet",
                bot=bot,
            )

        self.assertTrue(attempted)
        bot.send_message.assert_awaited_once()
        text = bot.send_message.await_args.kwargs["text"]
        self.assertIn("login_failed", text)
        self.assertIn(_listing().url, text)

    async def test_auto_reply_does_not_warn_for_dry_run_result(self):
        bot = AsyncMock()
        with (
            patch.object(scanner.db, "get_auto_reply", AsyncMock(return_value=None)),
            patch.object(scanner.db, "mark_auto_reply_result", AsyncMock()),
            patch.object(scanner.db, "log_event", AsyncMock()),
        ):
            attempted = await scanner._maybe_auto_reply_to_listing(
                123,
                _listing(),
                _settings(dry_run=True),
                None,
                attempts_so_far=0,
                replier_cls=FakeReplier,
                result_cls=KamernetReplyResult,
                source_label="Kamernet",
                bot=bot,
            )

        self.assertTrue(attempted)
        bot.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
