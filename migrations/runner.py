"""
Migration runner.

Each migration is a function named migration_NNN_description.
The runner checks which have already been applied (tracked in the
`schema_migrations` table) and runs only the new ones — in order.

To add a new migration:
  1. Add a function: async def migration_004_your_change(conn): ...
  2. Register it at the bottom of MIGRATIONS list.
"""

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from database.connection import engine


# ── Migration helpers ─────────────────────────────────────────────────────────

async def _table_exists(conn: AsyncConnection, table: str, schema: str = "public") -> bool:
    result = await conn.execute(text(
        "SELECT EXISTS ("
        "  SELECT FROM information_schema.tables"
        "  WHERE table_schema = :s AND table_name = :t"
        ")"
    ), {"s": schema, "t": table})
    # .scalar() is typed Any | None (a raw NULL is possible in general), but
    # this query is a bare `SELECT EXISTS(...)` which Postgres always
    # evaluates to true/false, never NULL — bool() makes that guarantee
    # explicit to the type checker instead of suppressing the mismatch.
    return bool(result.scalar())


async def _column_exists(conn: AsyncConnection, table: str, column: str, schema: str = "public") -> bool:
    result = await conn.execute(text(
        "SELECT EXISTS ("
        "  SELECT FROM information_schema.columns"
        "  WHERE table_schema = :s"
        "  AND table_name = :t AND column_name = :c"
        ")"
    ), {"s": schema, "t": table, "c": column})
    return bool(result.scalar())


async def _constraint_exists(conn: AsyncConnection, table: str, constraint: str, schema: str = "public") -> bool:
    result = await conn.execute(text(
        "SELECT EXISTS ("
        "  SELECT FROM information_schema.table_constraints"
        "  WHERE table_schema = :s"
        "  AND table_name = :t AND constraint_name = :c"
        ")"
    ), {"s": schema, "t": table, "c": constraint})
    return bool(result.scalar())


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
            assistant_id      UUID,
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


async def migration_004_add_webhook_urls(conn: AsyncConnection):
    """Add prefetch_webhook_url and end_of_call_webhook_url columns to assistants."""
    if not await _table_exists(conn, "assistants"):
        logger.info("[Migration 004] assistants table not found — skipped")
        return

    for col in ("prefetch_webhook_url", "end_of_call_webhook_url"):
        if not await _column_exists(conn, "assistants", col):
            await conn.execute(text(
                f"ALTER TABLE assistants ADD COLUMN {col} TEXT"
            ))
            logger.info(f"[Migration 004] Added assistants.{col} column")
        else:
            logger.info(f"[Migration 004] assistants.{col} already exists — skipped")


async def migration_005_create_call_logs(conn: AsyncConnection):
    """Create call_logs table to store per-call details."""
    if await _table_exists(conn, "call_logs"):
        logger.info("[Migration 005] call_logs table already exists — skipped")
        return

    await conn.execute(text("""
        CREATE TABLE call_logs (
            id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id     VARCHAR(255) NOT NULL,
            assistant_id   UUID,
            assistant_name VARCHAR(255) DEFAULT '',
            from_number    VARCHAR(50)  DEFAULT '',
            to_number      VARCHAR(50)  DEFAULT '',
            duration       INTEGER      DEFAULT 0,
            chat           TEXT,
            call_status    VARCHAR(50)  DEFAULT 'user-ended',
            error_message  TEXT,
            chars_used     INTEGER      DEFAULT 0,
            started_at     TIMESTAMPTZ  NOT NULL,
            ended_at       TIMESTAMPTZ  NOT NULL
        )
    """))
    await conn.execute(text(
        "CREATE INDEX idx_call_logs_session_id ON call_logs(session_id)"
    ))
    await conn.execute(text(
        "CREATE INDEX idx_call_logs_started_at ON call_logs(started_at DESC)"
    ))
    logger.info("[Migration 005] Created call_logs table")


