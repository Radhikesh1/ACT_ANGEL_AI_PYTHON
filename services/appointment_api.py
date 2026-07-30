import os

import httpx
from loguru import logger


async def create_appointment_api(
    appointment_type,
    appointment_datetime,
    from_number,
    session_id,
):
    api_url = os.getenv("APPOINTMENT_API_URL") or ""
    assistant_id = os.getenv("ASSISTANT_ID") or ""
    from_number_env = os.getenv("FROM_NUMBER") or ""

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
    }

    async with httpx.AsyncClient() as client:

        response = await client.post(
            api_url,
            json=payload,
            timeout=20,
        )

        response.raise_for_status()

        result = response.json()

        logger.info(f"Appointment API response: status={result.get('status')} id={result.get('id')}")

        return result
