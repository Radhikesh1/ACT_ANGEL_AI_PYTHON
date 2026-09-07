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

| Layer           | Technology                                                |
| --------------- | --------------------------------------------------------- |
| Voice pipeline  | Pipecat-AI                                                |
| Web server      | FastAPI + Uvicorn                                         |
| Database        | PostgreSQL (via SQLAlchemy async + asyncpg)               |
| LLM             | OpenAI (GPT-4o-mini / GPT-4o, configurable per assistant) |
| STT             | Sarvam AI (`saaras:v3`)                                   |
| TTS             | Sarvam AI (`bulbul:v3`)                                   |
| Telephony       | Plivo (µ-law, 8 kHz audio)                                |
| Appointment API | Make.com Webhook                                          |
| Auth            | JWT in httpOnly cookie                                    |
| Frontend        | React 19 + Vite + Tailwind + shadcn/ui (separate repo)    |

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

| Column                 | Type     | Description                                             |
| ---------------------- | -------- | ------------------------------------------------------- |
| `id`                   | UUID     | Primary key                                             |
| `name`                 | String   | Display name                                            |
| `system_prompt`        | Text     | Full LLM system prompt                                  |
| `welcome_message`      | String   | First thing the assistant says when call connects       |
| `default_language`     | String   | `english` / `hindi` / `bengali` / `telugu` / `gujarati` |
| `voice`                | String   | Sarvam TTS voice name                                   |
| `llm_model`            | String   | `gpt-4o-mini` or `gpt-4o`                               |
| `temperature`          | Float    | 0.0 – 1.0                                               |
| `business_hours_start` | String   | `HH:MM` (IST)                                           |
| `business_hours_end`   | String   | `HH:MM` (IST)                                           |
| `status`               | String   | `development` (default) or `production`                 |
| `created_at`           | DateTime |                                                         |
| `updated_at`           | DateTime |                                                         |

### `plivo_numbers`

| Column               | Type    | Description                   |
| -------------------- | ------- | ----------------------------- |
| `id`                 | UUID    | Primary key                   |
| `number`             | String  | Plivo phone number            |
| `friendly_name`      | String  | Alias from Plivo              |
| `assistant_id`       | UUID FK | Linked assistant (nullable)   |
| `webhook_configured` | Boolean | Whether Plivo webhook was set |

---

## API Endpoints

### Auth

| Method | Path               | Description                                                      |
| ------ | ------------------ | ---------------------------------------------------------------- |
| `POST` | `/api/auth/login`  | Login with `{ username, password }` → sets `access_token` cookie |
| `GET`  | `/api/auth/me`     | Returns current user info                                        |
| `POST` | `/api/auth/logout` | Clears cookie                                                    |

### Assistants

| Method   | Path                             | Description                                       |
| -------- | -------------------------------- | ------------------------------------------------- |
| `GET`    | `/api/assistants`                | List all assistants                               |
| `POST`   | `/api/assistants`                | Create assistant                                  |
| `GET`    | `/api/assistants/{id}`           | Get one assistant                                 |
| `PUT`    | `/api/assistants/{id}`           | Update assistant (blocked if `status=production`) |
| `DELETE` | `/api/assistants/{id}`           | Delete assistant (blocked if `status=production`) |
| `POST`   | `/api/assistants/{id}/publish`   | Move to production                                |
| `POST`   | `/api/assistants/{id}/unpublish` | Move back to development                          |

### Phone Numbers

| Method | Path                                          | Description                                        |
| ------ | --------------------------------------------- | -------------------------------------------------- |
| `GET`  | `/api/numbers`                                | List Plivo numbers + assignment state              |
| `POST` | `/api/numbers/{number}/assign/{assistant_id}` | Assign number → assistant, auto-sets Plivo webhook |
| `POST` | `/api/numbers/{number}/unassign`              | Remove assignment                                  |

### Voice (Plivo)

| Method      | Path          | Description                                                                   |
| ----------- | ------------- | ----------------------------------------------------------------------------- |
| `GET`       | `/answerCall` | Receives Plivo inbound call, looks up assistant by dialed number, returns XML |
| `WebSocket` | `/ws`         | Real-time µ-law audio stream for active calls                                 |

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
| -------- | --------- |
| English  | `pooja`   |
| Hindi    | `priya`   |
| Bengali  | `simran`  |
| Telugu   | `kavitha` |
| Gujarati | `priya`   |

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

`BYOK_STT_FLAT_FEE_PER_MINUTE` / `BYOK_LLM_FLAT_FEE_PER_MINUTE` — the flat per-minute rate charged instead of the usual usage-based cost when an org supplies its own Sarvam/OpenAI key (see `services/cost_service.py`). Two levels of override, both versioned (WEB tables, `public` schema), resolved by `server.py` on every call:

