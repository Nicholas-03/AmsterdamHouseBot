import unittest

from scripts.mailbox_viewer import _index_html


class MailboxViewerHtmlTests(unittest.TestCase):
    def test_message_rows_are_not_forced_to_toolbar_button_height(self):
        html = _index_html("housing@example.test")

        self.assertIn(".message-row {", html)
        self.assertIn("height: auto;", html)
        self.assertIn("min-height: 66px;", html)
        self.assertIn('id="list-status"', html)


if __name__ == "__main__":
    unittest.main()
