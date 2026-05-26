import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import db


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


if __name__ == "__main__":
    unittest.main()
