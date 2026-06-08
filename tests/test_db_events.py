import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

from housebot import db
from housebot.notification_sources import ALL_SOURCES


class BotEventLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = str(Path(self.temp_dir.name) / "events.sqlite3")

    async def asyncTearDown(self):
        db.DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    async def test_log_event_writes_structured_row(self):
        await db.init_db()

        await db.log_event(
            "auto_reply_result",
            level="warning",
            chat_id=123,
            source="kamernet",
            listing_id="2378912",
            title="tt. Vasumweg",
            status="login_failed",
            detail="Kamernet rejected the login credentials.",
            data={"dry_run": False},
        )

        events = await db.get_recent_bot_events()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "auto_reply_result")
        self.assertEqual(events[0]["level"], "warning")
        self.assertEqual(events[0]["chat_id"], 123)
        self.assertEqual(events[0]["source"], "kamernet")
        self.assertEqual(events[0]["status"], "login_failed")
        self.assertIn('"dry_run": false', events[0]["data_json"])

    async def test_prune_bot_events_deletes_rows_older_than_three_days(self):
        await db.init_db()
        await db.log_event("recent_event", status="recent")

        con = sqlite3.connect(db.DB_PATH)
        try:
            con.execute(
                """
                INSERT INTO bot_events (created_at, level, event_type, status)
                VALUES (datetime('now', '-4 days'), 'info', 'old_event', 'old')
                """
            )
            con.commit()
        finally:
            con.close()

        await db.prune_bot_events()
        events = await db.get_recent_bot_events()

        self.assertEqual([event["event_type"] for event in events], ["recent_event"])

    async def test_auto_reply_result_tracks_reply_and_confirmation_latency(self):
        await db.init_db()
        await db.mark_seen("funda", "abc", "https://example.test", "Test listing", "EUR 1000")

        await db.mark_auto_reply_result(
            "funda",
            "abc",
            "https://example.test",
            123,
            "confirmation_confirmed",
            False,
            first_seen_at="2026-05-26 10:00:00",
            sent_at="2026-05-26 10:00:05",
            confirmation_at="2026-05-26 10:00:35",
        )
        reply = await db.get_auto_reply("funda", "abc")

        self.assertEqual(reply["first_seen_at"], "2026-05-26 10:00:00")
        self.assertEqual(reply["sent_at"], "2026-05-26 10:00:05")
        self.assertEqual(reply["confirmation_at"], "2026-05-26 10:00:35")
        self.assertEqual(reply["reply_latency_seconds"], 5)
        self.assertEqual(reply["confirmation_latency_seconds"], 35)

    async def test_filters_default_to_all_notification_sources(self):
        await db.init_db()

        await db.save_filters(123, max_price=1500, min_bedrooms=1, min_size_m2=25)
        user_filters = await db.get_filters(123)
        active_users = await db.get_all_active_users()

        self.assertEqual(user_filters["enabled_sources"], ALL_SOURCES)
        self.assertEqual(active_users[0]["enabled_sources"], ALL_SOURCES)

    async def test_enabled_sources_are_saved_and_preserved_when_filters_change(self):
        await db.init_db()

        await db.save_filters(123, max_price=1500, min_bedrooms=1, min_size_m2=25)
        await db.set_enabled_sources(123, ["funda", "roofz"])
        await db.save_filters(123, max_price=1700, min_bedrooms=2, min_size_m2=30)
        user_filters = await db.get_filters(123)

        self.assertEqual(user_filters["enabled_sources"], ("funda", "roofz"))
        self.assertEqual(user_filters["max_price"], 1700)

    async def test_maintenance_marks_stale_attempting_auto_replies(self):
        await db.init_db()
        await db.mark_auto_reply_result(
            "kamernet",
            "stale",
            "https://example.test",
            123,
            "attempting",
            False,
        )

        con = sqlite3.connect(db.DB_PATH)
        try:
            con.execute(
                """
                UPDATE auto_replies
                SET attempted_at=datetime('now', '-31 minutes'), updated_at=datetime('now', '-31 minutes')
                WHERE source='kamernet' AND listing_id='stale'
                """
            )
            con.commit()
        finally:
            con.close()

        await db.run_maintenance()

        reply = await db.get_auto_reply("kamernet", "stale")
        events = await db.get_recent_bot_events()

        self.assertEqual(reply["status"], "error")
        self.assertEqual(reply["error"], "Auto-reply attempt was interrupted before completion.")
        self.assertEqual(events[0]["event_type"], "stale_auto_replies_marked")
        self.assertEqual(events[0]["status"], "marked_interrupted")


if __name__ == "__main__":
    unittest.main()
