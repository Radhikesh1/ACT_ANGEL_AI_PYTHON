import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user
from database.connection import get_db
from database.models import CallLog

router = APIRouter()


def _serialize(c: CallLog) -> dict:
    cost_breakdown = None
    if c.cost_breakdown:
        try:
            cost_breakdown = json.loads(c.cost_breakdown)
        except Exception:
            pass
    return {
        "id": str(c.id),
        "session_id": c.session_id,
        "assistant_id": str(c.assistant_id) if c.assistant_id else None,
        "assistant_name": c.assistant_name,
        "from_number": c.from_number,
        "to_number": c.to_number,
        "duration": c.duration,
        "chat": c.chat,
        "call_status": c.call_status,
        "error_message": c.error_message,
        "chars_used": c.chars_used,
        "recording_url": c.recording_url,
        "cost_breakdown": cost_breakdown,
        "total_cost": c.total_cost,
        "started_at": c.started_at.isoformat() if c.started_at else None,
        "ended_at": c.ended_at.isoformat() if c.ended_at else None,
    }


@router.get("/call-logs")
async def list_call_logs(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(CallLog).order_by(desc(CallLog.started_at)).limit(200)
    )
    return [_serialize(c) for c in result.scalars().all()]


@router.get("/call-logs/{log_id}")
async def get_call_log(
    log_id: str,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    c = await db.get(CallLog, uuid.UUID(log_id))
    if not c:
        raise HTTPException(status_code=404, detail="Call log not found")
    return _serialize(c)


class DurationPatch(BaseModel):
    duration: int


@router.patch("/call-logs/{log_id}/duration")
async def patch_call_log_duration(
    log_id: str,
    body: DurationPatch,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    if body.duration <= 0:
        raise HTTPException(status_code=422, detail="duration must be > 0")
    c = await db.get(CallLog, uuid.UUID(log_id))
    if not c:
        raise HTTPException(status_code=404, detail="Call log not found")
    c.duration = body.duration
    await db.commit()
    return {"id": log_id, "duration": body.duration}
