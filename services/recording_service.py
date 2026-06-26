"""
Call recording service.

Flow:
  1. start_recording(call_id)  — tells Plivo to begin recording the call
  2. fetch_and_upload(call_id) — after call ends:
       a. poll Plivo until the MP3 is ready (up to ~60 s)
       b. download the MP3 bytes from Plivo (requires Basic auth)
       c. upload to Cloudinary (resource_type=video so audio is accepted)
       d. return the Cloudinary secure URL
"""

import asyncio
import os
from io import BytesIO

import httpx
import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

PLIVO_AUTH_ID: str = os.getenv("PLIVO_AUTH_ID") or ""
PLIVO_AUTH_TOKEN: str = os.getenv("PLIVO_AUTH_TOKEN") or ""
PLIVO_API = f"https://api.plivo.com/v1/Account/{PLIVO_AUTH_ID}"

CLOUDINARY_CLOUD_NAME: str = os.getenv("CLOUDINARY_CLOUD_NAME") or ""
CLOUDINARY_API_KEY: str = os.getenv("CLOUDINARY_API_KEY") or ""
CLOUDINARY_API_SECRET: str = os.getenv("CLOUDINARY_API_SECRET") or ""

_cloudinary_configured = False


def _ensure_cloudinary():
    global _cloudinary_configured
    if _cloudinary_configured:
        return
    if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
        raise ValueError("Cloudinary credentials missing in .env")
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True,
    )
    _cloudinary_configured = True


# ── Plivo helpers ─────────────────────────────────────────────────────────────

async def start_recording(call_id: str) -> bool:
    """
    Ask Plivo to start recording the live call.
    Returns True on success.
    """
    if not PLIVO_AUTH_ID or not PLIVO_AUTH_TOKEN:
        logger.warning("[Recording] Plivo credentials missing — skipping recording start")
        return False
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                f"{PLIVO_API}/Call/{call_id}/Record/",
                auth=(PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN),
                json={"time_limit": 7200, "format": "mp3"},
            )
            if resp.status_code in (200, 201, 202):
                logger.info(f"[Recording] Started recording for call {call_id}")
                return True
            logger.warning(f"[Recording] Plivo Record start returned {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        logger.warning(f"[Recording] Failed to start recording: {e}")
        return False


async def _fetch_plivo_recording_url(call_id: str, max_wait: int = 60) -> tuple[str, int] | None:
    """
    Poll Plivo until a recording for this call is available.
    Waits up to `max_wait` seconds (checks every 5 s).
    Returns (recording_url, duration_seconds) or None.
    """
    if not PLIVO_AUTH_ID or not PLIVO_AUTH_TOKEN:
        return None

    deadline = asyncio.get_running_loop().time() + max_wait
    async with httpx.AsyncClient(timeout=10) as client:
        while asyncio.get_running_loop().time() < deadline:
            try:
                resp = await client.get(
                    f"{PLIVO_API}/Recording/",
                    auth=(PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN),
                    params={"call_uuid": call_id, "limit": 1},
                )
                if resp.status_code == 200:
                    objects = resp.json().get("objects", [])
                    if objects:
                        rec = objects[0]
                        url = rec.get("recording_url") or rec.get("url")
                        if url:
                            duration_secs = int(
                                rec.get("recording_duration")
                                or rec.get("duration")
                                or 0
                            )
                            logger.info(
                                f"[Recording] Plivo recording ready for {call_id} "
                                f"(duration={duration_secs}s)"
                            )
                            return url, duration_secs
                await asyncio.sleep(5)
            except Exception as e:
                logger.warning(f"[Recording] Poll error: {e}")
                await asyncio.sleep(5)

    logger.warning(f"[Recording] Recording not ready for {call_id} after {max_wait}s")
    return None


async def _download_plivo_recording(plivo_url: str) -> bytes | None:
    """Download the MP3 from Plivo (requires Basic auth)."""
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(
                plivo_url,
                auth=(PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN),
            )
            resp.raise_for_status()
            return resp.content
    except Exception as e:
        logger.error(f"[Recording] Download failed: {e}")
        return None


def _upload_to_cloudinary(audio_bytes: bytes, call_id: str) -> str | None:
    """Upload MP3 bytes to Cloudinary and return the secure URL."""
    _ensure_cloudinary()
    try:
        result = cloudinary.uploader.upload(
            BytesIO(audio_bytes),
            resource_type="video",          # Cloudinary uses "video" for audio files
            folder="call-recordings",
            public_id=f"call-{call_id}",
            overwrite=True,
            format="mp3",
        )
        url: str = result.get("secure_url") or result.get("url") or ""
        logger.info(f"[Recording] Uploaded to Cloudinary: {url}")
        return url or None
    except Exception as e:
        logger.error(f"[Recording] Cloudinary upload failed: {e}")
        return None


async def fetch_and_upload(call_id: str) -> tuple[str | None, int | None]:
    """
    Full pipeline: poll Plivo → download → upload Cloudinary.
    Returns (cloudinary_url, recording_duration_seconds).
    Both values are None on failure or missing credentials.
    """
    if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
        logger.info("[Recording] Cloudinary not configured — skipping upload")
        return None, None

    result = await _fetch_plivo_recording_url(call_id)
    if not result:
        return None, None
    plivo_url, duration_secs = result

    audio_bytes = await _download_plivo_recording(plivo_url)
    if not audio_bytes:
        return None, None

    loop = asyncio.get_running_loop()
    cloudinary_url = await loop.run_in_executor(
        None,
        lambda: _upload_to_cloudinary(audio_bytes, call_id),
    )
    return cloudinary_url, duration_secs
