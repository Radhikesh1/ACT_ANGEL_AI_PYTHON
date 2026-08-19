import uuid

from loguru import logger
from openai import AsyncOpenAI

from services import sms_client
from services.chat_agent import run_chat
from services.message_log import log_message


async def handle_message(
    *,
    organization_id: str | None,
    assistant_id: uuid.UUID | None,
    assistant_config: dict,
    plivo_number: str,
    from_number: str,
    auth_id: str,
    auth_token: str,
    openai_api_key: str,
    message_uuid: str | None,
    body: str,
) -> None:
    """Handle one inbound SMS: run the text-only AI agent and send the reply.
    Text-only — no voice/image/PDF equivalent exists for SMS."""

    system_prompt = assistant_config.get("system_prompt") or "You are a helpful assistant."
    llm_model = assistant_config.get("llm_model") or "gpt-4o-mini"
    temperature = float(assistant_config.get("temperature") or 0.2)

    await log_message(
        channel="sms",
        organization_id=organization_id,
        assistant_id=assistant_id,
        contact_id=from_number,
        channel_number=plivo_number,
        external_message_id=message_uuid,
        direction="inbound",
        message_type="text",
        content=body,
    )

    if not openai_api_key:
        logger.error(f"[SMS] No OpenAI API key configured for org={organization_id} — cannot process message")
        return

    try:
        client = AsyncOpenAI(api_key=openai_api_key)
        reply = await run_chat(client, "sms", from_number, system_prompt, llm_model, temperature, body)
        await sms_client.send_reply_sms(auth_id, auth_token, plivo_number, from_number, reply)
        await log_message(
            channel="sms",
            organization_id=organization_id,
            assistant_id=assistant_id,
            contact_id=from_number,
            channel_number=plivo_number,
            external_message_id=None,
            direction="outbound",
            message_type="text",
            content=reply,
            status="replied",
        )
    except Exception as e:
        logger.error(f"[SMS] Error handling message from {from_number}: {e}")
        await log_message(
            channel="sms",
            organization_id=organization_id,
            assistant_id=assistant_id,
            contact_id=from_number,
            channel_number=plivo_number,
            external_message_id=message_uuid,
            direction="inbound",
            message_type="text",
            content=body,
            status="error",
            error_message=str(e),
        )
