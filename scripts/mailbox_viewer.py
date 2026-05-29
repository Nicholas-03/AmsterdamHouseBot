from __future__ import annotations

import argparse
from email import policy
from email.parser import Parser
from html import escape
import io
import json
import os
from pathlib import Path
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse
import webbrowser
import zipfile

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from cloudflare_mailbox import CloudflareMailboxAuthSettings, CloudflareMailboxClient

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_LIMIT = 50


def main() -> int:
    parser = argparse.ArgumentParser(description="Open a local web inbox for the Cloudflare housing mailbox.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host. Defaults to localhost only.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Default number of messages to list.")
    parser.add_argument("--api-base", default="", help="Override CLOUDFLARE_MAILBOX_API_BASE.")
    parser.add_argument("--api-token", default="", help="Override CLOUDFLARE_MAILBOX_API_TOKEN.")
    parser.add_argument("--address", default="", help="Override CLOUDFLARE_MAILBOX_ADDRESS.")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically.")
    parser.add_argument("--check", action="store_true", help="Only verify the mailbox API and print message count.")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    settings = _settings_from_args(args)
    ready_error = settings.ready_error()
    if ready_error:
        print(f"Cannot open mailbox viewer: {ready_error}", file=sys.stderr)
        return 2

    if args.check:
        with CloudflareMailboxClient(settings) as client:
            messages = client.list_messages()
        print(f"Mailbox API OK. Showing {len(messages)} messages from {settings.address or 'configured mailbox'}.")
        return 0

    server = ThreadingHTTPServer((args.host, args.port), _handler(settings, args.limit))
    url = f"http://{args.host}:{args.port}/"
    print(f"Mailbox viewer running at {url}")
    print("Press Ctrl+C to stop.")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("Warning: this viewer has no login screen. Keep it bound to localhost unless you know why.")
    if not args.no_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping mailbox viewer.")
    finally:
        server.server_close()
    return 0


def _settings_from_args(args) -> CloudflareMailboxAuthSettings:
    return CloudflareMailboxAuthSettings(
        api_base=(args.api_base or os.getenv("CLOUDFLARE_MAILBOX_API_BASE", "")).rstrip("/"),
        api_token=args.api_token or os.getenv("CLOUDFLARE_MAILBOX_API_TOKEN", ""),
        address=args.address or os.getenv("CLOUDFLARE_MAILBOX_ADDRESS", "") or os.getenv("HOUSING_EMAIL", ""),
        max_results=max(1, min(int(args.limit or DEFAULT_LIMIT), 100)),
    )


