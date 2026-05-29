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


if __name__ == "__main__":
    unittest.main()
