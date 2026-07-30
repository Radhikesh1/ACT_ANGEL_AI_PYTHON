import random

from utils.filler_text import APPOINTMENT_FILLERS


def get_appointment_filler(language="english"):

    return random.choice(
        APPOINTMENT_FILLERS.get(
            language,
            APPOINTMENT_FILLERS["english"],
        )
    )