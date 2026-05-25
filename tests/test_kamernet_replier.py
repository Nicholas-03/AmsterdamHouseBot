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
        "headless": True,
        "timeout_seconds": 10,
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
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_state = Path(temp_dir) / "kamernet_storage_state.json"
            storage_state.write_text("{}", encoding="utf-8")

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


class FakeReplier:
    def __init__(self, settings):
        self.settings = settings

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def reply_to_listing(self, listing):
        return KamernetReplyResult("dry_run_ready", "ok")


class KamernetScannerAutoReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_reply_marks_attempt_and_result(self):
        mark_result = AsyncMock()
        with (
            patch.object(scanner.db, "get_auto_reply", AsyncMock(return_value=None)),
            patch.object(scanner.db, "mark_auto_reply_result", mark_result),
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


if __name__ == "__main__":
    unittest.main()