async def migration_006_add_recording_url(conn: AsyncConnection):
    """Add recording_url column to call_logs."""
    if not await _table_exists(conn, "call_logs"):
        logger.info("[Migration 006] call_logs table not found — skipped")
        return

    if not await _column_exists(conn, "call_logs", "recording_url"):
        await conn.execute(text("ALTER TABLE call_logs ADD COLUMN recording_url TEXT"))
        logger.info("[Migration 006] Added call_logs.recording_url column")
    else:
        logger.info("[Migration 006] call_logs.recording_url already exists — skipped")


async def migration_007_add_cost_columns(conn: AsyncConnection):
    """Add cost_breakdown (JSON text) and total_cost (float) to call_logs."""
    if not await _table_exists(conn, "call_logs"):
        logger.info("[Migration 007] call_logs table not found — skipped")
        return

    for col, definition in [
        ("cost_breakdown", "TEXT"),
        ("total_cost",     "DOUBLE PRECISION"),
    ]:
        if not await _column_exists(conn, "call_logs", col):
            await conn.execute(text(f"ALTER TABLE call_logs ADD COLUMN {col} {definition}"))
            logger.info(f"[Migration 007] Added call_logs.{col} column")
        else:
            logger.info(f"[Migration 007] call_logs.{col} already exists — skipped")


async def migration_008_add_number_details(conn: AsyncConnection):
    """Add country and number_type columns to plivo_numbers."""
    if not await _table_exists(conn, "plivo_numbers"):
        logger.info("[Migration 008] plivo_numbers table not found — skipped")
        return

    for col, definition in [
        ("country",     "VARCHAR(100) DEFAULT ''"),
        ("number_type", "VARCHAR(50)  DEFAULT ''"),
    ]:
        if not await _column_exists(conn, "plivo_numbers", col):
            await conn.execute(text(f"ALTER TABLE plivo_numbers ADD COLUMN {col} {definition}"))
            logger.info(f"[Migration 008] Added plivo_numbers.{col} column")
        else:
            logger.info(f"[Migration 008] plivo_numbers.{col} already exists — skipped")


async def migration_009_create_app_settings(conn: AsyncConnection):
    """Create app_settings key-value table for runtime configuration."""
    if await _table_exists(conn, "app_settings"):
        logger.info("[Migration 009] app_settings table already exists — skipped")
        return

    await conn.execute(text("""
        CREATE TABLE app_settings (
            key   VARCHAR(100) PRIMARY KEY,
            value TEXT
        )
    """))
    logger.info("[Migration 009] Created app_settings table")


async def migration_010_add_org_id_assistants(conn: AsyncConnection):
    """Add organization_id to assistants for multi-tenant support."""
    if not await _table_exists(conn, "assistants"):
        logger.info("[Migration 010] assistants table not found — skipped")
        return

    if not await _column_exists(conn, "assistants", "organization_id"):
        await conn.execute(text(
            "ALTER TABLE assistants ADD COLUMN organization_id VARCHAR(36)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_assistants_org_id ON assistants(organization_id)"
        ))
        logger.info("[Migration 010] Added assistants.organization_id")
    else:
        logger.info("[Migration 010] assistants.organization_id already exists — skipped")


async def migration_011_add_org_id_plivo_numbers(conn: AsyncConnection):
    """Add organization_id to plivo_numbers for multi-tenant support."""
    if not await _table_exists(conn, "plivo_numbers"):
        logger.info("[Migration 011] plivo_numbers table not found — skipped")
        return

    if not await _column_exists(conn, "plivo_numbers", "organization_id"):
        await conn.execute(text(
            "ALTER TABLE plivo_numbers ADD COLUMN organization_id VARCHAR(36)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_plivo_numbers_org_id ON plivo_numbers(organization_id)"
        ))
        logger.info("[Migration 011] Added plivo_numbers.organization_id")
    else:
        logger.info("[Migration 011] plivo_numbers.organization_id already exists — skipped")


