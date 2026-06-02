import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

from telegram.error import NetworkError

import bot


class BotLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_lock_skip_is_not_counted_as_warning(self):
        log_event = AsyncMock()

        with patch.object(bot.db, "log_event", log_event):
            async with bot._SCAN_LOCK:
                await bot._run_scan_job(
                    SimpleNamespace(),
                    started_event="fast_scan_started",
                    finished_event="fast_scan_finished",
                    failed_event="fast_scan_user_failed",
                )

        log_event.assert_awaited_once()
        self.assertEqual(log_event.await_args.args[0], "fast_scan_skipped")
        self.assertEqual(log_event.await_args.kwargs["level"], "info")
        self.assertEqual(log_event.await_args.kwargs["status"], "scan_already_running")

    async def test_polling_bad_gateway_is_logged_as_transient_warning(self):
        log_event = AsyncMock()
        context = SimpleNamespace(error=NetworkError("Bad Gateway"))

        with patch.object(bot.db, "log_event", log_event):
            await bot.log_error(None, context)

        log_event.assert_awaited_once_with(
            "telegram_polling_transient_error",
            level="warning",
            status="transient",
            detail="Bad Gateway",
        )

    async def test_roofz_preapplication_mailbox_failure_is_source_warning(self):
        class ReadySettings:
            dry_run = False

            def ready_error(self):
                return None

        class FailingReplier:
            def __init__(self, settings):
                self.settings = settings

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def complete_pending_preapplication(self, listing, since, initial_sent_at, poll_seconds=0):
                raise RuntimeError("mailbox 500")

        row = {
            "listing_id": "panamalaan-191",
            "listing_url": "https://www.roofz.eu/huur/woningen/panamalaan-191",
            "seen_title": "Panamalaan 191",
            "seen_price": "EUR 1309/month",
            "triggered_by_chat_id": 123,
            "sent_at": "2026-06-02 09:00:00",
            "attempted_at": "2026-06-02 09:00:00",
            "first_seen_at": "2026-06-02 08:59:00",
        }
        log_event = AsyncMock()
        context = SimpleNamespace(bot=AsyncMock())

        with (
            patch.object(bot.config, "ROOFZ_PREAPPLICATION_MONITOR_ENABLED", True),
            patch.object(bot.RoofzReplySettings, "from_config", return_value=ReadySettings()),
            patch.object(bot, "RoofzReplier", FailingReplier),
            patch.object(bot.db, "get_auto_replies_by_status", AsyncMock(return_value=[row])),
            patch.object(bot, "_get_active_allowed_users", AsyncMock(return_value=[])),
            patch.object(bot.db, "log_event", log_event),
        ):
            await bot.roofz_preapplication_watchdog(context)

        log_event.assert_awaited_once()
        self.assertEqual(log_event.await_args.args[0], "roofz_preapplication_check_failed")
        self.assertEqual(log_event.await_args.kwargs["level"], "warning")
        self.assertEqual(log_event.await_args.kwargs["source"], "roofz")
        self.assertEqual(log_event.await_args.kwargs["listing_id"], "panamalaan-191")
        self.assertEqual(log_event.await_args.kwargs["status"], "error")
        context.bot.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
