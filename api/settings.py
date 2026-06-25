import os
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from dotenv import load_dotenv

from api.dependencies import get_current_user
from database.connection import get_db
from database.models import AppSetting

load_dotenv()

router = APIRouter()

# Keys we expose — order determines form order in the UI.
PLIVO_KEYS = [
    "plivo_auth_id",
    "plivo_auth_token",
    "plivo_phone_number",
    "domain",
]

# Env-var fallbacks for each key.
_ENV_FALLBACK: dict[str, str] = {
    "plivo_auth_id":      os.getenv("PLIVO_AUTH_ID", ""),
    "plivo_auth_token":   os.getenv("PLIVO_AUTH_TOKEN", ""),
    "plivo_phone_number": os.getenv("PLIVO_PHONE_NUMBER", ""),
    "domain":             os.getenv("DOMAIN", ""),
}


async def get_setting(db: AsyncSession, key: str) -> str:
    """Return DB value if set, otherwise env fallback."""
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    rec = result.scalar_one_or_none()
    if rec and rec.value:
        return rec.value
    return _ENV_FALLBACK.get(key, "")


async def get_plivo_config(db: AsyncSession) -> dict[str, str]:
    """Return the active Plivo credentials (DB overrides env)."""
    return {k: await get_setting(db, k) for k in PLIVO_KEYS}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/settings/plivo")
async def get_plivo_settings(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """Return current Plivo settings (DB overrides env). Auth token is masked."""
    cfg = await get_plivo_config(db)
    token = cfg.get("plivo_auth_token", "")
    return {
        "plivo_auth_id":      cfg["plivo_auth_id"],
        "plivo_auth_token":   ("*" * 8 + token[-4:]) if len(token) > 4 else ("*" * len(token)),
        "plivo_phone_number": cfg["plivo_phone_number"],
        "domain":             cfg["domain"],
        "source": {k: "db" if await _from_db(db, k) else "env" for k in PLIVO_KEYS},
    }


async def _from_db(db: AsyncSession, key: str) -> bool:
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    rec = result.scalar_one_or_none()
    return bool(rec and rec.value)


class PlivoSettingsBody(BaseModel):
    plivo_auth_id:      str | None = None
    plivo_auth_token:   str | None = None
    plivo_phone_number: str | None = None
    domain:             str | None = None


@router.patch("/settings/plivo")
async def update_plivo_settings(
    body: PlivoSettingsBody,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """Upsert Plivo settings into the database. Omit a field to leave it unchanged.
    Pass an empty string to clear a key (falls back to env var)."""
    updates: dict[str, Any] = body.model_dump(exclude_none=True)

    for key, value in updates.items():
        if key not in PLIVO_KEYS:
            continue
        result = await db.execute(select(AppSetting).where(AppSetting.key == key))
        rec = result.scalar_one_or_none()
        if rec:
            rec.value = value or None
        else:
            db.add(AppSetting(key=key, value=value or None))

    await db.commit()
    return {"saved": list(updates.keys())}
