import uuid
from io import BytesIO

from loguru import logger
from openai import AsyncOpenAI

from services import whatsapp_client
from services.chat_agent import run_chat
from services.message_log import log_message
from utils.pdf_text import extract_pdf_text

NOT_SUPPORTED_MESSAGE = "You can only send text messages, images, audio files and PDF documents."
INCORRECT_FORMAT_MESSAGE = "Sorry but you can only send PDF files."

IMAGE_ANALYSIS_PROMPT = (
    "Describe this image in detail: key subjects, objects, setting, any visible "
    "text, and overall mood. Be concise but thorough."
)


async def _log_message(
    organization_id: str | None,
    assistant_id: uuid.UUID | None,
    wa_id: str,
    phone_number_id: str,
    wa_message_id: str | None,
    direction: str,
    message_type: str,
    content: str | None,
    status: str = "received",
    error_message: str | None = None,
) -> None:
    await log_message(
        channel="whatsapp",
        organization_id=organization_id,
        assistant_id=assistant_id,
        contact_id=wa_id,
        channel_number=phone_number_id,
        external_message_id=wa_message_id,
        direction=direction,
        message_type=message_type,
        content=content,
        status=status,
        error_message=error_message,
    )


async def _run_chat(
    client: AsyncOpenAI,
    wa_id: str,
    system_prompt: str,
    llm_model: str,
    temperature: float,
    user_text: str,
) -> str:
    return await run_chat(client, "whatsapp", wa_id, system_prompt, llm_model, temperature, user_text)


async def _transcribe_audio(client: AsyncOpenAI, data: bytes, mime_type: str) -> str:
    ext = "mp3" if "mp3" in mime_type or "mpeg" in mime_type else "ogg"
    audio_file = BytesIO(data)
    audio_file.name = f"voice.{ext}"
    transcript = await client.audio.transcriptions.create(model="whisper-1", file=audio_file)
    return transcript.text


async def _analyze_image(client: AsyncOpenAI, data: bytes, mime_type: str, caption: str | None) -> str:
    import base64
    b64 = base64.b64encode(data).decode("utf-8")
    prompt = IMAGE_ANALYSIS_PROMPT
    if caption:
        prompt = f"User's caption/question: {caption}\n\n{prompt}"

    completion = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
            ],
        }],
    )
    return completion.choices[0].message.content or ""


async def _synthesize_speech(client: AsyncOpenAI, text: str, voice: str) -> bytes:
    response = await client.audio.speech.create(model="tts-1", voice=voice or "onyx", input=text)
    return response.read()


async def handle_message(
    *,
    organization_id: str | None,
    assistant_id: uuid.UUID | None,
    assistant_config: dict,
    phone_number_id: str,
    wa_id: str,
    access_token: str,
    openai_api_key: str,
    message: dict,
) -> None:
    """Dispatch one inbound WhatsApp message by type, run the AI pipeline, and
    send the appropriate reply. Mirrors the n8n workflow's per-type branches."""

    msg_type = message.get("type")
    wa_message_id = message.get("id")

    system_prompt = assistant_config.get("system_prompt") or "You are a helpful assistant."
    llm_model = assistant_config.get("llm_model") or "gpt-4o-mini"
    temperature = float(assistant_config.get("temperature") or 0.2)
    voice = assistant_config.get("voice") or "onyx"

    if not openai_api_key:
        logger.error(f"[WhatsApp] No OpenAI API key configured for org={organization_id} — cannot process message")
        return
    client = AsyncOpenAI(api_key=openai_api_key)

    await _log_message(organization_id, assistant_id, wa_id, phone_number_id, wa_message_id, "inbound", msg_type or "unknown", None)

    try:
        if msg_type == "text":
            user_text = message.get("text", {}).get("body", "")
            reply = await _run_chat(client, wa_id, system_prompt, llm_model, temperature, user_text)
            await whatsapp_client.send_reply_text(phone_number_id, access_token, wa_id, reply)
            await _log_message(organization_id, assistant_id, wa_id, phone_number_id, None, "outbound", "text", reply, status="replied")

        elif msg_type == "audio":
            media = message["audio"]
            url = await whatsapp_client.get_media_url(media["id"], access_token)
            data = await whatsapp_client.download_media(url, access_token)
            transcript = await _transcribe_audio(client, data, media.get("mime_type", "audio/ogg"))
            reply_text = await _run_chat(client, wa_id, system_prompt, llm_model, temperature, transcript)
            audio_bytes = await _synthesize_speech(client, reply_text, voice)
            await whatsapp_client.send_reply_audio(phone_number_id, access_token, wa_id, audio_bytes)
            await _log_message(organization_id, assistant_id, wa_id, phone_number_id, None, "outbound", "audio", reply_text, status="replied")

        elif msg_type == "image":
            media = message["image"]
            caption = media.get("caption")
            url = await whatsapp_client.get_media_url(media["id"], access_token)
            data = await whatsapp_client.download_media(url, access_token)
            description = await _analyze_image(client, data, media.get("mime_type", "image/jpeg"), caption)
            user_text = f"User request on the image:\n{caption or 'Describe the following image'}\n\nImage description:\n{description}"
            reply = await _run_chat(client, wa_id, system_prompt, llm_model, temperature, user_text)
            await whatsapp_client.send_reply_text(phone_number_id, access_token, wa_id, reply)
            await _log_message(organization_id, assistant_id, wa_id, phone_number_id, None, "outbound", "text", reply, status="replied")

        elif msg_type == "document":
            media = message["document"]
            if media.get("mime_type") != "application/pdf":
                await whatsapp_client.send_reply_text(phone_number_id, access_token, wa_id, INCORRECT_FORMAT_MESSAGE)
                await _log_message(organization_id, assistant_id, wa_id, phone_number_id, None, "outbound", "text", INCORRECT_FORMAT_MESSAGE, status="rejected")
                return
            url = await whatsapp_client.get_media_url(media["id"], access_token)
            data = await whatsapp_client.download_media(url, access_token)
            pdf_text = extract_pdf_text(data)
            user_text = f"User request on the file:\n{media.get('caption') or 'Describe this file'}\n\nFile content:\n{pdf_text}"
            reply = await _run_chat(client, wa_id, system_prompt, llm_model, temperature, user_text)
            await whatsapp_client.send_reply_text(phone_number_id, access_token, wa_id, reply)
            await _log_message(organization_id, assistant_id, wa_id, phone_number_id, None, "outbound", "text", reply, status="replied")

        else:
            await whatsapp_client.send_reply_text(phone_number_id, access_token, wa_id, NOT_SUPPORTED_MESSAGE)
            await _log_message(organization_id, assistant_id, wa_id, phone_number_id, None, "outbound", "text", NOT_SUPPORTED_MESSAGE, status="rejected")

    except Exception as e:
        logger.error(f"[WhatsApp] Error handling {msg_type} message from {wa_id}: {e}")
        await _log_message(organization_id, assistant_id, wa_id, phone_number_id, wa_message_id, "inbound", msg_type or "unknown", None, status="error", error_message=str(e))
