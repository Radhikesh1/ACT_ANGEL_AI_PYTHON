import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user
from api.settings import get_plivo_config
from database.connection import get_db
from database.models import Assistant, PlivoNumber

router = APIRouter()


# ── Plivo helpers ─────────────────────────────────────────────────────────────

async def _fetch_plivo_number(auth_id: str, auth_token: str, number: str) -> dict:
    clean = number.lstrip("+")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.plivo.com/v1/Account/{auth_id}/Number/{clean}/",
            auth=(auth_id, auth_token),
            timeout=10,
        )
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Number {number} not found in your Plivo account")
    resp.raise_for_status()
    return resp.json()


async def _set_plivo_webhook(auth_id: str, auth_token: str, number: str, webhook_url: str) -> bool:
    try:
        clean = number.lstrip("+")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.plivo.com/v1/Account/{auth_id}/Number/{clean}/",
                auth=(auth_id, auth_token),
                json={"answer_url": webhook_url, "answer_method": "GET"},
                timeout=10,
            )
            resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to set Plivo webhook for {number}: {e}")
        return False


def _serialize(rec: PlivoNumber, asst_map: dict[str, str]) -> dict:
    asst_id = str(rec.assistant_id) if rec.assistant_id else None
    return {
        "number": rec.number,
        "friendly_name": rec.friendly_name or "",
        "country": rec.country or "",
        "number_type": rec.number_type or "",
        "assistant_id": asst_id,
        "assistant_name": asst_map.get(asst_id) if asst_id else None,
        "webhook_configured": rec.webhook_configured,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/numbers")
async def list_numbers(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    result = await db.execute(select(PlivoNumber))
    numbers = result.scalars().all()

    asst_result = await db.execute(select(Assistant))
    asst_map: dict[str, str] = {str(a.id): a.name for a in asst_result.scalars().all()}

    return [_serialize(n, asst_map) for n in numbers]


class LookupBody(BaseModel):
    number: str


@router.post("/numbers/lookup")
async def lookup_number(
    body: LookupBody,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    cfg = await get_plivo_config(db)
    if not cfg["plivo_auth_id"] or not cfg["plivo_auth_token"]:
        raise HTTPException(status_code=400, detail="Plivo credentials not configured")

    number = body.number.strip()
    if not number.startswith("+"):
        number = "+" + number

    plivo_data = await _fetch_plivo_number(cfg["plivo_auth_id"], cfg["plivo_auth_token"], number)

    result = await db.execute(select(PlivoNumber).where(PlivoNumber.number == number))
    rec = result.scalar_one_or_none()
    if not rec:
        rec = PlivoNumber(id=uuid.uuid4(), number=number)
        db.add(rec)

    rec.friendly_name = plivo_data.get("alias") or plivo_data.get("number", "")
    rec.country = plivo_data.get("country", "")
    rec.number_type = plivo_data.get("number_type", "")

    await db.commit()
    await db.refresh(rec)

    asst_result = await db.execute(select(Assistant))
    asst_map = {str(a.id): a.name for a in asst_result.scalars().all()}
    return _serialize(rec, asst_map)


@router.post("/numbers/{number}/assign/{assistant_id}")
async def assign_number(
    number: str,
    assistant_id: str,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    cfg = await get_plivo_config(db)

    a = await db.get(Assistant, uuid.UUID(assistant_id))
    if not a:
        raise HTTPException(status_code=404, detail="Assistant not found")

    result = await db.execute(select(PlivoNumber).where(PlivoNumber.number == number))
    rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="Number not in database — look it up first")

    rec.assistant_id = uuid.UUID(assistant_id)

    webhook_url = f"https://{cfg['domain']}/answerCall"
    rec.webhook_configured = await _set_plivo_webhook(
        cfg["plivo_auth_id"], cfg["plivo_auth_token"], number, webhook_url
    )

    await db.commit()

    asst_result = await db.execute(select(Assistant))
    asst_map = {str(a.id): a.name for a in asst_result.scalars().all()}
    return _serialize(rec, asst_map)


@router.post("/numbers/{number}/unassign")
async def unassign_number(
    number: str,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    result = await db.execute(select(PlivoNumber).where(PlivoNumber.number == number))
    rec = result.scalar_one_or_none()
    if rec:
        rec.assistant_id = None
        rec.webhook_configured = False
        await db.commit()
    return {"number": number, "assistant_id": None, "webhook_configured": False}


@router.delete("/numbers/{number}")
async def delete_number(
    number: str,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    result = await db.execute(select(PlivoNumber).where(PlivoNumber.number == number))
    rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="Number not found")
    await db.delete(rec)
    await db.commit()
    return {"deleted": number}
