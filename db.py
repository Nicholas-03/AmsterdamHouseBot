import json
import logging
from datetime import datetime, timezone

import aiosqlite

from config import DB_PATH
from notification_sources import DEFAULT_ENABLED_SOURCES_JSON, normalize_sources, serialize_sources
from scrapers.kamernet import serialize_kamernet_property_types

logger = logging.getLogger(__name__)

BOT_EVENT_RETENTION_DAYS = 3
STALE_AUTO_REPLY_ATTEMPT_MINUTES = 30
_BOT_EVENT_TEXT_LIMIT = 2000
_BOT_EVENT_DATA_LIMIT = 4000


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS seen_listings (
                source      TEXT NOT NULL,
                listing_id  TEXT NOT NULL,
                url         TEXT,
                title       TEXT,
                price       TEXT,
                scraped_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source, listing_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sent_listings (
                chat_id     INTEGER NOT NULL,
                source      TEXT NOT NULL,
                listing_id  TEXT NOT NULL,
                sent_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, source, listing_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_filters (
                chat_id       INTEGER PRIMARY KEY,
                city          TEXT    DEFAULT 'Amsterdam',
                max_price     INTEGER DEFAULT 2000,
                min_rooms     INTEGER DEFAULT 1,
                min_bedrooms  INTEGER DEFAULT 1,
                min_size_m2   INTEGER DEFAULT 0,
                kamernet_property_type TEXT DEFAULT 'any',
                auto_reply_enabled INTEGER DEFAULT 0,
                enabled_sources TEXT DEFAULT '["pararius", "funda", "kamernet", "huurwoningen", "roofz"]',
                neighborhoods TEXT    DEFAULT '[]',
                active        INTEGER DEFAULT 1,
                setup_in_progress INTEGER DEFAULT 0,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS kamernet_replies (
                source      TEXT NOT NULL DEFAULT 'kamernet',
                listing_id  TEXT NOT NULL,
                url         TEXT,
                triggered_by_chat_id INTEGER,
                status      TEXT NOT NULL,
                dry_run     INTEGER NOT NULL DEFAULT 1,
                error       TEXT,
                attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent_at     TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source, listing_id)
            )
        """)
        await _ensure_column(db, "kamernet_replies", "source", "TEXT NOT NULL DEFAULT 'kamernet'")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS auto_replies (
                source      TEXT NOT NULL,
                listing_id  TEXT NOT NULL,
                url         TEXT,
                triggered_by_chat_id INTEGER,
                status      TEXT NOT NULL,
                dry_run     INTEGER NOT NULL DEFAULT 1,
                error       TEXT,
                first_seen_at TIMESTAMP,
                attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent_at     TIMESTAMP,
                reply_latency_seconds REAL,
                confirmation_at TIMESTAMP,
                confirmation_latency_seconds REAL,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source, listing_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                level       TEXT NOT NULL DEFAULT 'info',
                event_type  TEXT NOT NULL,
                chat_id     INTEGER,
                source      TEXT,
                listing_id  TEXT,
                title       TEXT,
                status      TEXT,
                detail      TEXT,
                data_json   TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_events_created_at ON bot_events(created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_events_listing ON bot_events(source, listing_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_events_chat ON bot_events(chat_id)")
        await db.execute("""
            INSERT OR IGNORE INTO auto_replies (
                source, listing_id, url, triggered_by_chat_id, status, dry_run, error, attempted_at, sent_at, updated_at
            )
            SELECT source, listing_id, url, triggered_by_chat_id, status, dry_run, error, attempted_at, sent_at, updated_at
            FROM kamernet_replies
        """)
        await _ensure_column(db, "user_filters", "city", "TEXT DEFAULT 'Amsterdam'")
        await _ensure_column(db, "user_filters", "min_bedrooms", "INTEGER DEFAULT 1")
        await _ensure_column(db, "user_filters", "min_size_m2", "INTEGER DEFAULT 0")
        await _ensure_column(db, "user_filters", "kamernet_property_type", "TEXT DEFAULT 'any'")
        await _ensure_column(db, "user_filters", "auto_reply_enabled", "INTEGER DEFAULT 0")
        await _ensure_column(
            db,
            "user_filters",
            "enabled_sources",
            f"TEXT DEFAULT '{DEFAULT_ENABLED_SOURCES_JSON}'",
        )
        await _ensure_column(db, "user_filters", "setup_in_progress", "INTEGER DEFAULT 0")
        await _ensure_column(db, "auto_replies", "first_seen_at", "TIMESTAMP")
        await _ensure_column(db, "auto_replies", "reply_latency_seconds", "REAL")
        await _ensure_column(db, "auto_replies", "confirmation_at", "TIMESTAMP")
        await _ensure_column(db, "auto_replies", "confirmation_latency_seconds", "REAL")
        if await _has_column(db, "user_filters", "kamernet_auto_reply"):
            await db.execute("""
                UPDATE user_filters
                SET auto_reply_enabled = kamernet_auto_reply
                WHERE auto_reply_enabled = 0 AND kamernet_auto_reply = 1
            """)
        await _run_maintenance(db)
        await db.commit()


async def _ensure_column(db: aiosqlite.Connection, table: str, column: str, definition: str) -> None:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    if column not in {row[1] for row in rows}:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


async def _has_column(db: aiosqlite.Connection, table: str, column: str) -> bool:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    return column in {row[1] for row in rows}


async def mark_seen(source: str, listing_id: str, url: str = "", title: str = "", price: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "INSERT OR IGNORE INTO seen_listings (source, listing_id, url, title, price) VALUES (?,?,?,?,?)",
            (source, listing_id, url, title, price),
        )
        inserted = cursor.rowcount > 0
        async with db.execute(
            """
            SELECT source, listing_id, url, title, price, scraped_at
            FROM seen_listings
            WHERE source=? AND listing_id=?
            """,
            (source, listing_id),
        ) as cur:
            row = await cur.fetchone()
        await db.commit()
        if not row:
            return {
                "source": source,
                "listing_id": listing_id,
                "url": url,
                "title": title,
                "price": price,
                "scraped_at": "",
                "inserted": inserted,
            }
        result = dict(row)
        result["inserted"] = inserted
        return result


async def was_sent(chat_id: int, source: str, listing_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM sent_listings WHERE chat_id=? AND source=? AND listing_id=?",
            (chat_id, source, listing_id),
        ) as cur:
            return await cur.fetchone() is not None


async def mark_sent(chat_id: int, source: str, listing_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO sent_listings (chat_id, source, listing_id) VALUES (?, ?, ?)",
            (chat_id, source, listing_id),
        )
        await db.commit()


async def save_filters(
    chat_id: int,
    max_price: int,
    min_bedrooms: int,
    min_size_m2: int = 0,
    city: str = "Amsterdam",
    kamernet_property_type: str = "any",
    active: bool = True,
):
    kamernet_property_type = serialize_kamernet_property_types(kamernet_property_type)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO user_filters (chat_id, city, max_price, min_rooms, min_bedrooms, min_size_m2, kamernet_property_type, neighborhoods, active, setup_in_progress)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(chat_id) DO UPDATE SET
                city=excluded.city,
                max_price=excluded.max_price,
                min_rooms=excluded.min_rooms,
                min_bedrooms=excluded.min_bedrooms,
                min_size_m2=excluded.min_size_m2,
                kamernet_property_type=excluded.kamernet_property_type,
                active=excluded.active,
                setup_in_progress=0,
                updated_at=CURRENT_TIMESTAMP
        """, (
            chat_id,
            city,
            max_price,
            min_bedrooms,
            min_bedrooms,
            min_size_m2,
            kamernet_property_type,
            json.dumps([]),
            int(active),
        ))
        await db.commit()


async def get_filters(chat_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM user_filters WHERE chat_id=?", (chat_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            min_bedrooms = row["min_bedrooms"] if row["min_bedrooms"] is not None else row["min_rooms"]
            return {
                "chat_id": row["chat_id"],
                "city": row["city"] or "Amsterdam",
                "max_price": row["max_price"],
                "min_bedrooms": min_bedrooms,
                "min_size_m2": row["min_size_m2"] or 0,
                "kamernet_property_type": serialize_kamernet_property_types(row["kamernet_property_type"]),
                "auto_reply_enabled": bool(row["auto_reply_enabled"]),
                "enabled_sources": normalize_sources(row["enabled_sources"]),
                "active": bool(row["active"]),
                "setup_in_progress": bool(row["setup_in_progress"]),
            }


async def set_active(chat_id: int, active: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO user_filters (chat_id, active)
            VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                active=excluded.active,
                updated_at=CURRENT_TIMESTAMP
        """, (chat_id, int(active)))
        await db.commit()


async def set_setup_in_progress(chat_id: int, setup_in_progress: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE user_filters
            SET setup_in_progress=?, updated_at=CURRENT_TIMESTAMP
            WHERE chat_id=?
            """,
            (int(setup_in_progress), chat_id),
        )
        await db.commit()


async def log_event(
    event_type: str,
    *,
    level: str = "info",
    chat_id: int | None = None,
    source: str | None = None,
    listing_id: str | None = None,
    title: str = "",
    status: str = "",
    detail: str = "",
    data: dict | None = None,
) -> None:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await _insert_bot_event(
                db,
                event_type,
                level=level,
                chat_id=chat_id,
                source=source,
                listing_id=listing_id,
                title=title,
                status=status,
                detail=detail,
                data=data,
            )
            await db.commit()
    except Exception as exc:
        logger.warning("Could not write bot event %s: %s", event_type, exc)


async def prune_bot_events() -> None:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await _prune_bot_events(db)
            await db.commit()
    except Exception as exc:
        logger.warning("Could not prune bot events: %s", exc)


async def run_maintenance() -> None:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await _run_maintenance(db)
            await db.commit()
    except Exception as exc:
        logger.warning("Could not run database maintenance: %s", exc)


async def get_recent_bot_events(limit: int = 50) -> list[dict]:
    limit = max(1, min(limit, 200))
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT *
            FROM bot_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def _insert_bot_event(
    db: aiosqlite.Connection,
    event_type: str,
    *,
    level: str,
    chat_id: int | None,
    source: str | None,
    listing_id: str | None,
    title: str,
    status: str,
    detail: str,
    data: dict | None,
) -> None:
    data_json = ""
    if data:
        data_json = json.dumps(data, ensure_ascii=True, default=str)[:_BOT_EVENT_DATA_LIMIT]
    await db.execute(
        """
        INSERT INTO bot_events (
            level, event_type, chat_id, source, listing_id, title, status, detail, data_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            level[:20],
            event_type[:80],
            chat_id,
            source,
            listing_id,
            title[:300],
            status[:100],
            detail[:_BOT_EVENT_TEXT_LIMIT],
            data_json,
        ),
    )


async def _prune_bot_events(db: aiosqlite.Connection) -> None:
    await db.execute(
        "DELETE FROM bot_events WHERE created_at < datetime('now', ?)",
        (f"-{BOT_EVENT_RETENTION_DAYS} days",),
    )


async def _run_maintenance(db: aiosqlite.Connection) -> None:
    await _prune_bot_events(db)
    stale_count = await _mark_stale_auto_reply_attempts(db)
    if stale_count:
        await _insert_bot_event(
            db,
            "stale_auto_replies_marked",
            level="warning",
            chat_id=None,
            source=None,
            listing_id=None,
            title="",
            status="marked_interrupted",
            detail="Marked old auto-reply attempts as interrupted.",
            data={
                "count": stale_count,
                "timeout_minutes": STALE_AUTO_REPLY_ATTEMPT_MINUTES,
            },
        )


async def _mark_stale_auto_reply_attempts(db: aiosqlite.Connection) -> int:
    cursor = await db.execute(
        """
        UPDATE auto_replies
        SET
            status='error',
            error=CASE
                WHEN error IS NOT NULL AND length(trim(error)) > 0 THEN error
                ELSE 'Auto-reply attempt was interrupted before completion.'
            END,
            updated_at=CURRENT_TIMESTAMP
        WHERE status='attempting'
          AND attempted_at < datetime('now', ?)
        """,
        (f"-{STALE_AUTO_REPLY_ATTEMPT_MINUTES} minutes",),
    )
    return max(0, cursor.rowcount)


async def set_auto_reply(chat_id: int, enabled: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO user_filters (chat_id, auto_reply_enabled)
            VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                auto_reply_enabled=excluded.auto_reply_enabled,
                updated_at=CURRENT_TIMESTAMP
            """,
            (chat_id, int(enabled)),
        )
        await db.commit()


async def set_enabled_sources(chat_id: int, enabled_sources) -> None:
    normalized_sources = normalize_sources(enabled_sources)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO user_filters (chat_id, enabled_sources)
            VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                enabled_sources=excluded.enabled_sources,
                updated_at=CURRENT_TIMESTAMP
            """,
            (chat_id, serialize_sources(normalized_sources)),
        )
        await db.commit()


async def clear_seen(source: str | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if source:
            await db.execute("DELETE FROM seen_listings WHERE source=?", (source,))
            await db.execute("DELETE FROM sent_listings WHERE source=?", (source,))
        else:
            await db.execute("DELETE FROM seen_listings")
            await db.execute("DELETE FROM sent_listings")
        await db.commit()


async def get_all_active_users() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_filters WHERE active=1 AND setup_in_progress=0"
        ) as cur:
            rows = await cur.fetchall()
            return [
                {
                    "chat_id": row["chat_id"],
                    "city": row["city"] or "Amsterdam",
                    "max_price": row["max_price"],
                    "min_bedrooms": row["min_bedrooms"] if row["min_bedrooms"] is not None else row["min_rooms"],
                    "min_size_m2": row["min_size_m2"] or 0,
                    "kamernet_property_type": serialize_kamernet_property_types(row["kamernet_property_type"]),
                    "auto_reply_enabled": bool(row["auto_reply_enabled"]),
                    "enabled_sources": normalize_sources(row["enabled_sources"]),
                    "active": bool(row["active"]),
                    "setup_in_progress": bool(row["setup_in_progress"]),
                }
                for row in rows
            ]


async def get_kamernet_reply(listing_id: str) -> dict | None:
    return await get_auto_reply("kamernet", listing_id)


async def mark_kamernet_reply_result(
    listing_id: str,
    url: str,
    triggered_by_chat_id: int,
    status: str,
    dry_run: bool,
    error: str = "",
) -> None:
    await mark_auto_reply_result(
        "kamernet",
        listing_id,
        url,
        triggered_by_chat_id,
        status,
        dry_run,
        error,
    )


async def get_auto_reply(source: str, listing_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM auto_replies WHERE source=? AND listing_id=?",
            (source, listing_id),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return {
                "source": row["source"],
                "listing_id": row["listing_id"],
                "url": row["url"],
                "triggered_by_chat_id": row["triggered_by_chat_id"],
                "status": row["status"],
                "dry_run": bool(row["dry_run"]),
                "error": row["error"],
                "first_seen_at": row["first_seen_at"],
                "attempted_at": row["attempted_at"],
                "sent_at": row["sent_at"],
                "reply_latency_seconds": row["reply_latency_seconds"],
                "confirmation_at": row["confirmation_at"],
                "confirmation_latency_seconds": row["confirmation_latency_seconds"],
                "updated_at": row["updated_at"],
            }


async def mark_auto_reply_result(
    source: str,
    listing_id: str,
    url: str,
    triggered_by_chat_id: int,
    status: str,
    dry_run: bool,
    error: str = "",
    *,
    first_seen_at: str | datetime | None = None,
    sent_at: str | datetime | None = None,
    confirmation_at: str | datetime | None = None,
) -> None:
    sent_statuses = {
        "sent",
        "submitted_unconfirmed",
        "confirmation_confirmed",
        "confirmation_error",
        "confirmation_missing",
        "sent_preapplication_pending",
        "sent_preapplication_failed",
        "preapplication_confirmed",
        "preapplication_confirmation_missing",
        "preapplication_sent",
        "preapplication_submitted_unconfirmed",
    }
    first_seen_at_text = _format_timestamp(first_seen_at)
    sent_at_text = _format_timestamp(sent_at)
    confirmation_at_text = _format_timestamp(confirmation_at)
    if not sent_at_text and status in sent_statuses:
        sent_at_text = _format_timestamp(_utc_now())

    reply_latency_seconds = _elapsed_seconds(first_seen_at_text, sent_at_text)
    confirmation_latency_seconds = _elapsed_seconds(first_seen_at_text, confirmation_at_text)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO auto_replies (
                source, listing_id, url, triggered_by_chat_id, status, dry_run, error,
                first_seen_at, sent_at, reply_latency_seconds, confirmation_at, confirmation_latency_seconds
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(source, listing_id) DO UPDATE SET
                url=excluded.url,
                triggered_by_chat_id=excluded.triggered_by_chat_id,
                status=excluded.status,
                dry_run=excluded.dry_run,
                error=excluded.error,
                first_seen_at=CASE
                    WHEN auto_replies.first_seen_at IS NOT NULL THEN auto_replies.first_seen_at
                    ELSE excluded.first_seen_at
                END,
                sent_at=CASE
                    WHEN excluded.sent_at IS NOT NULL THEN excluded.sent_at
                    ELSE auto_replies.sent_at
                END,
                reply_latency_seconds=CASE
                    WHEN excluded.reply_latency_seconds IS NOT NULL THEN excluded.reply_latency_seconds
                    ELSE auto_replies.reply_latency_seconds
                END,
                confirmation_at=CASE
                    WHEN excluded.confirmation_at IS NOT NULL THEN excluded.confirmation_at
                    ELSE auto_replies.confirmation_at
                END,
                confirmation_latency_seconds=CASE
                    WHEN excluded.confirmation_latency_seconds IS NOT NULL THEN excluded.confirmation_latency_seconds
                    ELSE auto_replies.confirmation_latency_seconds
                END,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                source,
                listing_id,
                url,
                triggered_by_chat_id,
                status,
                int(dry_run),
                error[:1000],
                first_seen_at_text,
                sent_at_text,
                reply_latency_seconds,
                confirmation_at_text,
                confirmation_latency_seconds,
            ),
        )
        await db.commit()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_timestamp(value: str | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _parse_timestamp(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = value.strip()
        if not normalized:
            return None
        normalized = normalized.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            try:
                parsed = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _elapsed_seconds(start: str | datetime | None, end: str | datetime | None) -> float | None:
    start_dt = _parse_timestamp(start)
    end_dt = _parse_timestamp(end)
    if not start_dt or not end_dt:
        return None
    return max(0.0, round((end_dt - start_dt).total_seconds(), 3))
