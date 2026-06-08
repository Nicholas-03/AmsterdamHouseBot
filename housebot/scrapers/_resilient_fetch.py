import asyncio
import logging

logger = logging.getLogger(__name__)

try:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession

    _USE_CURL = True
except ImportError:
    import httpx

    CurlAsyncSession = None
    _USE_CURL = False

_CURL_IMPERSONATIONS = (
    "chrome124",
    "chrome133a",
    "chrome136",
    "chrome",
    "safari18_0",
    "safari17_0",
    "safari15_5",
)


async def fetch_html(url: str, headers: dict[str, str], *, source: str, timeout: int = 30) -> str:
    if _USE_CURL:
        return await _fetch_with_curl_fingerprints(url, headers, source=source, timeout=timeout)
    return await _fetch_with_httpx(url, headers, timeout=timeout)


async def _fetch_with_curl_fingerprints(
    url: str,
    headers: dict[str, str],
    *,
    source: str,
    timeout: int,
) -> str:
    if CurlAsyncSession is None:
        raise RuntimeError("curl_cffi is not available.")

    last_error: Exception | None = None
    for attempt, impersonate in enumerate(_CURL_IMPERSONATIONS):
        try:
            async with CurlAsyncSession(impersonate=impersonate) as session:
                response = await session.get(url, headers=headers, timeout=timeout)
                response.raise_for_status()
                if attempt:
                    logger.info(
                        "%s scrape recovered with %s fingerprint after %d failed attempt(s).",
                        source,
                        impersonate,
                        attempt,
                    )
                return response.text
        except Exception as exc:
            last_error = exc
            if attempt < len(_CURL_IMPERSONATIONS) - 1:
                logger.debug(
                    "%s scrape failed with %s fingerprint: %s",
                    source,
                    impersonate,
                    exc,
                )
                await asyncio.sleep(min(2.0, 0.25 * (attempt + 1)))

    if last_error:
        raise last_error
    raise RuntimeError("No curl_cffi browser fingerprints configured.")


async def _fetch_with_httpx(url: str, headers: dict[str, str], *, timeout: int) -> str:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.text