def _handler(settings: CloudflareMailboxAuthSettings, default_limit: int):
    class MailboxViewerHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/":
                    return self._send_html(_index_html(settings.address))
                if parsed.path == "/api/messages":
                    query = parse_qs(parsed.query)
                    limit = _parse_limit(query.get("limit", [default_limit])[0])
                    return self._send_json(_list_messages(settings, limit))
                if parsed.path == "/api/export.zip":
                    return self._send_bytes(
                        _export_zip(settings),
                        "application/zip",
                        {"Content-Disposition": 'attachment; filename="housing-mailbox.eml.zip"'},
                    )
                message_id = _match_message_path(parsed.path, suffix="")
                if message_id:
                    return self._send_json(_get_message(settings, message_id))
                eml_id = _match_message_path(parsed.path, suffix="/eml")
                if eml_id:
                    message = _get_message(settings, eml_id)
                    filename = _safe_filename(message.get("subject") or eml_id, "eml")
                    return self._send_bytes(
                        (message.get("raw") or "").encode("utf-8", errors="replace"),
                        "message/rfc822; charset=utf-8",
                        {"Content-Disposition": f'attachment; filename="{filename}"'},
                    )
                return self._send_json({"error": "not_found"}, status=404)
            except Exception as exc:
                return self._send_json({"error": str(exc)}, status=500)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                message_id = _match_message_path(parsed.path, suffix="/seen")
                if not message_id:
                    return self._send_json({"error": "not_found"}, status=404)
                with CloudflareMailboxClient(settings) as client:
                    client.mark_seen(message_id)
                return self._send_json({"ok": True})
            except Exception as exc:
                return self._send_json({"error": str(exc)}, status=500)

        def log_message(self, fmt: str, *args) -> None:
            print(f"{self.address_string()} - {fmt % args}")

        def _send_html(self, body: str) -> None:
            self._send_bytes(body.encode("utf-8"), "text/html; charset=utf-8")

        def _send_json(self, payload: dict | list, status: int = 200) -> None:
            self._send_bytes(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                status=status,
            )

        def _send_bytes(self, body: bytes, content_type: str, headers: dict[str, str] | None = None, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return MailboxViewerHandler


def _list_messages(settings: CloudflareMailboxAuthSettings, limit: int) -> dict:
    limited_settings = CloudflareMailboxAuthSettings(
        api_base=settings.api_base,
        api_token=settings.api_token,
        address=settings.address,
        max_results=limit,
    )
    with CloudflareMailboxClient(limited_settings) as client:
        messages = client.list_messages()
    return {"address": settings.address, "messages": messages}


def _get_message(settings: CloudflareMailboxAuthSettings, message_id: str) -> dict:
    with CloudflareMailboxClient(settings) as client:
        message = client.get_message(message_id)
    raw = message.get("raw") or ""
    parsed_text, parsed_html = _parse_raw_message(raw)
    text = "\n\n".join(part for part in parsed_text if part).strip()
    html_parts = parsed_html or [part for part in message.get("html", []) if isinstance(part, str)]
    message["display_text"] = text or _strip_headers(message.get("text") or raw)
    message["display_html"] = "\n\n".join(html_parts)
    return message


def _export_zip(settings: CloudflareMailboxAuthSettings) -> bytes:
    buffer = io.BytesIO()
    export_settings = CloudflareMailboxAuthSettings(
        api_base=settings.api_base,
        api_token=settings.api_token,
        address=settings.address,
        max_results=100,
    )
    with CloudflareMailboxClient(export_settings) as client:
        messages = client.list_messages()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, summary in enumerate(messages, start=1):
                message_id = summary.get("id") or ""
                if not message_id:
                    continue
                full = client.get_message(message_id)
                subject = full.get("subject") or summary.get("subject") or f"message-{index}"
                created_at = str(full.get("createdAt") or summary.get("createdAt") or "").replace(":", "-")
                filename = _safe_filename(f"{index:03d}-{created_at}-{subject}", "eml")
                archive.writestr(filename, full.get("raw") or "")
    return buffer.getvalue()


def _parse_raw_message(raw: str) -> tuple[list[str], list[str]]:
    if not raw:
        return [], []
    try:
        parsed = Parser(policy=policy.default).parsestr(raw)
    except Exception:
        return [raw], []

    parts = parsed.walk() if parsed.is_multipart() else [parsed]
    text_parts: list[str] = []
    html_parts: list[str] = []
    for part in parts:
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True)
            content = payload.decode(errors="replace") if isinstance(payload, bytes) else str(payload or "")
        if content_type == "text/html":
            html_parts.append(content)
        else:
            text_parts.append(content)
    return text_parts, html_parts


def _strip_headers(value: str) -> str:
    if "\n\n" in value:
        return value.split("\n\n", 1)[1].strip()
    if "\r\n\r\n" in value:
        return value.split("\r\n\r\n", 1)[1].strip()
    return value.strip()


def _match_message_path(path: str, suffix: str) -> str:
    pattern = rf"^/api/messages/([^/]+){re.escape(suffix)}$"
    match = re.match(pattern, path)
    return unquote(match.group(1)) if match else ""


def _parse_limit(value) -> int:
    try:
        return max(1, min(int(value), 100))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


