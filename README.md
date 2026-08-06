# ACT Angel AI — Voice Assistant Platform

A multi-assistant voice AI platform for managing inbound customer calls. Create multiple AI assistants, assign Plivo phone numbers to them, and configure every aspect of each assistant's behaviour — all through a web dashboard.

The default assistant persona is **Ciya**, built for Cloudsteer's luxury real estate project **Citadel** (Hiranandani Fortune City, Panvel, Navi Mumbai).

---

## System Overview

```
Frontend (React)  ──►  FastAPI backend  ──►  PostgreSQL
                            │
                    Plivo (inbound call)
                            │
                    WebSocket audio stream
                            │
                    Pipecat voice pipeline
                    STT → Noise → Intent → LLM → Filler → TTS
```

Each inbound call is routed to the assistant assigned to that Plivo number. The assistant's system prompt, voice, language, LLM model and temperature are loaded from the database at call time.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Voice pipeline | Pipecat-AI |
| Web server | FastAPI + Uvicorn |
| Database | PostgreSQL (via SQLAlchemy async + asyncpg) |
| LLM | OpenAI (GPT-4o-mini / GPT-4o, configurable per assistant) |
| STT | Sarvam AI (`saaras:v3`) |
| TTS | Sarvam AI (`bulbul:v3`) |
| Telephony | Plivo (µ-law, 8 kHz audio) |
| Appointment API | Make.com Webhook |
| Auth | JWT in httpOnly cookie |
| Frontend | React 19 + Vite + Tailwind + shadcn/ui (separate repo) |

---

## Project Structure

```
ACT_ANGEL_AI_PYTHON/
├── server.py                    # FastAPI app — all routes, CORS, startup
├── voice_agent.py               # Voice pipeline orchestrator (builds & runs Pipecat pipeline per call)
├── requirements.txt
├── .env                         # API keys & config (see below — gitignored)
├── docker-compose.dev.yml       # Local PostgreSQL container for development
│
├── migrations/                  # Database migration runner
│   └── runner.py                # Checks & applies schema changes on startup
│
├── database/                    # PostgreSQL layer
│   ├── connection.py            # Async engine, session factory, Base
│   └── models.py                # Assistant + PlivoNumber ORM models
│
├── api/                         # REST API routers
│   ├── auth.py                  # POST /api/auth/login, GET /api/auth/me, POST /api/auth/logout
│   ├── assistants.py            # CRUD /api/assistants + publish/unpublish
│   ├── numbers.py               # Plivo number management /api/numbers
│   └── dependencies.py          # JWT cookie guard (get_current_user)
│
├── processors/                  # Pipecat frame processors
│   ├── noise_gate_processor.py  # Filters junk transcriptions
│   ├── language_processor.py    # Detects & confirms language switching
│   ├── intent_router.py         # Routes appointment intent, queues fillers
│   ├── appointment_processor.py # Extracts date/time, calls booking API
│   └── filler_processor.py      # Plays "thinking" fillers during API latency
│
├── services/
│   ├── appointment_api.py       # POST to Make.com webhook
│   └── filler_manager.py        # Random filler string selection
│
├── utils/
│   ├── session_state.py         # In-memory call state dicts
│   ├── language_manager.py      # Language/voice lookup, session init, confirmations
│   ├── tts_factory.py           # Creates Sarvam TTS with correct voice per language
│   ├── datetime_parser.py       # Natural language date/time → ISO string (IST)
│   ├── context_manager.py       # LLM context trimming (last 8 messages)
│   ├── audio_utils.py           # Silence detection via RMS
│   └── filler_text.py           # Multilingual filler strings
│
├── frames/
│   └── custom_frames.py         # FillerRequestFrame (custom Pipecat frame)
│
└── data/
    ├── faq_data.py              # FAQ answers about Citadel project
    └── multilingual_keywords.py # Language keywords, voice map, confirmation words
```

---

## Database Models

### `assistants`

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `name` | String | Display name |
| `system_prompt` | Text | Full LLM system prompt |
| `welcome_message` | String | First thing the assistant says when call connects |
| `default_language` | String | `english` / `hindi` / `bengali` / `telugu` / `gujarati` |
| `voice` | String | Sarvam TTS voice name |
| `llm_model` | String | `gpt-4o-mini` or `gpt-4o` |
| `temperature` | Float | 0.0 – 1.0 |
| `business_hours_start` | String | `HH:MM` (IST) |
| `business_hours_end` | String | `HH:MM` (IST) |
| `status` | String | `development` (default) or `production` |
| `created_at` | DateTime | |
| `updated_at` | DateTime | |

### `plivo_numbers`

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `number` | String | Plivo phone number |
| `friendly_name` | String | Alias from Plivo |
| `assistant_id` | UUID FK | Linked assistant (nullable) |
| `webhook_configured` | Boolean | Whether Plivo webhook was set |