async def migration_012_add_org_id_call_logs(conn: AsyncConnection):
    """Add organization_id to call_logs for multi-tenant support."""
    if not await _table_exists(conn, "call_logs"):
        logger.info("[Migration 012] call_logs table not found — skipped")
        return

    if not await _column_exists(conn, "call_logs", "organization_id"):
        await conn.execute(text(
            "ALTER TABLE call_logs ADD COLUMN organization_id VARCHAR(36)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_call_logs_org_id ON call_logs(organization_id)"
        ))
        logger.info("[Migration 012] Added call_logs.organization_id")
    else:
        logger.info("[Migration 012] call_logs.organization_id already exists — skipped")


async def migration_013_setup_pipecat_schema(conn: AsyncConnection):
    """
    Create the pipecat PostgreSQL schema and migrate Pipecat-owned tables into it.

    Strategy when connecting to the unified DB (shared with Node):
    - public.assistants may be Node's WEB-shell table (no system_prompt) → create
      pipecat.assistants fresh; copy rows only if public version is the Python one.
    - public.plivo_numbers → rename to pipecat.voice_numbers (adds provider column).
    - public.app_settings  → transform into pipecat.voice_provider_settings with
      composite PK (organization_id, provider, key).
    - public.call_logs     → move to pipecat.call_logs.
    All cross-schema FK constraints are dropped to avoid inter-schema dependency.
    """
    await conn.execute(text("CREATE SCHEMA IF NOT EXISTS pipecat"))
    logger.info("[Migration 013] pipecat schema ready")

    # ── pipecat.assistants ────────────────────────────────────────────────────
    if not await _table_exists(conn, "assistants", "pipecat"):
        await conn.execute(text("""
            CREATE TABLE pipecat.assistants (
                id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                organization_id         VARCHAR(36),
                name                    VARCHAR(255) NOT NULL DEFAULT '',
                system_prompt           TEXT NOT NULL DEFAULT '',
                welcome_message         VARCHAR(500) DEFAULT 'Hello. I am Ciya. How can I help you?',
                default_language        VARCHAR(50)  DEFAULT 'english',
                voice                   VARCHAR(50)  DEFAULT 'priya',
                llm_model               VARCHAR(100) DEFAULT 'gpt-4o-mini',
                temperature             FLOAT        DEFAULT 0.2,
                business_hours_start    VARCHAR(10)  DEFAULT '10:30',
                business_hours_end      VARCHAR(10)  DEFAULT '18:30',
                prefetch_webhook_url    TEXT,
                end_of_call_webhook_url TEXT,
                status                  VARCHAR(20)  DEFAULT 'development',
                created_at              TIMESTAMPTZ  DEFAULT NOW(),
                updated_at              TIMESTAMPTZ  DEFAULT NOW()
            )
        """))
        await conn.execute(text(
            "CREATE INDEX idx_pipecat_assistants_org ON pipecat.assistants(organization_id)"
        ))
        # Copy rows from public.assistants only if it's the Python version (has system_prompt)
        if await _column_exists(conn, "assistants", "system_prompt", "public"):
            await conn.execute(text("""
                INSERT INTO pipecat.assistants
                    (id, organization_id, name, system_prompt, welcome_message,
                     default_language, voice, llm_model, temperature,
                     business_hours_start, business_hours_end,
                     prefetch_webhook_url, end_of_call_webhook_url, status, created_at, updated_at)
                SELECT id, organization_id, name, system_prompt, welcome_message,
                       default_language, voice, llm_model, temperature,
                       business_hours_start, business_hours_end,
                       prefetch_webhook_url, end_of_call_webhook_url, status, created_at, updated_at
                FROM public.assistants
                ON CONFLICT (id) DO NOTHING
            """))
            logger.info("[Migration 013] Copied assistants from public to pipecat schema")
        else:
            logger.info("[Migration 013] public.assistants is Node's table — pipecat.assistants created empty")
    else:
        logger.info("[Migration 013] pipecat.assistants already exists — skipped")

    # ── pipecat.voice_numbers (was public.plivo_numbers) ─────────────────────
    if not await _table_exists(conn, "voice_numbers", "pipecat"):
        if await _table_exists(conn, "plivo_numbers", "public"):
            # Drop FK to public.assistants before moving
            await conn.execute(text("""
                ALTER TABLE public.plivo_numbers
                DROP CONSTRAINT IF EXISTS plivo_numbers_assistant_id_fkey
            """))
            await conn.execute(text("ALTER TABLE public.plivo_numbers SET SCHEMA pipecat"))
            await conn.execute(text("ALTER TABLE pipecat.plivo_numbers RENAME TO voice_numbers"))
            if not await _column_exists(conn, "voice_numbers", "provider", "pipecat"):
                await conn.execute(text(
                    "ALTER TABLE pipecat.voice_numbers ADD COLUMN provider VARCHAR(20) NOT NULL DEFAULT 'plivo'"
                ))
            logger.info("[Migration 013] Moved plivo_numbers → pipecat.voice_numbers")
        else:
            await conn.execute(text("""
                CREATE TABLE pipecat.voice_numbers (
                    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    organization_id   VARCHAR(36),
                    provider          VARCHAR(20) NOT NULL DEFAULT 'plivo',
                    number            VARCHAR(30) UNIQUE NOT NULL,
                    friendly_name     VARCHAR(255) DEFAULT '',
                    country           VARCHAR(100) DEFAULT '',
                    number_type       VARCHAR(50)  DEFAULT '',
                    assistant_id      UUID,
                    webhook_configured BOOLEAN DEFAULT FALSE
                )
            """))
            await conn.execute(text(
                "CREATE INDEX idx_pipecat_voice_numbers_org ON pipecat.voice_numbers(organization_id)"
            ))
            logger.info("[Migration 013] Created pipecat.voice_numbers (fresh)")
    else:
        logger.info("[Migration 013] pipecat.voice_numbers already exists — skipped")

    # ── pipecat.voice_provider_settings (was public.app_settings) ────────────
    if not await _table_exists(conn, "voice_provider_settings", "pipecat"):
        await conn.execute(text("""
            CREATE TABLE pipecat.voice_provider_settings (
                organization_id VARCHAR(36)  NOT NULL,
                provider        VARCHAR(20)  NOT NULL,
                key             VARCHAR(100) NOT NULL,
                value           TEXT,
                PRIMARY KEY (organization_id, provider, key)
            )
        """))
        logger.info("[Migration 013] Created pipecat.voice_provider_settings")

        # Migrate data from old app_settings if it exists
        if await _table_exists(conn, "app_settings", "public"):
            key_map = {
                "plivo_auth_id":    ("plivo",  "auth_id"),
                "plivo_auth_token": ("plivo",  "auth_token"),
                "domain":           ("system", "domain"),
            }
            for old_key, (provider, new_key) in key_map.items():
                await conn.execute(text("""
                    INSERT INTO pipecat.voice_provider_settings (organization_id, provider, key, value)
                    SELECT '', :provider, :new_key, value
                    FROM public.app_settings
                    WHERE key = :old_key AND value IS NOT NULL
                    ON CONFLICT DO NOTHING
                """), {"provider": provider, "new_key": new_key, "old_key": old_key})
            logger.info("[Migration 013] Migrated app_settings → voice_provider_settings")
    else:
        logger.info("[Migration 013] pipecat.voice_provider_settings already exists — skipped")

    # ── pipecat.call_logs (was public.call_logs) ──────────────────────────────
    if not await _table_exists(conn, "call_logs", "pipecat"):
        if await _table_exists(conn, "call_logs", "public"):
            await conn.execute(text("""
                ALTER TABLE public.call_logs
                DROP CONSTRAINT IF EXISTS call_logs_assistant_id_fkey
            """))
            await conn.execute(text("ALTER TABLE public.call_logs SET SCHEMA pipecat"))
            logger.info("[Migration 013] Moved call_logs → pipecat.call_logs")
        else:
            await conn.execute(text("""
                CREATE TABLE pipecat.call_logs (
                    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
                    organization_id VARCHAR(36),
                    session_id      VARCHAR(255) NOT NULL,
                    assistant_id    UUID,
                    assistant_name  VARCHAR(255) DEFAULT '',
                    from_number     VARCHAR(50)  DEFAULT '',
                    to_number       VARCHAR(50)  DEFAULT '',
                    duration        INTEGER      DEFAULT 0,
                    chat            TEXT,
                    call_status     VARCHAR(50)  DEFAULT 'user-ended',
                    error_message   TEXT,
                    chars_used      INTEGER      DEFAULT 0,
                    recording_url   TEXT,
                    cost_breakdown  TEXT,
                    total_cost      DOUBLE PRECISION,
                    started_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                    ended_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
                )
            """))
            await conn.execute(text(
                "CREATE INDEX idx_pipecat_call_logs_session ON pipecat.call_logs(session_id)"
            ))
            await conn.execute(text(
                "CREATE INDEX idx_pipecat_call_logs_org ON pipecat.call_logs(organization_id)"
            ))
            logger.info("[Migration 013] Created pipecat.call_logs (fresh)")
    else:
        logger.info("[Migration 013] pipecat.call_logs already exists — skipped")