def _safe_filename(value: str, extension: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")[:80] or "message"
    return f"{stem}.{extension}"


def _index_html(address: str) -> str:
    safe_address = escape(address or "housing mailbox")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Housing Mailbox</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f7f8;
      --panel: #ffffff;
      --line: #d9e0e4;
      --muted: #5d6b73;
      --text: #182126;
      --accent: #0f766e;
      --accent-soft: #e1f5f2;
      --warn: #a16207;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: var(--bg);
    }}
    header {{
      height: 58px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 0 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }}
    h1 {{ font-size: 17px; margin: 0; font-weight: 650; }}
    .subtle {{ color: var(--muted); font-size: 12px; }}
    .header-actions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    main {{
      display: grid;
      grid-template-columns: minmax(280px, 390px) minmax(0, 1fr);
      height: calc(100vh - 58px);
    }}
    aside {{
      border-right: 1px solid var(--line);
      background: var(--panel);
      min-width: 0;
      display: flex;
      flex-direction: column;
    }}
    .toolbar {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
    }}
    .list-status {{
      min-height: 34px;
      display: flex;
      align-items: center;
      padding: 0 12px;
      border-bottom: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
    }}
    input, button {{
      height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      font: inherit;
      background: #fff;
    }}
    input {{ padding: 0 10px; min-width: 0; }}
    button {{
      padding: 0 12px;
      cursor: pointer;
      color: var(--text);
    }}
    button.primary {{
      border-color: var(--accent);
      background: var(--accent);
      color: white;
    }}
    #list {{
      overflow: auto;
      min-height: 0;
    }}
    .message-row {{
      display: grid;
      gap: 4px;
      height: auto;
      min-height: 66px;
      width: 100%;
      padding: 12px;
      border: 0;
      border-bottom: 1px solid var(--line);
      text-align: left;
      border-radius: 0;
      background: #fff;
      color: var(--text);
    }}
    .message-row:hover, .message-row.active {{ background: #f0faf8; }}
    .message-row.unread .subject {{ font-weight: 750; }}
    .subject {{
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .meta {{
      color: var(--muted);
      font-size: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .badge {{
      display: inline-block;
      margin-left: 6px;
      padding: 1px 6px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 11px;
      font-weight: 650;
    }}
    section {{
      min-width: 0;
      overflow: auto;
      padding: 18px;
    }}
    .detail {{
      max-width: 980px;
      margin: 0 auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      min-height: calc(100vh - 96px);
      display: flex;
      flex-direction: column;
    }}
    .detail-head {{
      padding: 18px;
      border-bottom: 1px solid var(--line);
    }}
    .detail-title {{
      margin: 0 0 8px;
      font-size: 20px;
      line-height: 1.25;
    }}
    .actions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 14px;
    }}
    .links {{
      display: grid;
      gap: 6px;
      padding: 12px 18px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfc;
    }}
    .links a {{
      color: var(--accent);
      word-break: break-all;
    }}
    .tabs {{
      display: flex;
      gap: 8px;
      padding: 12px 18px 0;
    }}
    .tab.active {{
      border-color: var(--accent);
      background: var(--accent-soft);
      color: var(--accent);
    }}
    pre {{
      margin: 0;
      padding: 18px;
      white-space: pre-wrap;
      word-break: break-word;
      font: 13px/1.5 ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
    }}
    iframe {{
      width: 100%;
      min-height: 620px;
      border: 0;
      background: white;
    }}
    .empty {{
      height: 100%;
      display: grid;
      place-items: center;
      color: var(--muted);
      text-align: center;
      padding: 24px;
    }}
    .error {{ color: #991b1b; }}
    @media (max-width: 820px) {{
      main {{ grid-template-columns: 1fr; height: auto; }}
      aside {{ height: 42vh; border-right: 0; border-bottom: 1px solid var(--line); }}
      section {{ padding: 10px; }}
      .detail {{ min-height: 50vh; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Housing Mailbox</h1>
      <div class="subtle">{safe_address}</div>
    </div>
    <div class="header-actions">
      <button onclick="window.location='/api/export.zip'">Download all .eml</button>
      <button class="primary" onclick="loadMessages()">Refresh</button>
    </div>
  </header>
  <main>
    <aside>
      <div class="toolbar">
        <input id="search" placeholder="Search subject or sender" oninput="renderList()">
        <button onclick="loadMessages()">Reload</button>
      </div>
      <div id="list-status" class="list-status">Loading mailbox...</div>
      <div id="list"></div>
    </aside>
    <section>
      <div id="detail" class="detail">
        <div class="empty">Select an email to read it.</div>
      </div>
    </section>
  </main>
  <script>
    let messages = [];
    let selectedId = "";
    let selectedMessage = null;
    let activeTab = "text";

    function fmtDate(value) {{
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      return date.toLocaleString();
    }}

    async function requestJson(url, options) {{
      const response = await fetch(url, options);
      const payload = await response.json().catch(() => ({{ error: response.statusText }}));
      if (!response.ok) throw new Error(payload.error || response.statusText);
      return payload;
    }}

    async function loadMessages() {{
      const list = document.getElementById("list");
      const status = document.getElementById("list-status");
      status.textContent = "Loading mailbox...";
      list.innerHTML = '<div class="empty">Loading emails...</div>';
      try {{
        const payload = await requestJson("/api/messages?limit=100");
        messages = payload.messages || [];
        renderList();
      }} catch (error) {{
        status.textContent = "Mailbox API error";
        list.innerHTML = `<div class="empty error">${{escapeHtml(error.message)}}</div>`;
      }}
    }}

    function renderList() {{
      const query = document.getElementById("search").value.toLowerCase().trim();
      const filtered = messages.filter((message) => {{
        const sender = (message.from && message.from.address) || "";
        return !query || `${{message.subject || ""}} ${{sender}}`.toLowerCase().includes(query);
      }});
      const list = document.getElementById("list");
      const status = document.getElementById("list-status");
      const unread = messages.filter((message) => !message.seen).length;
      status.textContent = `${{filtered.length}} email${{filtered.length === 1 ? "" : "s"}} shown · ${{unread}} unread`;
      if (!filtered.length) {{
        list.innerHTML = '<div class="empty">No matching emails.</div>';
        return;
      }}
      list.innerHTML = filtered.map((message) => {{
        const sender = (message.from && message.from.address) || "";
        const unread = message.seen ? "" : " unread";
        const active = message.id === selectedId ? " active" : "";
        const links = Array.isArray(message.links) && message.links.length ? `<span class="badge">${{message.links.length}} links</span>` : "";
        const forward = message.forward && message.forward.status ? `<span class="badge">forward: ${{escapeHtml(message.forward.status)}}</span>` : "";
        return `
          <button class="message-row${{unread}}${{active}}" onclick="openMessage('${{message.id}}')">
            <div class="subject">${{escapeHtml(message.subject || "(no subject)")}}${{links}}${{forward}}</div>
            <div class="meta">${{escapeHtml(sender)}} · ${{escapeHtml(fmtDate(message.createdAt))}}</div>
          </button>`;
      }}).join("");
    }}

    async function openMessage(id) {{
      selectedId = id;
      selectedMessage = null;
      renderList();
      document.getElementById("detail").innerHTML = '<div class="empty">Opening email...</div>';
      try {{
        selectedMessage = await requestJson(`/api/messages/${{encodeURIComponent(id)}}`);
        activeTab = selectedMessage.display_html ? "html" : "text";
        renderDetail();
      }} catch (error) {{
        document.getElementById("detail").innerHTML = `<div class="empty error">${{escapeHtml(error.message)}}</div>`;
      }}
    }}

    async function markSeen() {{
      if (!selectedId) return;
      await requestJson(`/api/messages/${{encodeURIComponent(selectedId)}}/seen`, {{ method: "POST" }});
      const item = messages.find((message) => message.id === selectedId);
      if (item) item.seen = true;
      if (selectedMessage) selectedMessage.seen = true;
      renderList();
      renderDetail();
    }}

    function setTab(tab) {{
      activeTab = tab;
      renderDetail();
    }}

    function renderDetail() {{
      const message = selectedMessage;
      if (!message) return;
      const sender = (message.from && message.from.address) || "";
      const links = Array.isArray(message.links) ? message.links : [];
      const forward = message.forward || null;
      const forwardText = forward && forward.status
        ? `Forward: ${{forward.status}}${{forward.to ? " to " + forward.to : ""}}${{forward.error ? " (" + forward.error + ")" : ""}}`
        : "";
      const detail = document.getElementById("detail");
      detail.innerHTML = `
        <div class="detail-head">
          <h2 class="detail-title">${{escapeHtml(message.subject || "(no subject)")}}</h2>
          <div class="meta">From: ${{escapeHtml(sender)}}</div>
          <div class="meta">To: ${{escapeHtml(message.to || "")}}</div>
          <div class="meta">Date: ${{escapeHtml(fmtDate(message.createdAt))}}</div>
          ${{forwardText ? `<div class="meta">${{escapeHtml(forwardText)}}</div>` : ""}}
          <div class="actions">
            <button onclick="markSeen()">${{message.seen ? "Seen" : "Mark seen"}}</button>
            <button onclick="window.location='/api/messages/${{encodeURIComponent(message.id)}}/eml'">Download .eml</button>
          </div>
        </div>
        ${{links.length ? `<div class="links"><strong>Links</strong>${{links.map((link) => `<a href="${{escapeAttr(link)}}" target="_blank" rel="noreferrer">${{escapeHtml(link)}}</a>`).join("")}}</div>` : ""}}
        <div class="tabs">
          <button class="tab ${{activeTab === "text" ? "active" : ""}}" onclick="setTab('text')">Text</button>
          <button class="tab ${{activeTab === "html" ? "active" : ""}}" onclick="setTab('html')">HTML</button>
          <button class="tab ${{activeTab === "raw" ? "active" : ""}}" onclick="setTab('raw')">Raw</button>
        </div>
        <div id="body"></div>
      `;
      const body = document.getElementById("body");
      if (activeTab === "html") {{
        body.innerHTML = '<iframe sandbox=""></iframe>';
        body.querySelector("iframe").srcdoc = message.display_html || "<p>No HTML body.</p>";
      }} else {{
        const value = activeTab === "raw" ? message.raw : message.display_text;
        body.innerHTML = `<pre>${{escapeHtml(value || "No body.")}}</pre>`;
      }}
    }}

    function escapeHtml(value) {{
      return String(value || "").replace(/[&<>"']/g, (char) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }}[char]));
    }}

    function escapeAttr(value) {{
      return escapeHtml(value).replace(/`/g, "&#96;");
    }}

    loadMessages();
  </script>
</body>
</html>"""


if __name__ == "__main__":
    raise SystemExit(main())
