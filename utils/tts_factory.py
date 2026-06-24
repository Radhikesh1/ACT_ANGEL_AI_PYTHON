from dotenv import load_dotenv
from pipecat.services.sarvam.tts import (
    SarvamTTSService,
)

from utils.language_manager import (
    get_voice,
)

import os

load_dotenv()

SARVAM_API_KEY: str = os.getenv(
    "SARVAM_API_KEY"
) or ""


if not SARVAM_API_KEY:
    raise ValueError("SARVAM_API_KEY missing")


def create_tts(call_id):

    voice = get_voice(call_id)

    return SarvamTTSService(

        api_key=SARVAM_API_KEY,

        settings=SarvamTTSService.Settings(
            voice=voice,
            model="bulbul:v3",
        ),
    )