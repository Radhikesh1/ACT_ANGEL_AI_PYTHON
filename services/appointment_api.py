import os

import httpx
from loguru import logger


async def create_appointment_api(
    appointment_type,
    appointment_datetime,
    from_number,
    session_id,
    organization_id=None,
    agent_id=None,
):
    api_url = os.getenv("APPOINTMENT_API_URL") or ""
    # Falls back to the static env assistant id only if the caller didn't
    # have the actual per-call assistant's external id in scope — previously
    # this was the ONLY source, so every appointment across every org/assistant
    # was misattributed to whichever single assistant this env var named.
    assistant_id = agent_id or os.getenv("ASSISTANT_ID") or ""
    from_number_env = os.getenv("FROM_NUMBER") or ""
    ingest_secret = os.getenv("ACTANGEL_INGEST_SECRET") or ""

    if not api_url:
        raise ValueError("APPOINTMENT_API_URL missing — add it to .env")
    if not assistant_id:
        raise ValueError("ASSISTANT_ID missing — add it to .env")
    if not from_number_env:
        raise ValueError("FROM_NUMBER missing — add it to .env")

    payload = {
        "tool": (
            "site_visit_tool"
            if appointment_type == "site_visit"
            else "create_appointment"
        ),
        "datetime": appointment_datetime,
        "to_number": from_number,
        "assistantId": assistant_id,
        "from_number": from_number_env,
        "session_id": session_id,
        # Lets the WEB side scope its assistant lookup to this org instead of
        # matching assistantId (only unique WITHIN an org) globally — without
        # it, two orgs sharing the same external assistant id could have this
        # appointment (and any outcome billing it triggers) attributed to the
        # wrong one. Omitted when unresolved, matching the ingest webhook's
        # own already-established convention (see voice_agent.py).
        "organization_id": organization_id,
    }

    # Same shared-secret convention as the call-session-actangel ingest
    # webhook (voice_agent.py) — this request was previously sent with no
    # auth header at all, which the WEB endpoint's own auth check would
    # reject outright.
    headers: dict = {"Content-Type": "application/json"}
    if ingest_secret:
        headers["X-Internal-Secret"] = ingest_secret

    async with httpx.AsyncClient() as client:

        response = await client.post(
            api_url,
            json=payload,
            headers=headers,
            timeout=20,
        )

        response.raise_for_status()

        result = response.json()

        logger.info(f"Appointment API response: status={result.get('status')} id={result.get('id')}")

        return result
