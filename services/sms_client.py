import httpx
from loguru import logger

PLIVO_API_BASE = "https://api.plivo.com/v1/Account"


async def send_sms(auth_id: str, auth_token: str, src: str, dst: str, text: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{PLIVO_API_BASE}/{auth_id}/Message/",
            auth=(auth_id, auth_token),
            json={"src": src, "dst": dst, "text": text},
        )
        resp.raise_for_status()
        return resp.json()


async def send_reply_sms(auth_id: str, auth_token: str, src: str, dst: str, text: str) -> None:
    try:
        await send_sms(auth_id, auth_token, src, dst, text)
    except Exception as e:
        logger.error(f"[SMS] send_sms failed src={src} dst={dst}: {e}")
