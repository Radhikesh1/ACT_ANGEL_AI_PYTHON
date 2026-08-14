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
    assistant_number=None,
):
    api_url = os.getenv("APPOINTMENT_API_URL") or ""
    # Falls back to the static env assistant id only if the caller didn't
    # have the actual per-call assistant's external id in scope — previously
    # this was the ONLY source, so every appointment across every org/assistant
    # was misattributed to whichever single assistant this env var named.
    assistant_id = agent_id or os.getenv("ASSISTANT_ID") or ""
    # Same fallback pattern — prefer the real per-call number (the number
    # actually dialed for this call) over the static env var, which named
    # one single number for every org/assistant regardless of which one
    # actually took the call.
    resolved_assistant_number = assistant_number or os.getenv("FROM_NUMBER") or ""
    ingest_secret = os.getenv("ACTANGEL_INGEST_SECRET") or ""

    if not api_url:
        raise ValueError("APPOINTMENT_API_URL missing — add it to .env")
    if not assistant_id:
        raise ValueError("ASSISTANT_ID missing — add it to .env")
    if not resolved_assistant_number:
        raise ValueError("FROM_NUMBER missing — add it to .env")

    # WEB's /api/webhook/call-appointment (ingestAppointment) requires these
    # exact field names — selectedOption is "siteVisit" for a site visit,
    # "callback" otherwise (appointment_processor.py only ever classifies
    # these two), and scheduleDateTime/customerNumber/assistantNumber replace
    # this payload's older datetime/to_number/(missing) shape, which never
    # matched what that endpoint actually reads.
    payload = {
        "customerNumber": from_number,
        "assistantNumber": resolved_assistant_number,
        "selectedOption": "siteVisit" if appointment_type == "site_visit" else "callback",
        "scheduleDateTime": appointment_datetime,
        "assistantId": assistant_id,
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