1. The assistant's **active margin version** (WEB's `marginVersions` table, mirrored onto `assistant_extensions` for fast reads) — set from the "Update Margin" popup on the assistant edit page, right alongside the margin percentages. `server.py` queries `assistant_extensions` (an `AssistantExtension` model in `database/models.py`) by `assistant_external_id` and uses its `byok_stt_flat_fee_per_minute`/`byok_llm_flat_fee_per_minute` if not null.
2. Otherwise, the **active** row of WEB's `byok_rate_versions` table (Global Settings → "BYOK Billing Rates" tab — versioned exactly like Exchange Rate Versions: a history of versions, one `is_active` at a time), via a new `BYOKRateVersion` model queried with `is_active.is_(True)` (`.limit(1)` + `.scalars().first()`, not `scalar_one_or_none()` — tolerates more than one active row without raising instead of 500ing an inbound call on that edge case).

The env var here is only the last-resort fallback when neither is configured (i.e. no assistant override AND no BYOK rate version has ever been activated). Deliberately no org-level tier — a margin version's BYOK fields are either explicitly set for that one assistant or left null to inherit the active global version's rate.

On the WEB side, every `call_usages` row records which active `byok_rate_versions` row (if any) was used to bill that call's analytics component, via a `byok_rate_version_id` column — the same audit-trail convention as `margin_version_id`/`exchange_rate_version_id`.

### Model Pricing Overrides

Where BYOK Cost Overrides (above) is the flat rate charged _instead of_ the usual cost formula, `services/cost_service.py`'s pricing table (`_LLM_PRICING`, `_SARVAM_PER_MIN`, `_PLIVO_PER_MIN`, `_PLATFORM_PER_MIN` — the env vars documented at the top of that file) _is_ the usual cost formula: what OpenAI/Sarvam/Plivo actually charge, per model / per minute. Those env vars are still the last-resort fallback, but the primary source is now the same versioned mechanism as everything else — WEB's `model_pricing_versions` table (`public` schema, Global Settings → "Model Pricing" tab), read via a new `ModelPricingVersion` model (`database/models.py`).

Unlike `BYOKRateVersion`/`AssistantExtension` (one column per rate), `ModelPricingVersion.rates` is a single JSONB blob holding the whole pricing table — `{"analytics": {...}, "llm": {"<model-prefix>": {"inputPer1M", "outputPer1M"}, ...}, "sttPerMinute", "phonePerMinute", "platformPerMinute"}` — since the model list can grow without a migration. `analytics` is WEB-only (its post-call analytics pricing) and is ignored on the Python side.

`server.py`'s `/answerCall` fetches the active row once per call (same `.limit(1)` + `.scalars().first()` pattern as `BYOKRateVersion`, for the same reason) and stores its `rates` dict into `call_sessions[call_uuid]["model_pricing"]`. `voice_agent.py` reads it back out (`session.get("model_pricing")`) and threads it through to every `calculate_cost()` call site as a new `model_pricing` parameter.

Inside `calculate_cost()`, resolution is **per-field**, not per-version: for the LLM table, `_llm_rates(model, llm_overrides)` checks `llm_overrides.get(<matched-prefix>)` first and falls back to that specific model's own env-loaded rate if absent — a version that only overrides `gpt-4o-mini` still lets every other model resolve from its env default. Same per-field fallback for `sttPerMinute`/`phonePerMinute`/`platformPerMinute` (each checked independently with `is not None`, so a version can override just one of the three). This means an incomplete or partially-filled-in version can never break a call — every field has its own fallback chain: DB version → env var → (nothing further needed, `_f()` always resolves to a literal at import time).

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

| Dict                  | Purpose                                             |
| --------------------- | --------------------------------------------------- |
| `call_sessions`       | `call_id → { from_number, assistant_config }`       |
| `language_sessions`   | `call_id → { language, locked, pending }`           |
| `conversation_states` | `call_id → { awaiting_datetime, appointment_type }` |

---

## Frontend Pages (CHAT_WEB)

| Route             | Page                                            |
| ----------------- | ----------------------------------------------- |
| `/login`          | Login form                                      |
| `/assistants`     | List all assistants — create, publish, delete   |
| `/assistants/new` | Create assistant form                           |
| `/assistants/:id` | Edit assistant form (locked when production)    |
| `/numbers`        | Plivo number table — assign/unassign assistants |
| `/chat`           | Chat UI                                         |

Split services/email_api.py (748 lines) into four files matching this repo's existing conventions:

utils/email_parsing.py (51 lines) — stateless text helpers (extract_email_address, get_body, strip_quoted_reply, strip_leading_subject_line)
services/email_agent_prompt.py (178 lines) — SYSTEM_PROMPT + TOOLS, pure config
services/email_repository.py (161 lines) — all raw psycopg2 access to the leads DB, now reading LEADS_DATABASE_URL instead of colliding with the app's own DATABASE_URL
services/email_agent.py (378 lines) — orchestration + entrypoint (OpenAI calls, SMTP, IMAP IDLE loop, main())
maybe_compact now takes call_openai_fn as a parameter instead of the repository module importing OpenAI config directly — keeps the DB layer free of LLM-specific concerns and avoids a circular import between repository and agent.

