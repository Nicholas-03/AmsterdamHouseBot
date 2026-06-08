import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

from telegram.error import NetworkError

from housebot import bot


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
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=456))))

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

    async def test_roofz_complete_application_auto_submits_and_marks_message_seen(self):
        message = SimpleNamespace(
            message_id="message-1",
            subject="Complete application for Panamalaan 263, Amsterdam",
            sender='"ROOFZ.eu" <living@rockfieldrealestate.com>',
            links=("https://roofz.onosre.com/application/abc",),
            listing_title="Panamalaan 263, Amsterdam",
        )
        log_event = AsyncMock()
        mark_seen = AsyncMock()
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=456))))
        completed = []

        class ReadySettings:
            enabled = True

            def ready_error(self):
                return None

        class FakeCompleter:
            def __init__(self, settings):
                self.settings = settings

            async def complete_application(self, url):
                completed.append(url)
                return SimpleNamespace(
                    status="complete_application_sent",
                    detail="sent",
                    sent_at=None,
                )

        with (
            patch.object(bot.config, "ROOFZ_COMPLETE_APPLICATION_MONITOR_ENABLED", True),
            patch.object(bot, "find_new_complete_application_emails", AsyncMock(return_value=[message])),
            patch.object(bot, "mark_complete_application_email_seen", mark_seen),
            patch.object(bot.RoofzCompleteApplicationSettings, "from_config", return_value=ReadySettings()),
            patch.object(bot, "RoofzCompleteApplicationCompleter", FakeCompleter),
            patch.object(bot, "_get_active_allowed_users", AsyncMock(return_value=[{"chat_id": 123, "enabled_sources": '["roofz"]'}])),
            patch.object(bot.db, "bot_event_exists", AsyncMock(return_value=False)),
            patch.object(bot.db, "log_event", log_event),
        ):
            await bot.roofz_complete_application_watchdog(context)

        self.assertEqual(completed, ["https://roofz.onosre.com/application/abc"])
        context.bot.send_message.assert_awaited_once()
        self.assertIn("completed", context.bot.send_message.await_args.kwargs["text"].casefold())
        self.assertTrue(
            any(
                call.args
                and call.args[0] == "roofz_complete_application_notification_user_sent"
                and call.kwargs["chat_id"] == 123
                and call.kwargs["data"]["message_id"] == 456
                for call in log_event.await_args_list
            )
        )
        mark_seen.assert_awaited_once_with("message-1")


if __name__ == "__main__":
    unittest.main()
