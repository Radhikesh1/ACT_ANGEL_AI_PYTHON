"""
Helpers for retrieving per-org Plivo credentials stored in voice_provider_settings.

Credentials are written by Node.js (POST /api/voice/numbers/lookup saves them
when a phone number is imported). Python reads them here to build the serializer
and recording service for each call.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import VoiceProviderSetting


async def get_plivo_creds(db: AsyncSession, org_id: str) -> tuple[str, str]:
    """
    Return (auth_id, auth_token) for *org_id* from voice_provider_settings.

    Priority:
      1. Row with organization_id == org_id  (org-specific)
      2. Row with organization_id == ''      (global default)
      3. ('', '')                            — no credentials configured

    The caller decides how to handle empty strings (log a warning, skip
    recording, skip auto-hangup, etc.).
    """
    orgs_to_check = [org_id, ""] if org_id else [""]
    result = await db.execute(
        select(VoiceProviderSetting).where(
            VoiceProviderSetting.provider == "plivo",
            VoiceProviderSetting.organization_id.in_(orgs_to_check),
        )
    )
    rows = result.scalars().all()

    def _pick(key: str) -> str:
        for row in rows:
            if row.organization_id == org_id and row.key == key and row.value:
                return row.value
        for row in rows:
            if row.organization_id == "" and row.key == key and row.value:
                return row.value
        return ""

    return _pick("auth_id"), _pick("auth_token")
