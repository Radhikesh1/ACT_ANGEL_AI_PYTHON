import os

from pipecat.services.sarvam.tts import SarvamTTSService

from utils.language_manager import get_voice

SARVAM_API_KEY: str = os.getenv(
    "SARVAM_API_KEY"
) or ""


if not SARVAM_API_KEY:
    raise ValueError("SARVAM_API_KEY missing")


def create_tts(call_id, default_language: str = "english", voice: str | None = None):
    from utils.language_manager import initialize_language_session
    initialize_language_session(call_id, default_language)

    # Use explicitly configured voice if provided, otherwise derive from language
    resolved_voice = voice or get_voice(call_id)

    return SarvamTTSService(

        api_key=SARVAM_API_KEY,

        settings=SarvamTTSService.Settings(
            voice=resolved_voice,
            model="bulbul:v3",
        ),
    )