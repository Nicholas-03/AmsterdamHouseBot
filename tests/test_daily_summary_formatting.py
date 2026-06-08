import os
import unittest

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

from housebot.bot import _format_daily_summary_text


class DailySummaryFormattingTests(unittest.TestCase):
    def test_daily_summary_uses_readable_sections(self):
        text = _format_daily_summary_text(
            listing_counts=[
                {"source": "kamernet", "count": 3},
                {"source": "roofz", "count": 3},
            ],
            auto_summary=[
                {"source": "kamernet", "status": "needs_verification", "count": 1},
                {"source": "kamernet", "status": "sent", "count": 2},
                {"source": "roofz", "status": "sent_preapplication_failed", "count": 1},
                {"source": "roofz", "status": "sent_preapplication_pending", "count": 2},
            ],
            level_counts={"warning": 154, "error": 10},
            scan_age=41,
        )

        self.assertEqual(
            text,
            "\n".join(
                [
                    "Daily housing summary",
                    "Period: last 24h",
                    "",
                    "New listings",
                    "- Kamernet: 3",
                    "- Roofz: 3",
                    "",
                    "Auto-replies",
                    "- Kamernet: 1 needs verification, 2 sent",
                    "- Roofz: 1 pre-application failed, 2 pre-application pending",
                    "",
                    "Warnings / errors",
                    "- Warnings: 154 warnings",
                    "- Errors: 10 errors",
                    "",
                    "Health",
                    "- Last completed scan: 41s ago",
                ]
            ),
        )

    def test_daily_summary_handles_empty_counts(self):
        text = _format_daily_summary_text([], [], {}, None)

        self.assertIn("New listings\n- None", text)
        self.assertIn("Auto-replies\n- None", text)
        self.assertIn("- Warnings: 0 warnings", text)
        self.assertIn("- Errors: 0 errors", text)
        self.assertIn("- Last completed scan: never recorded", text)


if __name__ == "__main__":
    unittest.main()
