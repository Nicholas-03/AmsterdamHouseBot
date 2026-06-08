import os
from datetime import date
import unittest

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

from housebot.config import _derive_age, _derive_initials


class ConfigDerivedApplicantFieldsTests(unittest.TestCase):
    def test_derives_initials_from_first_name_tokens(self):
        self.assertEqual(_derive_initials("Alex Maria", "Tenant"), "A.M.")

    def test_derives_initials_from_first_and_last_when_needed(self):
        self.assertEqual(_derive_initials("Alex", "Tenant"), "A.T.")

    def test_derives_age_from_day_first_birth_date(self):
        self.assertEqual(_derive_age("01-01-2000", today=date(2026, 6, 8)), "26")


if __name__ == "__main__":
    unittest.main()
