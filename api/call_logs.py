import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user
from database.connection import get_db
from database.models import CallLog

router = APIRouter()


def _serialize(c: CallLog) -> dict:
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
