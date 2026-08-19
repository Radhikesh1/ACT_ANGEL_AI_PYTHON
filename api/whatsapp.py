import hashlib
import hmac
import os

from fastapi import APIRouter, Request, Response
from loguru import logger
from sqlalchemy import select

from database.connection import get_db
from database.models import Assistant, WhatsAppNumber
from services.whatsapp_agent import handle_message
from utils.provider_creds import get_org_provider_key

router = APIRouter()

WEBHOOK_VERIFY_TOKEN: str = os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN") or ""
APP_SECRET: str = os.getenv("WHATSAPP_APP_SECRET") or ""
ACCESS_TOKEN_FALLBACK: str = os.getenv("WHATSAPP_ACCESS_TOKEN") or ""
OPENAI_API_KEY_FALLBACK: str = os.getenv("OPENAI_API_KEY") or ""


def _verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """HMAC-SHA256 of the raw body against the app secret, per Meta's
    X-Hub-Signature-256 contract. Skipped (returns True) if no app secret is
    configured, so local/dev setups without one keep working."""
    if not APP_SECRET:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header[len("sha256="):])


@router.get("/webhook")
async def verify_webhook(request: Request):
    """Meta's webhook verification handshake — no phone_number_id is present
    here, so the verify token is necessarily a single global secret."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN and WEBHOOK_VERIFY_TOKEN:
        return Response(content=challenge or "", media_type="text/plain")
    return Response(status_code=403)


@router.post("/webhook")
async def receive_webhook(request: Request):
    raw_body = await request.body()
    if not _verify_signature(raw_body, request.headers.get("X-Hub-Signature-256")):
        logger.warning("[WhatsApp] Rejected webhook — signature verification failed")
        return Response(status_code=403)

    payload = await request.json()

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages") or []
            if not messages:
                continue  # status updates (delivered/read) etc. — nothing to do

            phone_number_id = value.get("metadata", {}).get("phone_number_id", "")

            async for db in get_db():
                result = await db.execute(
                    select(WhatsAppNumber).where(WhatsAppNumber.phone_number_id == phone_number_id)
                )
                number_rec = result.scalar_one_or_none()

                organization_id = number_rec.organization_id if number_rec else None
                assistant_config: dict = {}
                assistant_id = number_rec.assistant_id if number_rec else None
                if assistant_id:
                    asst = await db.get(Assistant, assistant_id)
                    if asst:
                        assistant_config = {
                            "system_prompt": asst.system_prompt,
                            "llm_model": asst.llm_model,
                            "temperature": asst.temperature,
                            "voice": asst.voice,
                        }

                org_id_for_lookup = organization_id or ""
                access_token = (
                    await get_org_provider_key(db, org_id_for_lookup, "whatsapp", "access_token", str(assistant_id) if assistant_id else None)
                    or ACCESS_TOKEN_FALLBACK
                )
                openai_api_key = (
                    await get_org_provider_key(db, org_id_for_lookup, "openai", "api_key", str(assistant_id) if assistant_id else None)
                    or OPENAI_API_KEY_FALLBACK
                )

                if not access_token:
                    logger.error(f"[WhatsApp] No access token configured for phone_number_id={phone_number_id} — dropping message")
                    continue

                for message in messages:
                    wa_id = message.get("from", "")
                    await handle_message(
                        organization_id=organization_id,
                        assistant_id=assistant_id,
                        assistant_config=assistant_config,
                        phone_number_id=phone_number_id,
                        wa_id=wa_id,
                        access_token=access_token,
                        openai_api_key=openai_api_key,
                        message=message,
                    )

    return {"status": "ok"}
