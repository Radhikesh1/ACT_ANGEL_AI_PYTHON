import os

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from loguru import logger
from sqlalchemy import select

from database.connection import get_db
from database.models import VoiceNumber, Assistant
from migrations.runner import run_migrations
from utils.session_state import call_sessions, _redis_client, _REDIS_URL
from voice_agent import run_bot

from api.auth import router as auth_router
from api.assistants import router as assistants_router
from api.call_logs import router as call_logs_router

load_dotenv()


def mask_phone(phone: str) -> str:
    """Mask phone number for logging, keeping only last 4 digits."""
    if not phone:
        return ""
    digits = ''.join(c for c in phone if c.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 4) + digits[-4:]


app = FastAPI(title="ACT Angel AI API")

# --------------------------------------------------
# CORS  (allow the frontend origin in dev + prod)
# --------------------------------------------------

_origins_env = os.getenv("ALLOWED_ORIGINS")
if not _origins_env:
    raise ValueError("ALLOWED_ORIGINS missing — add it to .env")
ALLOWED_ORIGINS = [o.strip() for o in _origins_env.split(",") if o.strip()]

DOMAIN: str = os.getenv("DOMAIN") or ""
if not DOMAIN:
    raise ValueError("DOMAIN missing — add it to .env")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Org-Id", "Cookie"],
)

# --------------------------------------------------
# Startup
# --------------------------------------------------

@app.on_event("startup")
async def startup():
    await run_migrations()
    logger.info("Migrations complete — server ready")

    # --------------------------------------------------
    # Redis health check (non-fatal)
    # --------------------------------------------------
    if _redis_client is None:
        logger.warning(
            f"[Redis] Not connected to {_REDIS_URL}. "
            "Active call sessions will not survive server restarts. "
            "Set REDIS_URL and ensure Redis is running for production."
        )
    else:
        try:
            _redis_client.ping()
            logger.info(f"[Redis] Healthy at {_REDIS_URL}")
        except Exception as exc:
            logger.warning(
                f"[Redis] Ping failed at startup ({exc}). "
                "Session persistence is degraded — falling back to in-memory storage."
            )

# --------------------------------------------------
# API Routers
# --------------------------------------------------

app.include_router(auth_router, prefix="/api")
app.include_router(assistants_router, prefix="/api")
app.include_router(call_logs_router, prefix="/api")

# --------------------------------------------------
# Plivo Inbound Call — returns XML + stores config
# --------------------------------------------------

@app.get("/answerCall")
async def get_answer_xml(request: Request):
    params = dict(request.query_params)

    call_uuid = params.get("CallUUID", "")
    from_number = params.get("From", "")
    to_number = params.get("To", "")      # the Plivo number that was called

    # Plivo sends numbers without '+'; DB may store them with or without it.
    # Try both variants so the lookup succeeds regardless of how numbers were saved.
    to_number_variants = [to_number]
    if to_number.startswith("+"):
        to_number_variants.append(to_number[1:])
    else:
        to_number_variants.append("+" + to_number)

    # Look up which assistant owns this number
    assistant_config: dict | None = None

    async for db in get_db():
        result = await db.execute(
            select(VoiceNumber).where(VoiceNumber.number.in_(to_number_variants))
        )
        plivo_rec = result.scalar_one_or_none()

        if plivo_rec and plivo_rec.assistant_id:
            asst = await db.get(Assistant, plivo_rec.assistant_id)
            if asst:
                assistant_config = {
                    "id": str(asst.id),
                    "name": asst.name,
                    "system_prompt": asst.system_prompt,
                    "welcome_message": asst.welcome_message,
                    "default_language": asst.default_language,
                    "voice": asst.voice,
                    "llm_model": asst.llm_model,
                    "temperature": asst.temperature,
                    "business_hours_start": asst.business_hours_start,
                    "business_hours_end": asst.business_hours_end,
                    "prefetch_webhook_url": asst.prefetch_webhook_url,
                    "end_of_call_webhook_url": asst.end_of_call_webhook_url,
                    "faq_items": asst.faq_items or [],
                    "intent_triggers": asst.intent_triggers or [],
                    "filler_messages": asst.filler_messages or {},
                }

    resolved_org_id = str(asst.organization_id) if assistant_config and asst.organization_id else None

    call_sessions[call_uuid] = {
        "from_number": from_number,
        "to_number": to_number,
        "assistant_config": assistant_config,
        "organization_id": resolved_org_id,
    }

    logger.info(
        f"Inbound call {call_uuid} from {mask_phone(from_number)} → {mask_phone(to_number)} | "
        f"assistant: {assistant_config['name'] if assistant_config else 'DEFAULT (no number match)'} | "
        f"org: {resolved_org_id or 'UNRESOLVED'}"
    )

    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream
        bidirectional="true"
        keepCallAlive="true"
        contentType="audio/x-mulaw;rate=8000">
        wss://{domain}/ws
    </Stream>
</Response>"""

    return Response(content=xml_content, media_type="application/xml")

# --------------------------------------------------
# WebSocket — real-time audio pipeline
# --------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket connected")

    try:
        await run_bot(websocket)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")

    except Exception as e:
        logger.error(f"WebSocket error: {e}")

# --------------------------------------------------
# Entry point
# --------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
