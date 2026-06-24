import os
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from dotenv import load_dotenv

from api.dependencies import get_current_user
from database.connection import get_db
from database.models import Assistant, PlivoNumber

load_dotenv()

router = APIRouter()

PLIVO_AUTH_ID: str = os.getenv("PLIVO_AUTH_ID") or ""
PLIVO_AUTH_TOKEN: str = os.getenv("PLIVO_AUTH_TOKEN") or ""
DOMAIN: str = os.getenv("DOMAIN") or ""

PLIVO_API = f"https://api.plivo.com/v1/Account/{PLIVO_AUTH_ID}"


# ── Plivo helper ──────────────────────────────────────────────────────────────

async def _fetch_plivo_numbers() -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{PLIVO_API}/Number/",
            auth=(PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("objects", [])


async def _set_plivo_webhook(number: str, webhook_url: str) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{PLIVO_API}/Number/{number}/",
                auth=(PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN),
                json={"answer_url": webhook_url, "answer_method": "GET"},
                timeout=10,
            )
            resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to set Plivo webhook for {number}: {e}")
        return False


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/numbers")
async def list_numbers(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    plivo_numbers = await _fetch_plivo_numbers()

    result = await db.execute(select(PlivoNumber))
    db_map: dict[str, PlivoNumber] = {n.number: n for n in result.scalars().all()}

    # Fetch all assistants for name lookup
    asst_result = await db.execute(select(Assistant))
    asst_map: dict[str, str] = {
        str(a.id): a.name for a in asst_result.scalars().all()
    }

    output = []
    for pn in plivo_numbers:
        number = pn.get("number", "")
        rec = db_map.get(number)
        asst_id = str(rec.assistant_id) if rec and rec.assistant_id else None
        output.append({
            "number": number,
            "friendly_name": pn.get("alias") or pn.get("number", ""),
            "country": pn.get("country", ""),
            "number_type": pn.get("number_type", ""),
            "assistant_id": asst_id,
            "assistant_name": asst_map.get(asst_id) if asst_id else None,
            "webhook_configured": rec.webhook_configured if rec else False,
        })

    return output


@router.post("/numbers/{number}/assign/{assistant_id}")
async def assign_number(
    number: str,
    assistant_id: str,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    a = await db.get(Assistant, uuid.UUID(assistant_id))
    if not a:
        raise HTTPException(status_code=404, detail="Assistant not found")

    result = await db.execute(
        select(PlivoNumber).where(PlivoNumber.number == number)
    )
    rec = result.scalar_one_or_none()

    if not rec:
        rec = PlivoNumber(id=uuid.uuid4(), number=number)
        db.add(rec)

    rec.assistant_id = uuid.UUID(assistant_id)

    webhook_url = f"https://{DOMAIN}/answerCall"
    rec.webhook_configured = await _set_plivo_webhook(number, webhook_url)

    await db.commit()

    return {
        "number": number,
        "assistant_id": assistant_id,
        "assistant_name": a.name,
        "webhook_configured": rec.webhook_configured,
    }


@router.post("/numbers/{number}/unassign")
async def unassign_number(
    number: str,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(PlivoNumber).where(PlivoNumber.number == number)
    )
    rec = result.scalar_one_or_none()

    if rec:
        rec.assistant_id = None
        rec.webhook_configured = False
        await db.commit()

    return {"number": number, "assistant_id": None, "webhook_configured": False}
