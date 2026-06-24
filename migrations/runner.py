"""
Migration runner.

Each migration is a function named migration_NNN_description.
The runner checks which have already been applied (tracked in the
`schema_migrations` table) and runs only the new ones — in order.

To add a new migration:
  1. Add a function: async def migration_004_your_change(conn): ...
  2. Register it at the bottom of MIGRATIONS list.
"""

import os

from dotenv import load_dotenv
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncConnection

load_dotenv()

DATABASE_URL: str = os.getenv("DATABASE_URL") or ""


# ── Migration helpers ─────────────────────────────────────────────────────────

async def _table_exists(conn: AsyncConnection, table: str) -> bool:
    result = await conn.execute(text(
        "SELECT EXISTS ("
        "  SELECT FROM information_schema.tables"
        "  WHERE table_schema = 'public' AND table_name = :t"
        ")"
    ), {"t": table})
    return result.scalar()


async def _column_exists(conn: AsyncConnection, table: str, column: str) -> bool:
    result = await conn.execute(text(
        "SELECT EXISTS ("
        "  SELECT FROM information_schema.columns"
        "  WHERE table_schema = 'public'"
        "  AND table_name = :t AND column_name = :c"
        ")"
    ), {"t": table, "c": column})
    return result.scalar()


# ── Tracking table ────────────────────────────────────────────────────────────

async def _ensure_tracking_table(conn: AsyncConnection):
    """Create schema_migrations table if it doesn't exist."""
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id         SERIAL PRIMARY KEY,
            name       VARCHAR(255) UNIQUE NOT NULL,
            applied_at TIMESTAMPTZ DEFAULT NOW()
        )
    """))


async def _is_applied(conn: AsyncConnection, name: str) -> bool:
    result = await conn.execute(
        text("SELECT 1 FROM schema_migrations WHERE name = :n"),
        {"n": name},
    )
    return result.fetchone() is not None


async def _mark_applied(conn: AsyncConnection, name: str):
    await conn.execute(
        text("INSERT INTO schema_migrations (name) VALUES (:n)"),
        {"n": name},
    )


# ── Individual migrations ─────────────────────────────────────────────────────

async def migration_001_create_assistants(conn: AsyncConnection):
    """Create assistants table."""
    if await _table_exists(conn, "assistants"):
        logger.info("[Migration 001] assistants table already exists — skipped")
        return

    await conn.execute(text("""
        CREATE TABLE assistants (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name                 VARCHAR(255) NOT NULL,
            system_prompt        TEXT NOT NULL,
            welcome_message      VARCHAR(500) DEFAULT 'Hello. I am Ciya. How can I help you?',
            default_language     VARCHAR(50)  DEFAULT 'english',
            voice                VARCHAR(50)  DEFAULT 'priya',
            llm_model            VARCHAR(100) DEFAULT 'gpt-4o-mini',
            temperature          FLOAT        DEFAULT 0.2,
            business_hours_start VARCHAR(10)  DEFAULT '10:30',
            business_hours_end   VARCHAR(10)  DEFAULT '18:30',
            status               VARCHAR(20)  DEFAULT 'development',
            created_at           TIMESTAMPTZ  DEFAULT NOW(),
            updated_at           TIMESTAMPTZ  DEFAULT NOW()
        )
    """))
    logger.info("[Migration 001] Created assistants table")


async def migration_002_create_plivo_numbers(conn: AsyncConnection):
    """Create plivo_numbers table."""
    if await _table_exists(conn, "plivo_numbers"):
        logger.info("[Migration 002] plivo_numbers table already exists — skipped")
        return

    await conn.execute(text("""
        CREATE TABLE plivo_numbers (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            number            VARCHAR(30)  UNIQUE NOT NULL,
            friendly_name     VARCHAR(255) DEFAULT '',
            assistant_id      UUID         REFERENCES assistants(id) ON DELETE SET NULL,
            webhook_configured BOOLEAN     DEFAULT FALSE
        )
    """))
    logger.info("[Migration 002] Created plivo_numbers table")


async def migration_003_add_assistant_status(conn: AsyncConnection):
    """Add status column to assistants if missing (for existing databases)."""
    if not await _table_exists(conn, "assistants"):
        logger.info("[Migration 003] assistants table not found — skipped (will be created by 001)")
        return

    if await _column_exists(conn, "assistants", "status"):
        logger.info("[Migration 003] assistants.status already exists — skipped")
        return

    await conn.execute(text(
        "ALTER TABLE assistants ADD COLUMN status VARCHAR(20) DEFAULT 'development'"
    ))
    logger.info("[Migration 003] Added assistants.status column")


# ── Registry — add new migrations here in order ───────────────────────────────

MIGRATIONS = [
    ("001_create_assistants",       migration_001_create_assistants),
    ("002_create_plivo_numbers",    migration_002_create_plivo_numbers),
    ("003_add_assistant_status",    migration_003_add_assistant_status),
]


# ── Runner ────────────────────────────────────────────────────────────────────

async def run_migrations():
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL missing — add it to .env")

    engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)

    try:
        async with engine.begin() as conn:
            await _ensure_tracking_table(conn)

            applied = 0
            for name, fn in MIGRATIONS:
                if await _is_applied(conn, name):
                    logger.debug(f"[Migration] {name} already applied — skipped")
                    continue

                logger.info(f"[Migration] Applying {name} …")
                await fn(conn)
                await _mark_applied(conn, name)
                applied += 1

            if applied == 0:
                logger.info("[Migration] All migrations already applied — nothing to do")
            else:
                logger.info(f"[Migration] {applied} migration(s) applied successfully")
    finally:
        await engine.dispose()