Supporting changes:

requirements.txt: added imapclient, psycopg2-binary, requests (installed into the project venv and verified)
.env.example: documented IMAP*\*, SMTP*\*, LEADS_DATABASE_URL, TOOL_WEBHOOK_URL, and clarified OPENAI_API_KEY is hard-required for the email agent (unlike the voice app, where it's optional)
Deleted the old services/email_api.py
Verified with py_compile and a real import (with dummy env vars) that the four modules wire together correctly
Nothing is committed yet. Still open: you were going to confirm whether LEADS_DATABASE_URL should point at the same Postgres instance the EC2 watcher currently uses, or a different one — let me know when you have that, and I'll fill in the real value/notes accordingly (or if it's just for you to configure at deploy time, we're done here).

Here's the full trace, end to end, with the one gap I'm honest about at the point it occurs.

1. Form submission (act-angel_front, Vercel)
   User picks the Email channel button in meet-your-angel-form.tsx:29-38, fills name/phone/email, submits.

handleDetailsSubmit (:134-169) → sendCodes → POST /api/otp/send.
Important detail: verification is always dual-channel — /api/otp/send sends a Twilio Verify code to both the phone (SMS) and the email, regardless of which conversation channel was picked (route.ts:82-87). The channel choice doesn't affect verification — only what happens after.
User enters both codes → handleOtpSubmit (:188-240) → POST /api/otp/verify with {name, phone, email, phoneCode, emailCode, channel: "Email"}. 2. Verification + handoff to n8n
/api/otp/verify checks both codes against Twilio (route.ts:70-95). If both pass, it makes one outbound call: POST N8N_INTAKE_WEBHOOK_URL with {name, phone, email, otp_status: "verified", channel: "Email"} (route.ts:107-122).

This is the gap I can't see into. That n8n workflow is hosted at cloudsteer.app.n8n.cloud — not in any repo I have access to. Per the code comment it "pushes the lead to Postgres, picks the right outbound number by country, and fires the Retell call." The success screen text for the Email channel says "Check your inbox — your Angel has just sent you a note to get the conversation started" (meet-your-angel-form.tsx:442-447), so somewhere in that n8n workflow, for channel="Email", it must: (a) insert the leads row, and (b) send the first outbound email and log a matching direction='outbound' row in emails — that row is what later lets already_replied_to and the reply-threading (In-Reply-To/References) work. I haven't seen that logic; if you want it confirmed, we'd need to open that n8n workflow.

3. The lead replies — this is where email_agent.py takes over
   Independent of all of the above, services/email_agent.py's main() holds an open IMAP IDLE connection to Hello@ActAngel.com, re-armed every 9 minutes (idle_loop). When the lead hits reply:

Gmail wakes the IDLE connection → catch_up(conn) runs → handle_message(conn, uid) for each new UID (:267).
Parses the raw email: from_email, subject, body (quote-stripped), message_id, in_reply_to, references (:271-276).
find_lead_by_email(cur, from_email) (:288) — looks the sender up in the shared leads table. This only works if step 2's n8n insert already created that row with this exact email. No match → silently skipped, no reply sent.
Two idempotency guards: already_replied_to (has an outbound reply already gone out in_reply_to this exact message?) and already_logged_inbound (was this inbound message already logged by a prior, crashed attempt?) (:294-302).
Logs the inbound row, runs maybe_compact (rolls old history into a summary once 50+ messages have piled up), then load_context pulls: the last 12 raw messages, the most recent prospect_memory summary (not filtered by channel at read time — this is the actual cross-channel memory: a summary written from a voice or WhatsApp conversation gets read here too), and the latest callbacks row (:306).
DB connection closes here — deliberately, before the slow part.
generate_reply(...) builds the prompt (system prompt + a "ground truth" block about the lead's name/email/website_trial origin + cross-channel summary + callback status, per build_messages), calls OpenAI. If the model requests tool calls (log_qualification, update_lead_score, book_demo, etc.), each one is POSTed to the same TOOL_WEBHOOK_URL n8n endpoint Retell/WhatsApp use (run_tool_call) — so the actual side-effects (writing qualification data, scheduling callbacks) are executed by n8n, not by this script. A second OpenAI call (no tools offered) then produces the actual reply text.
send_reply(...) sends the real email via SMTP, threaded with In-Reply-To/References (:213), then a second DB round-trip logs the outbound row.
save_last_uid(uid) persists progress locally, loop goes back to IDLE.
So the only thing genuinely "EC2-specific" today is which machine is running that IMAP loop — steps 1–2 and the n8n pieces don't change no matter where email_agent.py runs, since nothing calls it by address; it just needs the same mailbox creds + LEADS_DATABASE_URL + TOOL_WEBHOOK_URL you've now got in .env.

Want me to look at anything specific next — e.g., a dry run of email_agent.py here, or should we try to get eyes on that n8n workflow to confirm step 2's outbound-email logic?
