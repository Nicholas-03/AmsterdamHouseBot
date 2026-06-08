import unittest
from unittest.mock import AsyncMock, patch

from housebot.scrapers.huurwoningen import HuurwoningenScraper
from housebot.scrapers.pararius import ParariusScraper
from housebot.scrapers.student_compatibility import html_to_search_text, is_student_compatible_text


class StudentCompatibilityTextTests(unittest.TestCase):
    def test_accepts_student_or_guarantor_positive_language(self):
        self.assertTrue(
            is_student_compatible_text(
                "Suitable for students. A parental guarantee is accepted by the landlord."
            )
        )
        self.assertTrue(is_student_compatible_text("Studenten welkom. Garantsteller mogelijk."))

    def test_rejects_explicit_student_or_guarantor_negative_language(self):
        self.assertFalse(is_student_compatible_text("Not suitable for students / home sharers / guarantee."))
        self.assertFalse(is_student_compatible_text("Niet geschikt voor studenten. Geen garantsteller."))
        self.assertFalse(
            is_student_compatible_text(
                "Not for sharers. Sharers are understood to mean students and friends."
            )
        )

    def test_requires_positive_student_or_guarantor_signal(self):
        self.assertFalse(is_student_compatible_text("Bright apartment near the university and tram."))

    def test_html_search_text_prefers_listing_content_over_navigation(self):
        text = html_to_search_text("<nav>Student housing</nav><main>Bright apartment near the tram.</main>")

        self.assertFalse(is_student_compatible_text(text))


class StudentCompatibilityScraperTests(unittest.IsolatedAsyncioTestCase):
    async def test_pararius_filters_detail_pages_for_student_compatible_listings(self):
        search_html = """
        <section class="listing-search-item">
          <a class="listing-search-item__link--title" href="/huurwoning/amsterdam/student-studio">Student Studio</a>
          <div class="listing-search-item__sub-title">Amsterdam</div>
          <div class="listing-search-item__price">€ 1200 per maand</div>
          <ul class="listing-search-item__features"><li>25 m2</li><li>1 kamer</li></ul>
        </section>
        <section class="listing-search-item">
          <a class="listing-search-item__link--title" href="/huurwoning/amsterdam/no-students">Regular Apartment</a>
          <div class="listing-search-item__sub-title">Amsterdam</div>
          <div class="listing-search-item__price">€ 1300 per maand</div>
          <ul class="listing-search-item__features"><li>30 m2</li><li>1 kamer</li></ul>
        </section>
        """

        async def detail_fetch(url, *_args, **_kwargs):
            if "student-studio" in url:
                return "<main>Students welcome. Guarantor accepted.</main>"
            return "<main>Not suitable for students.</main>"

        with (
            patch("housebot.scrapers.pararius.fetch_html", AsyncMock(return_value=search_html)),
            patch("housebot.scrapers.pararius.asyncio.sleep", AsyncMock()),
            patch("housebot.scrapers.student_compatibility.fetch_html", detail_fetch),
        ):
            listings = await ParariusScraper(max_price=1500, min_bedrooms=0).scrape()

        self.assertEqual([listing.id for listing in listings], ["student-studio"])

    async def test_huurwoningen_filters_detail_pages_for_student_compatible_listings(self):
        search_html = """
        <section class="listing-search-item" data-listing-search-item-id="student-room">
          <a class="listing-search-item__link--title" href="/en/rent/amsterdam/student-room/">Student Room</a>
          <div class="listing-search-item__sub-title">Amsterdam</div>
          <div class="listing-search-item__price-main">€ 900 per month</div>
          <ul class="listing-search-item__features"><li>20 m2</li><li>1 room</li></ul>
        </section>
        <section class="listing-search-item" data-listing-search-item-id="professionals-only">
          <a class="listing-search-item__link--title" href="/en/rent/amsterdam/professionals-only/">Apartment</a>
          <div class="listing-search-item__sub-title">Amsterdam</div>
          <div class="listing-search-item__price-main">€ 1250 per month</div>
          <ul class="listing-search-item__features"><li>28 m2</li><li>1 room</li></ul>
        </section>
        """

        async def detail_fetch(url, *_args, **_kwargs):
            if "student-room" in url:
                return "<main>Only available for a full-time student.</main>"
            return "<main>No students. No guarantors.</main>"

        with (
            patch("housebot.scrapers.huurwoningen.fetch_html", AsyncMock(return_value=search_html)),
            patch("housebot.scrapers.huurwoningen.asyncio.sleep", AsyncMock()),
            patch("housebot.scrapers.student_compatibility.fetch_html", detail_fetch),
        ):
            listings = await HuurwoningenScraper(max_price=1500, min_bedrooms=0).scrape()

        self.assertEqual([listing.id for listing in listings], ["student-room"])


if __name__ == "__main__":
    unittest.main()
