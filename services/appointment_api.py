import os

from dotenv import load_dotenv
import httpx
from loguru import logger


# --------------------------------------------------
# ENV
# --------------------------------------------------
load_dotenv()

API_URL: str = os.getenv("APPOINTMENT_API_URL") or ""
ASSISTANT_ID: str = os.getenv("ASSISTANT_ID") or ""
FROM_NUMBER: str = os.getenv("FROM_NUMBER") or ""

if not API_URL:
    raise ValueError("APPOINTMENT_API_URL missing")

if not ASSISTANT_ID:
    raise ValueError("ASSISTANT_ID missing")

if not FROM_NUMBER:
    raise ValueError("FROM_NUMBER missing")


async def create_appointment_api(
    appointment_type,
    appointment_datetime,
    from_number,
    session_id,
):

    payload = {

        "tool": (
            "site_visit_tool"
            if appointment_type == "site_visit"
            else "create_appointment"
        ),

        "datetime": appointment_datetime,

        "to_number": from_number,

        "assistantId": ASSISTANT_ID,

        "from_number": FROM_NUMBER,

        "session_id": session_id,
    }

    async with httpx.AsyncClient() as client:

        response = await client.post(
            API_URL,
            json=payload,
            timeout=20,
        )

        response.raise_for_status()

        result = response.json()

        logger.info(f"Appointment API response: status={result.get('status')} id={result.get('id')}")

        return result
