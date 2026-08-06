import os

from loguru import logger
from pipecat.services.sarvam.tts import SarvamTTSService

from utils.language_manager import get_voice

# Last-resort fallback only — the primary source is the per-org/global
# `voice_provider_settings` DB row (SAD-configurable from the dashboard),
# resolved by the caller and passed in as `api_key`. Not required at
# startup: a deployment can rely entirely on the dashboard-configured
# global default with no Sarvam key in .env at all.
SARVAM_API_KEY: str = os.getenv("SARVAM_API_KEY") or ""


def create_tts(
    call_id,
    default_language: str = "english",
    voice: str | None = None,
    api_key: str | None = None,
):
    from utils.language_manager import initialize_language_session
    initialize_language_session(call_id, default_language)

    # Use explicitly configured voice if provided, otherwise derive from language
    resolved_voice = voice or get_voice(call_id)

    resolved_api_key = api_key or SARVAM_API_KEY
    if not resolved_api_key:
        logger.warning(
            f"[TTS] No Sarvam API key configured (call {call_id}) — set a global "
            "default on the Global API Defaults page, or SARVAM_API_KEY in .env."
        )

    return SarvamTTSService(

        api_key=resolved_api_key,

        settings=SarvamTTSService.Settings(
            voice=resolved_voice,
            model="bulbul:v3",
        ),
    )