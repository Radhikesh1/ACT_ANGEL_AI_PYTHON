import os

from dotenv import load_dotenv
import httpx


# --------------------------------------------------
# ENV
# --------------------------------------------------
load_dotenv()

API_URL: str = os.getenv(
    "APPOINTMENT_API_URL"
) or ""


if not API_URL:
    raise ValueError("APPOINTMENT_API_URL missing")

# API_URL = "YOUR_API_ENDPOINT"


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

        "assistantId": "-Oe14JkgSgLUJXay80DF",

        "from_number": "+918031274555",

        "session_id": session_id,
    }

    async with httpx.AsyncClient() as client:

        response = await client.post(
            API_URL,
            json=payload,
            timeout=20,
        )

        return response.json()