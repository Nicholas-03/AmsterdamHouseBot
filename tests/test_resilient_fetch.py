import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

from housebot.scrapers import _resilient_fetch


class _FakeResponse:
    def __init__(self, text: str = "", error: Exception | None = None):
        self.text = text
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error


class _FakeCurlSession:
    attempts = []

    def __init__(self, *, impersonate):
        self.impersonate = impersonate

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, url, headers, timeout):
        self.attempts.append(self.impersonate)
        if self.impersonate == "bad":
            return _FakeResponse(error=RuntimeError("HTTP Error 403"))
        return _FakeResponse(text="<html>ok</html>")


class ResilientFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_retries_with_next_browser_fingerprint(self):
        _FakeCurlSession.attempts = []
        with (
            patch.object(_resilient_fetch, "_USE_CURL", True),
            patch.object(_resilient_fetch, "_CURL_IMPERSONATIONS", ("bad", "good")),
            patch.object(_resilient_fetch, "CurlAsyncSession", _FakeCurlSession),
            patch.object(_resilient_fetch.asyncio, "sleep", return_value=None),
        ):
            html = await _resilient_fetch.fetch_html(
                "https://example.test",
                {},
                source="test",
            )

        self.assertEqual(html, "<html>ok</html>")
        self.assertEqual(_FakeCurlSession.attempts, ["bad", "good"])


if __name__ == "__main__":
    unittest.main()