async def migration_014_add_dynamic_config(conn: AsyncConnection):
    """Add faq_items, intent_triggers, filler_messages JSONB columns to pipecat.assistants."""
    for col in ("faq_items", "intent_triggers", "filler_messages"):
        if not await _column_exists(conn, "assistants", col, "pipecat"):
            await conn.execute(text(
                f"ALTER TABLE pipecat.assistants ADD COLUMN {col} JSONB"
            ))
            logger.info(f"[Migration 014] Added column {col} to pipecat.assistants")
        else:
            logger.info(f"[Migration 014] Column {col} already exists — skipped")


# ── Registry — add new migrations here in order ───────────────────────────────

async def migration_015_timestamp_with_timezone(conn):
    """Convert TIMESTAMP WITHOUT TIME ZONE → TIMESTAMPTZ on all datetime columns."""
    conversions = [
        ("assistants",  "created_at"),
        ("assistants",  "updated_at"),
        ("call_logs",   "started_at"),
        ("call_logs",   "ended_at"),
    ]
    for table, col in conversions:
        await conn.execute(text(
            f"ALTER TABLE pipecat.{table} "
            f"ALTER COLUMN {col} TYPE TIMESTAMPTZ "
            f"USING {col} AT TIME ZONE 'UTC'"
        ))


