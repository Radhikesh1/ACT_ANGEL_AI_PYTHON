"""
Generic single-field credential lookup in voice_provider_settings, for
providers with one simple value per key (Sarvam, OpenAI, Cloudinary) — as
opposed to Plivo's auth_id+auth_token shape handled by utils/plivo_creds.py.

Credentials are written by Node.js (PATCH /api/voice/settings/:provider).
Python reads them here at call-setup time.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import VoiceProviderSetting


async def get_org_provider_key(
    db: AsyncSession,
    org_id: str,
    provider: str,
    key: str = "api_key",
    assistant_id: str | None = None,
) -> str:
    """
    Return the value for (provider, key) scoped to *org_id* (and, when
    passed, *assistant_id*).

    Priority:
      1. Row with assistant_id == assistant_id (assistant-specific override,
                                                 only checked when passed)
      2. Row with organization_id == org_id    (org-specific override)
      3. Row with organization_id == ''        (global default)
      4. ""                                    — nothing configured; caller
                                                  falls back to its own env var.

    Existing callers that never pass assistant_id keep today's 2-tier
    behavior (org -> global) byte-for-byte.
    """
    orgs_to_check = [org_id, ""] if org_id else [""]
    result = await db.execute(
        select(VoiceProviderSetting).where(
            VoiceProviderSetting.provider == provider,
            VoiceProviderSetting.key == key,
            VoiceProviderSetting.organization_id.in_(orgs_to_check),
        )
    )
    rows = result.scalars().all()

    if assistant_id:
        for row in rows:
            if str(row.assistant_id) == assistant_id and row.value:
                return row.value
    for row in rows:
        if row.organization_id == org_id and not row.assistant_id and row.value:
            return row.value
    for row in rows:
        if row.organization_id == "" and not row.assistant_id and row.value:
            return row.value
    return ""
