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
├── system_prompt.py             # Default fallback system prompt
├── requirements.txt
├── .env                         # API keys & config (see below)
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
│   ├── assistants.py            # CRUD /api/assistants
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
| `welcome_message` | String | First thing Ciya says when call connects |
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
| `PUT` | `/api/assistants/{id}` | Update assistant |
| `DELETE` | `/api/assistants/{id}` | Delete assistant |

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

## Environment Variables

```env
# AI Services
OPENAI_API_KEY=sk-proj-...
SARVAM_API_KEY=sk_...

# Plivo Telephony
PLIVO_AUTH_ID=...
PLIVO_AUTH_TOKEN=...
PLIVO_PHONE_NUMBER=+91...
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

---

## Setup & Running

### 1. Create virtual environment

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/macOS
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

Create `.env` in the project root and fill in all values from the section above.

### 4. Start the server

```bash
python server.py
```

Server starts on `http://0.0.0.0:8000`. On startup, the migration runner checks which migrations have been applied and runs any new ones automatically — safe to run on both a fresh database and an existing one.

### 5. Expose with Cloudflare Tunnel (development)

```bash
cloudflared tunnel --url http://localhost:8000
```

Copy the generated URL (e.g. `something.trycloudflare.com`) and set it as `DOMAIN` in `.env`.

### 6. Use the dashboard

Open the frontend at `http://localhost:3000` (see the frontend repo). Log in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`, then:

1. Go to **Assistants** → create an assistant with your system prompt and settings
2. Go to **Phone Numbers** → assign a Plivo number to the assistant (webhook is set automatically)
3. Call the number — the configured assistant handles the call

---

## Adding a Database Migration

To add a new schema change:

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

The runner tracks applied migrations in a `schema_migrations` table and will only run each one once.

---

## In-Memory Session State

All per-call state is in-memory and scoped to `CallUUID`. It is lost on server restart (mid-call state only — assistant configuration is always re-read from the DB).

| Dict | Purpose |
|---|---|
| `call_sessions` | `call_id → { from_number, assistant_config }` |
| `language_sessions` | `call_id → { language, locked, pending }` |
| `conversation_states` | `call_id → { awaiting_datetime, appointment_type }` |

---

## Frontend (separate repo)

The management dashboard lives in `CHAT_WEB/`. It is a React 19 + Vite app using Wouter routing, TanStack Query, and shadcn/ui components.

Pages added for this platform:

| Route | Page |
|---|---|
| `/assistants` | List all assistants — create, edit, delete |
| `/assistants/new` | Create assistant form |
| `/assistants/:id` | Edit assistant form |
| `/numbers` | Plivo number table — assign/unassign assistants |

The frontend proxies `/api/*` to the backend via Vite dev proxy (`PUBLIC_API_URL` in `.env`).
