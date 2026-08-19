import os

import plivo.utils
from fastapi import APIRouter, Request, Response
from loguru import logger
from sqlalchemy import select

from database.connection import get_db
from database.models import Assistant, VoiceNumber
from services.sms_agent import handle_message
from utils.plivo_creds import get_plivo_creds
from utils.provider_creds import get_org_provider_key

router = APIRouter()

DOMAIN: str = os.getenv("DOMAIN") or ""


def _number_variants(number: str) -> list[str]:
    """Plivo sends numbers without '+'; VoiceNumber rows may be stored with or
    without it — try both so the lookup succeeds regardless."""
    if number.startswith("+"):
        return [number, number[1:]]
    return [number, "+" + number]


def _verify_signature(request: Request, auth_token: str, params: dict) -> bool:
    """Verify X-Plivo-Signature-V3 via Plivo's own SDK helper — its exact
    concatenation format (sorted params + nonce) is easy to get subtly wrong
    by hand, so this defers to the vendor's implementation. Skipped (returns
    True) when no auth_token is resolved yet or no signature header is
    present, so local/dev setups without one keep working."""
    signature = request.headers.get("X-Plivo-Signature-V3")
    nonce = request.headers.get("X-Plivo-Signature-V3-Nonce")
    if not auth_token or not signature or not nonce:
        return True
    webhook_url = f"https://{DOMAIN}/api/sms/webhook" if DOMAIN else str(request.url)
    return plivo.utils.validate_v3_signature("POST", webhook_url, nonce, auth_token, signature, params)


@router.post("/webhook")
async def receive_webhook(request: Request):
    form = await request.form()
    params = dict(form)

    from_number = params.get("From", "")
    to_number = params.get("To", "")
    body = params.get("Text", "")
    message_uuid = params.get("MessageUUID")

    async for db in get_db():
        result = await db.execute(
            select(VoiceNumber).where(VoiceNumber.number.in_(_number_variants(to_number)))
        )
        number_rec = result.scalar_one_or_none()

        organization_id = number_rec.organization_id if number_rec else None
        assistant_id = number_rec.assistant_id if number_rec else None
        assistant_config: dict = {}
        if assistant_id:
            asst = await db.get(Assistant, assistant_id)
            if asst:
                assistant_config = {
                    "system_prompt": asst.system_prompt,
                    "llm_model": asst.llm_model,
                    "temperature": asst.temperature,
                }

        auth_id, auth_token = await get_plivo_creds(db, organization_id or "")

        if not _verify_signature(request, auth_token, params):
            logger.warning(f"[SMS] Rejected webhook — signature verification failed (to={to_number})")
            return Response(status_code=403)

        if not auth_id or not auth_token:
            logger.error(f"[SMS] No Plivo credentials configured for to={to_number} — dropping message")
            return {"status": "ok"}

        openai_api_key = (
            await get_org_provider_key(db, organization_id or "", "openai", "api_key", str(assistant_id) if assistant_id else None)
            or os.getenv("OPENAI_API_KEY")
            or ""
        )

        await handle_message(
            organization_id=organization_id,
            assistant_id=assistant_id,
            assistant_config=assistant_config,
            plivo_number=to_number,
            from_number=from_number,
            auth_id=auth_id,
            auth_token=auth_token,
            openai_api_key=openai_api_key,
            message_uuid=message_uuid,
            body=body,
        )

    return {"status": "ok"}