---

## API Endpoints

### Auth
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/auth/login` | Login with `{ username, password }` → sets `access_token` cookie |
| `GET` | `/api/auth/me` | Returns current user info |
| `POST` | `/api/auth/logout` | Clears cookie |

### Assistants
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/assistants` | List all assistants |
| `POST` | `/api/assistants` | Create assistant |
| `GET` | `/api/assistants/{id}` | Get one assistant |
| `PUT` | `/api/assistants/{id}` | Update assistant (blocked if `status=production`) |
| `DELETE` | `/api/assistants/{id}` | Delete assistant (blocked if `status=production`) |
| `POST` | `/api/assistants/{id}/publish` | Move to production |
| `POST` | `/api/assistants/{id}/unpublish` | Move back to development |

### Phone Numbers
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/numbers` | List Plivo numbers + assignment state |
| `POST` | `/api/numbers/{number}/assign/{assistant_id}` | Assign number → assistant, auto-sets Plivo webhook |
| `POST` | `/api/numbers/{number}/unassign` | Remove assignment |

### Voice (Plivo)
| Method | Path | Description |
|---|---|---|
| `GET` | `/answerCall` | Receives Plivo inbound call, looks up assistant by dialed number, returns XML |
| `WebSocket` | `/ws` | Real-time µ-law audio stream for active calls |

---

## Call Flow

1. Caller dials a Plivo number
2. Plivo `GET /answerCall?CallUUID=…&From=…&To=…`
3. Server looks up `To` number → finds assigned assistant → loads config from DB
4. Config is stored in `call_sessions[call_uuid]`
5. Server returns XML → Plivo opens WebSocket to `/ws`
6. `run_bot()` reads assistant config (prompt, model, voice, language) and builds the pipeline
7. Pipeline runs: STT → noise filter → language detection → intent router → LLM → TTS → back to caller
8. If no assistant is assigned to the number, the default fallback prompt is used

---

## Supported Languages & Voices

| Language | TTS Voice |
|---|---|
| English | `pooja` |
| Hindi | `priya` |
| Bengali | `simran` |
| Telugu | `kavitha` |
| Gujarati | `priya` |

Language is auto-detected from caller speech. A confirmation step prevents accidental switching. The default language per assistant is configurable in the dashboard.

---

## Development / Production Workflow

Each assistant has a `status` field:

- **Development** — editable; all fields, publish/delete allowed
- **Production** — locked; PUT and DELETE return `409 Conflict`; must unpublish first

All state changes (create, update, publish, unpublish, delete) are logged via loguru with field-level diff for updates.

---

## Environment Variables

```env
# AI Services
OPENAI_API_KEY=sk-proj-...
SARVAM_API_KEY=sk_...

# Plivo Telephony

DOMAIN=your-tunnel.trycloudflare.com

# Appointment Booking
APPOINTMENT_API_URL=https://hook.eu2.make.com/...
ASSISTANT_ID=...
FROM_NUMBER=+91...

# Database
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/dbname

