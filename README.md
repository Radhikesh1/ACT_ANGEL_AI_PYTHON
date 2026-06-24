# ACT Angel AI — Ciya Voice Assistant

**Ciya** is a multilingual inbound voice AI assistant built for Cloudsteer's luxury real estate project **Citadel** (Hiranandani Fortune City, Panvel, Navi Mumbai). It handles incoming customer calls, answers FAQs, detects language preferences, and books appointments — all in real time over phone.

---

## Architecture

Calls arrive via **Plivo** telephony. Audio is streamed over WebSocket into a **Pipecat** voice pipeline that processes frames sequentially:

```
Plivo (WebSocket) → STT → Noise Filter → Intent Router → LLM → Filler → TTS → Plivo (WebSocket)
```

State is managed in-memory per `CallUUID` for the lifetime of each call.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Voice pipeline | [Pipecat-AI](https://github.com/pipecat-ai/pipecat) |
| Web server | FastAPI + Uvicorn |
| LLM | OpenAI GPT-4o-mini |
| STT | Sarvam AI (`saaras:v3`) |
| TTS | Sarvam AI (`bulbul:v3`) |
| Telephony | Plivo (µ-law, 8 kHz audio) |
| Appointment API | Make.com Webhook |
| Date parsing | `dateparser` |
| HTTP client | `httpx` (async) |

---

## Project Structure

```
ACT_ANGEL_AI_PYTHON/
├── server.py                    # FastAPI server — HTTP & WebSocket endpoints
├── agent_bengali.py             # Main bot orchestrator — builds & runs pipeline
├── system_prompt.py             # AI persona (Ciya) rules & knowledge
├── requirements.txt
├── .env                         # API keys & configuration (see below)
│
├── processors/                  # Pipecat frame processors
│   ├── noise_gate_processor.py  # Filters short/junk transcriptions
│   ├── language_processor.py    # Detects & switches language per caller
│   ├── intent_router.py         # Detects appointment intent, queues fillers
│   ├── appointment_processor.py # Extracts date/time, calls booking API
│   └── filler_processor.py      # Plays "thinking" fillers during API calls
│
├── services/
│   ├── appointment_api.py       # POST to Make.com webhook
│   └── filler_manager.py        # Returns random filler strings
│
├── utils/
│   ├── session_state.py         # In-memory session dicts (call_sessions, language_sessions, etc.)
│   ├── language_manager.py      # Language/voice lookup, confirmation detection
│   ├── tts_factory.py           # Creates Sarvam TTS with correct voice per language
│   ├── datetime_parser.py       # Parses natural language date/time → ISO string
│   ├── context_manager.py       # Keeps LLM context to last 8 messages
│   ├── audio_utils.py           # Silence detection via RMS energy
│   └── filler_text.py           # Filler strings (multilingual)
│
├── frames/
│   └── custom_frames.py         # FillerRequestFrame (custom Pipecat frame)
│
└── data/
    ├── faq_data.py              # Hardcoded FAQ answers about Citadel
    └── multilingual_keywords.py # Language keywords, voice mappings, confirmation words
```

---

## Supported Languages

| Language | TTS Voice |
|---|---|
| English | `pooja` |
| Hindi | `priya` |
| Bengali | `simran` |
| Telugu | `kavitha` |
| Gujarati | *(configured)* |

Language is auto-detected from caller speech. A confirmation step prevents accidental switching.

---

## Key Features

- **Inbound call handling** via Plivo WebSocket streaming
- **Multilingual STT/TTS** (5 Indian languages via Sarvam AI)
- **Intent detection** — appointment booking, site visit, callback requests
- **Appointment scheduling** — natural language date/time parsing → Make.com API
- **Noise filtering** — ignores filler sounds ("hmm", "uh", "umm")
- **Filler sounds** — plays thinking phrases during API latency to keep calls natural
- **FAQ integration** — answers questions about Citadel pricing, location, amenities
- **Session state** — per-call language lock and conversation state tracking

### Property Knowledge (Citadel)
- Location: Hiranandani Fortune City, Panvel, Navi Mumbai
- Configuration: 4 BHK luxury villas
- Price range: ₹3.8 – ₹5.5 Crore
- Possession: September 2027
- Business hours: Mon–Fri, 10:30 AM – 6:30 PM

---

## Environment Variables

Create a `.env` file in the project root:

```env
OPENAI_API_KEY=sk-proj-...
SARVAM_API_KEY=sk_...
PLIVO_AUTH_ID=...
PLIVO_AUTH_TOKEN=...
PLIVO_PHONE_NUMBER=+91...
APPOINTMENT_API_URL=https://hook.eu2.make.com/...
ASSISTANT_ID=...
FROM_NUMBER=+91...
DOMAIN=your-tunnel.trycloudflare.com
```

`DOMAIN` must be the public hostname of your Cloudflare tunnel — Plivo uses it to connect to the WebSocket.

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

Copy `.env.example` (if present) or create `.env` and fill in all values listed above.

### 4. Start the server

```bash
python server.py
```

Server runs on `http://0.0.0.0:8000`.

### 5. Expose via Cloudflare Tunnel

```bash
cloudflared tunnel --url http://localhost:8000
```

Copy the generated `*.trycloudflare.com` URL and set it as `DOMAIN` in `.env`.

### 6. Configure Plivo webhook

In your Plivo console, set the answer URL for the inbound number to:

```
https://{DOMAIN}/answerCall
```

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` / `POST` | `/answerCall` | Receives Plivo call, returns XML with WebSocket URL |
| `WebSocket` | `/ws` | Streams µ-law audio for active calls |

---

## Call Flow

1. Caller dials the Plivo number
2. Plivo `GET /answerCall` → server returns XML with WebSocket URL
3. Plivo streams audio to `/ws`
4. Pipeline: audio → STT → noise filter → intent router → LLM → TTS → back to caller
5. Ciya greets the caller and handles the conversation
6. If appointment intent is detected, date/time is parsed and sent to Make.com

---

## Session State

All state is **in-memory** and scoped to `CallUUID`. It is lost on server restart.

| Dict | Keys | Purpose |
|---|---|---|
| `call_sessions` | `call_id → phone` | Maps call UUID to caller phone number |
| `language_sessions` | `call_id → {language, locked, pending}` | Tracks detected/confirmed language |
| `conversation_states` | `call_id → {state, type}` | Appointment form state machine |
| `call_states` | `call_id → {}` | Reserved for additional state |

---

## Notes

- The `LanguageProcessor` and `AppointmentProcessor` are present in the codebase but currently **commented out** of the main pipeline in `agent_bengali.py`. Enable them by uncommenting the relevant lines.
- Context is compressed to the **last 8 messages** per call to limit token usage.
- Silence is detected via **RMS energy threshold** (default: 500) on raw audio bytes.
