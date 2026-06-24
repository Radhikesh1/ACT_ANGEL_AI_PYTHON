import random

from utils.filler_text import (
    FILLERS_ENGLISH,
    FILLERS_BENGALI,
    APPOINTMENT_FILLERS,
)


def get_filler(language="english"):

    if language == "bengali":
        return random.choice(FILLERS_BENGALI)

    return random.choice(FILLERS_ENGLISH)


def get_appointment_filler(language="english"):

    return random.choice(
        APPOINTMENT_FILLERS.get(
            language,
            APPOINTMENT_FILLERS["english"],
        )
    )