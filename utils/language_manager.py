from data.multilingual_keywords import (
    NEGATIVE_WORDS,
    POSITIVE_WORDS,
    VOICE_MAPPING,
)

from utils.session_state import (
    language_sessions,
)



# ---------------------------------------------------
# GET LANGUAGE
# ---------------------------------------------------

def get_language(call_id):

    session = language_sessions.get(
        call_id,
        {}
    )

    return session.get(
        "language",
        "english",
    )

# ---------------------------------------------------
# GET VOICE
# ---------------------------------------------------

def get_voice(call_id):

    language = get_language(call_id)

    return VOICE_MAPPING.get(
        language,
        "priya",
    )

# ---------------------------------------------------
# SET LANGUAGE
# ---------------------------------------------------

def set_language(
    call_id,
    language,
):

    session = language_sessions.get(
        call_id,
        {}
    )

    session["language"] = language

    session["locked"] = True

    session["pending"] = None

    language_sessions[call_id] = session

# ---------------------------------------------------
# SET PENDING LANGUAGE
# ---------------------------------------------------

def set_pending_language(
    call_id,
    language,
):

    session = language_sessions.get(
        call_id,
        {}
    )

    session["pending"] = language

    language_sessions[call_id] = session

# ---------------------------------------------------
# CLEAR PENDING LANGUAGE
# ---------------------------------------------------

def clear_pending_language(
    call_id,
):

    session = language_sessions.get(
        call_id,
        {}
    )

    session["pending"] = None

    language_sessions[call_id] = session

# ---------------------------------------------------
# POSITIVE CONFIRMATION
# ---------------------------------------------------

def is_positive_confirmation(
    text,
):

    text = text.lower()

    return any(
        word in text
        for word in POSITIVE_WORDS
    )

# ---------------------------------------------------
# NEGATIVE CONFIRMATION
# ---------------------------------------------------

def is_negative_confirmation(
    text,
):

    text = text.lower()

    return any(
        word in text
        for word in NEGATIVE_WORDS
    )