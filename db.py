import json

import aiosqlite

from config import DB_PATH
from scrapers.kamernet import serialize_kamernet_property_types


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
                attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent_at     TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source, listing_id)
            )
        """)
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
        await _ensure_column(db, "user_filters", "setup_in_progress", "INTEGER DEFAULT 0")
        if await _has_column(db, "user_filters", "kamernet_auto_reply"):
            await db.execute("""
                UPDATE user_filters
                SET auto_reply_enabled = kamernet_auto_reply
                WHERE auto_reply_enabled = 0 AND kamernet_auto_reply = 1
            """)
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
        await db.execute(
            "INSERT OR IGNORE INTO seen_listings (source, listing_id, url, title, price) VALUES (?,?,?,?,?)",
            (source, listing_id, url, title, price),
        )
        await db.commit()


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
                "attempted_at": row["attempted_at"],
                "sent_at": row["sent_at"],
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
) -> None:
    sent_statuses = {
        "sent",
        "submitted_unconfirmed",
        "sent_preapplication_pending",
        "sent_preapplication_failed",
        "preapplication_sent",
        "preapplication_submitted_unconfirmed",
    }
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO auto_replies (
                source, listing_id, url, triggered_by_chat_id, status, dry_run, error, sent_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END
            )
            ON CONFLICT(source, listing_id) DO UPDATE SET
                url=excluded.url,
                triggered_by_chat_id=excluded.triggered_by_chat_id,
                status=excluded.status,
                dry_run=excluded.dry_run,
                error=excluded.error,
                sent_at=CASE
                    WHEN excluded.sent_at IS NOT NULL THEN excluded.sent_at
                    ELSE auto_replies.sent_at
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
                status in sent_statuses,
            ),
        )
        await db.commit()
