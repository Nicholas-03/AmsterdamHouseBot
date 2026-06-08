import unittest

from housebot.roofz_mailbox_monitor import RoofzCompleteApplicationEmail


class RoofzMailboxMonitorTests(unittest.TestCase):
    def test_listing_title_strips_complete_application_subject_prefix(self):
        email = RoofzCompleteApplicationEmail(
            message_id="message-1",
            subject="Complete application for Spaklerweg 14-F-10, Amsterdam",
            sender="living@rockfieldrealestate.com",
            links=(),
        )

        self.assertEqual(email.listing_title, "Spaklerweg 14-F-10, Amsterdam")

    def test_listing_title_strips_forwarded_subject_prefix(self):
        email = RoofzCompleteApplicationEmail(
            message_id="message-1",
            subject="Fwd: Complete application for Panamalaan 149, Amsterdam",
            sender="living@rockfieldrealestate.com",
            links=(),
        )

        self.assertEqual(email.listing_title, "Panamalaan 149, Amsterdam")


if __name__ == "__main__":
    unittest.main()