async def migration_016_add_voice_provider_settings_id(conn: AsyncConnection):
    """
    Add id/assistant_id columns to pipecat.voice_provider_settings.

    This table was created by migration_013 with ONLY (organization_id,
    provider, key, value) — a composite PRIMARY KEY on (organization_id,
    provider, key), no id, no assistant_id. Both the SQLAlchemy model
    (database/models.py's VoiceProviderSetting) and the Node WEB mirror
    (shared/schema-pipecat.ts) were later updated to add `id` (declared as
    the ORM primary key) and `assistant_id` (for the assistant → org →
    global BYOK-key resolution tier) — but no migration ever added them to
    the real table. Any full-entity SELECT (SQLAlchemy `select(VoiceProviderSetting)`,
    or WEB's `db.select({id: voiceProviderSettings.id})` in upsertSetting)
    fails outright against a table missing these columns.

    Deliberately NOT touching the existing composite PRIMARY KEY here — that
    would be a separate, higher-risk change (existing rows/constraints,
    potential app-level assumptions) and is out of scope for "make the
    columns the code already expects actually exist". Callers that upsert by
    id (WEB's upsertSetting) work correctly once id exists and is unique,
    regardless of what the table's actual PRIMARY KEY is.

    NOTE: the composite PK (organization_id, provider, key) has no
    assistant_id component, so it cannot hold both an org-level row and an
    assistant-level row for the same (organization_id, provider, key) —
    inserting the second would violate that PK. Widened in
    migration_017_widen_voice_provider_settings_key (kept as a separate,
    explicitly-requested migration rather than folded in here).
    """
    if not await _table_exists(conn, "voice_provider_settings", "pipecat"):
        logger.info("[Migration 016] pipecat.voice_provider_settings not found — skipped")
        return

    if not await _column_exists(conn, "voice_provider_settings", "id", "pipecat"):
        # VARCHAR, not UUID — matches both consumers exactly: the SQLAlchemy
        # model declares `id: Mapped[str] = mapped_column(String, primary_key=True)`,
        # and WEB's Drizzle mirror declares `varchar("id")`. Neither expects
        # a native uuid type back from a query, just a string that happens
        # to look like one.
        await conn.execute(text(
            "ALTER TABLE pipecat.voice_provider_settings "
            "ADD COLUMN id VARCHAR NOT NULL DEFAULT gen_random_uuid()::text"
        ))
        await conn.execute(text(
            "ALTER TABLE pipecat.voice_provider_settings ADD CONSTRAINT "
            "voice_provider_settings_id_unique UNIQUE (id)"
        ))
        logger.info("[Migration 016] Added pipecat.voice_provider_settings.id column (+ unique constraint)")
    else:
        logger.info("[Migration 016] pipecat.voice_provider_settings.id already exists — skipped")

    if not await _column_exists(conn, "voice_provider_settings", "assistant_id", "pipecat"):
        await conn.execute(text(
            "ALTER TABLE pipecat.voice_provider_settings ADD COLUMN assistant_id UUID"
        ))
        logger.info("[Migration 016] Added pipecat.voice_provider_settings.assistant_id column")
    else:
        logger.info("[Migration 016] pipecat.voice_provider_settings.assistant_id already exists — skipped")


