import os

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from loguru import logger
from sqlalchemy import select, or_

from database.connection import get_db
from database.models import VoiceNumber, Assistant
from migrations.runner import run_migrations
from utils.session_state import call_sessions
from voice_agent import run_bot

from api.auth import router as auth_router
from api.assistants import router as assistants_router
from api.call_logs import router as call_logs_router

load_dotenv()

app = FastAPI(title="ACT Angel AI API")

# --------------------------------------------------
# CORS  (allow the frontend origin in dev + prod)
# --------------------------------------------------

ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "https://actangels.com",
    "https://www.actangels.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------
# Startup
# --------------------------------------------------

@app.on_event("startup")
async def startup():
    await run_migrations()
    logger.info("Migrations complete — server ready")

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
                }

    resolved_org_id = str(asst.organization_id) if assistant_config and asst.organization_id else None

    call_sessions[call_uuid] = {
        "from_number": from_number,
        "to_number": to_number,
        "assistant_config": assistant_config,
        "organization_id": resolved_org_id,
    }

    logger.info(
        f"Inbound call {call_uuid} from {from_number} → {to_number} | "
        f"assistant: {assistant_config['name'] if assistant_config else 'DEFAULT (no number match)'} | "
        f"org: {resolved_org_id or 'UNRESOLVED'}"
    )

    domain = os.getenv("DOMAIN", "localhost:8000")

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
