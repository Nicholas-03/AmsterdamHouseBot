from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright


SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-csrf-token",
    "x-xsrf-token",
    "csrf-token",
    "xsrf-token",
}

DEFAULT_CAPTURE_HOST_MARKERS = (
    "onosre.com",
    "osre.eu",
    "portal.prd.osre.eu",
    "relet.portal.prd.osre.eu",
    "financial-check.portal.prd.osre.eu",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for name, value in headers.items():
        if name.lower() in SENSITIVE_HEADER_NAMES:
            safe[name] = "<redacted>"
        else:
            safe[name] = value
    return safe


def _text_preview(text: str | None, limit: int) -> dict[str, Any]:
    if not text:
        return {"text": "", "truncated": False, "length": 0}
    truncated = len(text) > limit
    return {
        "text": text[:limit],
        "truncated": truncated,
        "length": len(text),
    }


def _matches_capture_target(url: str, markers: tuple[str, ...]) -> bool:
    lowered = url.lower()
    return any(marker.lower() in lowered for marker in markers)


def _write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Capture Roofz/OSRE browser API requests while a user fills an application.")
    parser.add_argument("--url", default="https://roofz.onosre.com/dashboard", help="Initial URL to open.")
    parser.add_argument("--output-dir", default="output", help="Directory where JSONL capture files are written.")
    parser.add_argument("--profile-dir", default="output/roofz_osre_capture_profile", help="Persistent browser profile directory.")
    parser.add_argument("--timeout-seconds", type=int, default=7200, help="Maximum capture duration.")
    parser.add_argument("--body-preview-limit", type=int, default=20000, help="Max request/response body characters stored.")
    parser.add_argument("--marker", action="append", default=[], help="Additional URL substring to capture.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(args.profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    capture_path = output_dir / f"roofz_osre_requests_{stamp}.jsonl"
    status_path = output_dir / "roofz_osre_capture_status.json"
    markers = tuple(dict.fromkeys(DEFAULT_CAPTURE_HOST_MARKERS + tuple(args.marker)))

    status = {
        "started_at": _utc_now(),
        "capture_path": str(capture_path.resolve()),
        "status": "running",
        "initial_url": args.url,
        "markers": markers,
    }
    status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(f"Capture file: {capture_path.resolve()}", flush=True)
    print("Fill and submit the application in the browser window. Close the browser when done.", flush=True)

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile_dir.resolve()),
            headless=False,
            locale="en-US",
            timezone_id="Europe/Amsterdam",
            viewport={"width": 1440, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )

        async def on_request(request) -> None:
            if not _matches_capture_target(request.url, markers):
                return
            method = request.method.upper()
            if method not in {"POST", "PUT", "PATCH", "DELETE"}:
                return
            try:
                post_data = request.post_data
            except Exception as exc:
                post_data = f"<could not read post data: {exc}>"
            _write_jsonl(
                capture_path,
                {
                    "event": "request",
                    "time": _utc_now(),
                    "method": method,
                    "url": request.url,
                    "resource_type": request.resource_type,
                    "headers": _safe_headers(request.headers),
                    "post_data": _text_preview(post_data, args.body_preview_limit),
                },
            )

        async def on_response(response) -> None:
            if not _matches_capture_target(response.url, markers):
                return
            request = response.request
            if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
                return
            body_text = ""
            try:
                content_type = response.headers.get("content-type", "")
                if "json" in content_type or "text" in content_type:
                    body_text = await response.text()
            except Exception as exc:
                body_text = f"<could not read response body: {exc}>"
            _write_jsonl(
                capture_path,
                {
                    "event": "response",
                    "time": _utc_now(),
                    "method": request.method.upper(),
                    "url": response.url,
                    "status": response.status,
                    "headers": _safe_headers(response.headers),
                    "body": _text_preview(body_text, args.body_preview_limit),
                },
            )

        context.on("request", on_request)
        context.on("response", on_response)

        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(args.url, wait_until="domcontentloaded")

        deadline = asyncio.get_running_loop().time() + args.timeout_seconds
        while context.pages and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(1)

        await context.close()

    status.update({"finished_at": _utc_now(), "status": "finished"})
    status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