async def migration_017_widen_voice_provider_settings_key(conn: AsyncConnection):
    """
    Widen pipecat.voice_provider_settings' uniqueness to include assistant_id.

    migration_013 created this table with PRIMARY KEY (organization_id,
    provider, key) — no assistant_id component. Once the assistant -> org ->
    global BYOK-key resolution tier was added (the assistant_id column,
    added by migration_016), that PK became too narrow: it can't hold BOTH
    an org-level row (assistant_id NULL) and an assistant-scoped row for the
    SAME (organization_id, provider, key) — inserting the second would
    violate the PK. Flagged in migration_016's own docstring as a deferred,
    explicit-sign-off change; this is that follow-up, requested directly.

    Replaces the composite PK with:
      - PRIMARY KEY on `id` (added by migration_016)
      - the exact unique index WEB's own Drizzle mirror already declares —
        shared/schema-pipecat.ts's `voice_provider_settings_scope_idx`,
        which COALESCEs assistant_id to a sentinel UUID before comparing
        (Postgres treats NULL != NULL in a plain unique index, so two
        org-level rows for the same key would NOT collide without this).
        That index was declared in WEB's schema but never actually reached
        the real table, since this schema is Python-migration-owned and
        drizzle-kit never runs DDL against it — this migration is what
        finally makes the real table match what WEB's mirror describes.

    Safe against existing data: the OLD PK already guaranteed at most one
    row per (organization_id, provider, key) with NO assistant_id
    dimension, and assistant_id is a column migration_016 just added (NULL
    on every pre-existing row) — so the new index can never find a
    pre-existing duplicate.
    """
    if not await _table_exists(conn, "voice_provider_settings", "pipecat"):
        logger.info("[Migration 017] pipecat.voice_provider_settings not found — skipped")
        return

    # The ORIGINAL composite PK, Postgres' default auto-generated name for
    # an unnamed `PRIMARY KEY (...)` in a CREATE TABLE. Named distinctly
    # from the new id-based PK below so this check stays a true no-op on
    # every run after the first, instead of dropping-and-recreating forever.
    if await _constraint_exists(conn, "voice_provider_settings", "voice_provider_settings_pkey", "pipecat"):
        await conn.execute(text(
            "ALTER TABLE pipecat.voice_provider_settings DROP CONSTRAINT voice_provider_settings_pkey"
        ))
        logger.info("[Migration 017] Dropped old composite PRIMARY KEY (organization_id, provider, key)")
    else:
        logger.info("[Migration 017] Old composite PRIMARY KEY already gone — skipped")

    # Superseded by the PK below (a PRIMARY KEY already implies UNIQUE) —
    # drop it first so there's no redundant constraint left behind.
    if await _constraint_exists(conn, "voice_provider_settings", "voice_provider_settings_id_unique", "pipecat"):
        await conn.execute(text(
            "ALTER TABLE pipecat.voice_provider_settings DROP CONSTRAINT voice_provider_settings_id_unique"
        ))

    if not await _constraint_exists(conn, "voice_provider_settings", "voice_provider_settings_id_pkey", "pipecat"):
        await conn.execute(text(
            "ALTER TABLE pipecat.voice_provider_settings "
            "ADD CONSTRAINT voice_provider_settings_id_pkey PRIMARY KEY (id)"
        ))
        logger.info("[Migration 017] Added PRIMARY KEY (id)")
    else:
        logger.info("[Migration 017] PRIMARY KEY (id) already present — skipped")

    # Matches shared/schema-pipecat.ts's `voice_provider_settings_scope_idx`
    # exactly — same name, same COALESCE sentinel — so WEB's Drizzle mirror
    # and the real table agree.
    await conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS voice_provider_settings_scope_idx "
        "ON pipecat.voice_provider_settings ("
        "  organization_id,"
        "  COALESCE(assistant_id, '00000000-0000-0000-0000-000000000000'::uuid),"
        "  provider,"
        "  key"
        ")"
    ))
    logger.info("[Migration 017] Ensured voice_provider_settings_scope_idx (org+assistant+provider+key)")