# Auth
SECRET_KEY=a-long-random-secret-string
ADMIN_USERID=your-username
ADMIN_PASSWORD=your-secure-password
```

`DOMAIN` is the public hostname used in the Plivo XML response WebSocket URL. In production this is your server domain; in development use a Cloudflare tunnel.

> **Note:** Passwords or values containing `@` or `!` in `DATABASE_URL` must be percent-encoded: `@` → `%40`, `!` → `%21`.

> **Note:** This list is not exhaustive — see `.env.example` for the full set (webhook secrets, Cloudinary, cost-tracking overrides, etc.), which is kept up to date as the source of truth.

> **Note:** `OPENAI_API_KEY` and `SARVAM_API_KEY` here are optional last-resort fallbacks, not hard requirements — the service no longer refuses to start without them. The primary source is the global default a Super Admin configures on the WEB dashboard's Global Settings page (with optional org/assistant overrides); a deployment can run with neither of these set in `.env` as long as that's configured. If a call ends up with no key from any source, it's logged as a warning rather than crashing the service.

### BYOK Cost Overrides

`BYOK_STT_FLAT_FEE_PER_MINUTE` / `BYOK_LLM_FLAT_FEE_PER_MINUTE` — the flat per-minute rate charged instead of the usual usage-based cost when an org supplies its own Sarvam/OpenAI key (see `services/cost_service.py`). Two levels of override, resolved by `server.py` on every call:

1. The assistant's **active margin version** (WEB's `marginVersions` table, mirrored onto `assistant_extensions` for fast reads) — set from the "Update Margin" popup on the assistant edit page, right alongside the margin percentages. `server.py` queries `assistant_extensions` (a new `AssistantExtension` model in `database/models.py`, reading the default/public schema — everything else in this file lives in the `pipecat` schema) by `assistant_external_id` and uses its `byok_stt_flat_fee_per_minute`/`byok_llm_flat_fee_per_minute` if not null.
2. Otherwise, the **global** rate (Global Settings → "BYOK Billing Rates" tab, still the `voice_provider_settings` mechanism with `organization_id=''`), via `get_org_provider_key(db, "", "byok_billing", ...)`.

The env var here is only the last-resort fallback when neither is configured. Deliberately no org-level tier — a version's BYOK fields are either explicitly set for that one assistant or left null to inherit the global rate.

---

## Local Development Setup

### 1. Start the local database

```powershell
docker compose -f docker-compose.dev.yml up -d
```

This starts a PostgreSQL 16 container on port **5433** (port 5432 may already be in use by a local Postgres installation).

### 2. Create virtual environment

```powershell
python -m venv venv
.\venv\Scripts\activate
```

### 3. Install dependencies

```powershell
pip install -r requirements.txt
```

### 4. Configure environment

Create `.env` in the project root:

```env
DATABASE_URL=postgresql+asyncpg://actangel:actangel_dev_2026@localhost:5433/act_angel_ai
SECRET_KEY=any-random-string-for-dev
ADMIN_USERID=cloudsteer
ADMIN_PASSWORD=your-password
OPENAI_API_KEY=sk-proj-...
SARVAM_API_KEY=sk_...
DOMAIN=your-tunnel.trycloudflare.com
APPOINTMENT_API_URL=https://hook.eu2.make.com/...
ASSISTANT_ID=...
FROM_NUMBER=+91...
```

### 5. Start the backend

```powershell
.\venv\Scripts\python.exe server.py
```

Server starts on `http://localhost:8000`. On startup, the migration runner automatically creates or updates all tables.

### 6. Expose with Cloudflare Tunnel (for live Plivo calls)

```powershell
cloudflared tunnel --url http://localhost:8000
```

Copy the generated URL and set it as `DOMAIN` in `.env`.

---

## Frontend Setup (CHAT_WEB)

The management dashboard lives in the `CHAT_WEB/` sibling repo.

### 1. Install dependencies

```powershell
cd ..\CHAT_WEB
npm install
```

### 2. Configure local API target

Create `CHAT_WEB/.env.local` (gitignored — never commit):

```env
PUBLIC_API_URL=http://localhost:8000
```

### 3. Start the frontend

```powershell
npm run dev
```

Frontend runs on `http://localhost:3000`. All `/api/*` requests are proxied to `PUBLIC_API_URL`.

### 4. Log in

Open `http://localhost:3000` → log in with `ADMIN_USERID` / `ADMIN_PASSWORD` → redirected to the **Assistants** dashboard.

---

## Production Deployment

On the AWS server the backend's `.env` should use:

```env
DATABASE_URL=postgresql+asyncpg://user:password@db:5432/act_angel_ai
```

where `db` is the internal Docker network hostname for the PostgreSQL container. Migrations run automatically on server start — no manual step required.

The frontend build is served separately. Set `PUBLIC_API_URL=https://actangels.com` in the build environment so the Vite proxy targets the production API.

---

## Adding a Database Migration

1. Open `migrations/runner.py`
2. Write a new async function — always check first, act second:

```python
async def migration_004_add_my_column(conn):
    if await _column_exists(conn, "assistants", "my_column"):
        logger.info("[Migration 004] already exists — skipped")
        return
    await conn.execute(text("ALTER TABLE assistants ADD COLUMN my_column TEXT"))
    logger.info("[Migration 004] Added my_column")
```

3. Register it in the `MIGRATIONS` list at the bottom of the file:

```python
("004_add_my_column", migration_004_add_my_column),
```

The runner tracks applied migrations in a `schema_migrations` table and runs each one only once.

---

## In-Memory Session State

All per-call state is in-memory and scoped to `CallUUID`. It is lost on server restart (mid-call state only — assistant configuration is always re-read from the DB).

| Dict | Purpose |
|---|---|
| `call_sessions` | `call_id → { from_number, assistant_config }` |
| `language_sessions` | `call_id → { language, locked, pending }` |
| `conversation_states` | `call_id → { awaiting_datetime, appointment_type }` |

---

## Frontend Pages (CHAT_WEB)

| Route | Page |
|---|---|
| `/login` | Login form |
| `/assistants` | List all assistants — create, publish, delete |
| `/assistants/new` | Create assistant form |
| `/assistants/:id` | Edit assistant form (locked when production) |
| `/numbers` | Plivo number table — assign/unassign assistants |
| `/chat` | Chat UI |
