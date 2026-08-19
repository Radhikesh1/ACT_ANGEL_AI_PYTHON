import os

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from loguru import logger
from sqlalchemy import select

from database.connection import get_db
from database.models import VoiceNumber, Assistant, AssistantExtension, BYOKRateVersion, ModelPricingVersion
from utils.plivo_creds import get_plivo_creds
from utils.provider_creds import get_org_provider_key
from migrations.runner import run_migrations
from utils.session_state import call_sessions, _redis_client, _REDIS_URL
from utils.phone_utils import mask_phone
from voice_agent import run_bot

from api.auth import router as auth_router
from api.assistants import router as assistants_router
from api.call_logs import router as call_logs_router
from api.whatsapp import router as whatsapp_router

load_dotenv()

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
app.include_router(whatsapp_router, prefix="/api/whatsapp")

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
    resolved_org_id: str | None = None

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
                if asst.organization_id:
                    resolved_org_id = str(asst.organization_id)

        plivo_auth_id, plivo_auth_token = await get_plivo_creds(db, resolved_org_id or "")
        if not plivo_auth_id:
            logger.warning(
                f"[AnswerCall] No Plivo credentials found for org={resolved_org_id} — "
                "auto-hangup and recording will be unavailable for this call. "
                "Import a number with credentials to fix this."
            )

        # Org-specific overrides for Sarvam/OpenAI/Cloudinary — empty string
        # means "nothing configured", voice_agent.py falls back to its env vars.
        org_id_for_lookup = resolved_org_id or ""
        sarvam_api_key = await get_org_provider_key(db, org_id_for_lookup, "sarvam")
        openai_api_key = await get_org_provider_key(db, org_id_for_lookup, "openai")
        cloudinary_cloud_name = await get_org_provider_key(db, org_id_for_lookup, "cloudinary", "cloud_name")
        cloudinary_api_key = await get_org_provider_key(db, org_id_for_lookup, "cloudinary", "api_key")
        cloudinary_api_secret = await get_org_provider_key(db, org_id_for_lookup, "cloudinary", "api_secret")

        # BYOK flat-fee rates — versioned per-assistant (mirrors the active
        # marginVersion, set from the WEB "Update Margin" popup), falling
        # back to the active global BYOK rate version (Global Settings →
        # BYOK Billing Rates tab, versioned the same way exchange rates are)
        # whenever the assistant's active version left a field unset (NULL).
        # Empty string means "not configured anywhere", voice_agent.py falls
        # back to its own env-configured defaults.
        assistant_ext: AssistantExtension | None = None
        if assistant_config:
            ext_result = await db.execute(
                select(AssistantExtension).where(
                    AssistantExtension.assistant_external_id == assistant_config["id"]
                )
            )
            assistant_ext = ext_result.scalar_one_or_none()

        active_byok_result = await db.execute(
            select(BYOKRateVersion)
            .where(BYOKRateVersion.is_active.is_(True))
            .limit(1)
        )
        active_byok_rate = active_byok_result.scalars().first()

        byok_stt_rate = (
            str(assistant_ext.byok_stt_flat_fee_per_minute)
            if assistant_ext and assistant_ext.byok_stt_flat_fee_per_minute is not None
            else (str(active_byok_rate.stt_flat_fee_per_minute) if active_byok_rate else "")
        )
        byok_llm_rate = (
            str(assistant_ext.byok_llm_flat_fee_per_minute)
            if assistant_ext and assistant_ext.byok_llm_flat_fee_per_minute is not None
            else (str(active_byok_rate.llm_flat_fee_per_minute) if active_byok_rate else "")
        )

        # Actual-cost basis (Global Settings > Model Pricing tab) — the
        # per-model LLM rates plus STT/phone/platform per-minute rates that
        # cost_service.calculate_cost() uses instead of its own env-configured
        # defaults, when a version has been activated.
        active_pricing_result = await db.execute(
            select(ModelPricingVersion)
            .where(ModelPricingVersion.is_active.is_(True))
            .limit(1)
        )
        active_model_pricing = active_pricing_result.scalars().first()
        model_pricing = active_model_pricing.rates if active_model_pricing else None

    call_sessions[call_uuid] = {
        "from_number": from_number,
        "to_number": to_number,
        "assistant_config": assistant_config,
        "organization_id": resolved_org_id,
        "plivo_auth_id": plivo_auth_id,
        "plivo_auth_token": plivo_auth_token,
        "sarvam_api_key": sarvam_api_key,
        "openai_api_key": openai_api_key,
        "cloudinary_cloud_name": cloudinary_cloud_name,
        "cloudinary_api_key": cloudinary_api_key,
        "cloudinary_api_secret": cloudinary_api_secret,
        "byok_stt_rate": byok_stt_rate,
        "byok_llm_rate": byok_llm_rate,
        "model_pricing": model_pricing,
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
        wss://{DOMAIN}/ws
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