# ── Registry — add new migrations here in order ───────────────────────────────

MIGRATIONS = [
    ("001_create_assistants",           migration_001_create_assistants),
    ("002_create_plivo_numbers",        migration_002_create_plivo_numbers),
    ("003_add_assistant_status",        migration_003_add_assistant_status),
    ("004_add_webhook_urls",            migration_004_add_webhook_urls),
    ("005_create_call_logs",            migration_005_create_call_logs),
    ("006_add_recording_url",           migration_006_add_recording_url),
    ("007_add_cost_columns",            migration_007_add_cost_columns),
    ("008_add_number_details",          migration_008_add_number_details),
    ("009_create_app_settings",         migration_009_create_app_settings),
    ("010_add_org_id_assistants",       migration_010_add_org_id_assistants),
    ("011_add_org_id_plivo_numbers",    migration_011_add_org_id_plivo_numbers),
    ("012_add_org_id_call_logs",        migration_012_add_org_id_call_logs),
    ("013_setup_pipecat_schema",        migration_013_setup_pipecat_schema),
    ("014_add_dynamic_config",          migration_014_add_dynamic_config),
    ("015_timestamp_with_timezone",     migration_015_timestamp_with_timezone),
    ("016_add_voice_provider_settings_id", migration_016_add_voice_provider_settings_id),
    ("017_widen_voice_provider_settings_key", migration_017_widen_voice_provider_settings_key),
]


# ── Runner ────────────────────────────────────────────────────────────────────

async def run_migrations():
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
