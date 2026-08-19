import os

import httpx
from loguru import logger

GRAPH_API_VERSION = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v20.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


async def get_media_url(media_id: str, access_token: str) -> str:
    """Step 1 of media download: resolve a media id to its short-lived download URL."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{GRAPH_BASE}/{media_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()["url"]


async def download_media(url: str, access_token: str) -> bytes:
    """Step 2 of media download: fetch the raw bytes from the short-lived URL.
    Must be called immediately after get_media_url — the URL expires quickly."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
        resp.raise_for_status()
        return resp.content


async def send_text(phone_number_id: str, access_token: str, to: str, body: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{GRAPH_BASE}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": body},
            },
        )
        resp.raise_for_status()
        return resp.json()


async def upload_media(phone_number_id: str, access_token: str, data: bytes, mime_type: str, filename: str) -> str:
    """Upload bytes (e.g. TTS audio) to Meta's Media endpoint, returning a media id
    usable in a subsequent send call — required for audio replies (no reliable
    public-link path for outbound audio)."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{GRAPH_BASE}/{phone_number_id}/media",
            headers={"Authorization": f"Bearer {access_token}"},
            data={"messaging_product": "whatsapp"},
            files={"file": (filename, data, mime_type)},
        )
        resp.raise_for_status()
        return resp.json()["id"]


async def send_audio(phone_number_id: str, access_token: str, to: str, media_id: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{GRAPH_BASE}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "audio",
                "audio": {"id": media_id},
            },
        )
        resp.raise_for_status()
        return resp.json()


async def send_reply_text(phone_number_id: str, access_token: str, to: str, body: str) -> None:
    try:
        await send_text(phone_number_id, access_token, to, body)
    except Exception as e:
        logger.error(f"[WhatsApp] send_text failed to={to}: {e}")


async def send_reply_audio(phone_number_id: str, access_token: str, to: str, data: bytes, mime_type: str = "audio/mpeg") -> None:
    try:
        media_id = await upload_media(phone_number_id, access_token, data, mime_type, "reply.mp3")
        await send_audio(phone_number_id, access_token, to, media_id)
    except Exception as e:
        logger.error(f"[WhatsApp] send_audio failed to={to}: {e}")
