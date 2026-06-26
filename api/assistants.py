import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_org_id
from database.connection import get_db
from database.models import Assistant

router = APIRouter()

DEVELOPMENT = "development"
PRODUCTION = "production"


# ── Schemas ───────────────────────────────────────────────────────────────────

class AssistantIn(BaseModel):
    name: str
    system_prompt: str
    welcome_message: str = "Hello. I am Ciya. How can I help you?"
    default_language: str = "english"
    voice: str = "priya"
    llm_model: str = "gpt-4o-mini"
    temperature: float = 0.2
    business_hours_start: str = "10:30"
    business_hours_end: str = "18:30"
    prefetch_webhook_url: str | None = None
    end_of_call_webhook_url: str | None = None


class AssistantUpdate(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    welcome_message: str | None = None
    default_language: str | None = None
    voice: str | None = None
    llm_model: str | None = None
    temperature: float | None = None
    business_hours_start: str | None = None
    business_hours_end: str | None = None
    prefetch_webhook_url: str | None = None
    end_of_call_webhook_url: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize(a: Assistant) -> dict:
    return {
        "id": str(a.id),
        "organization_id": a.organization_id,
        "name": a.name,
        "system_prompt": a.system_prompt,
        "welcome_message": a.welcome_message,
        "default_language": a.default_language,
        "voice": a.voice,
        "llm_model": a.llm_model,
        "temperature": a.temperature,
        "business_hours_start": a.business_hours_start,
        "business_hours_end": a.business_hours_end,
        "prefetch_webhook_url": a.prefetch_webhook_url,
        "end_of_call_webhook_url": a.end_of_call_webhook_url,
        "status": a.status,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


async def _get_or_404(db: AsyncSession, aid: str, org_id: str | None = None) -> Assistant:
    a = await db.get(Assistant, uuid.UUID(aid))
    if not a:
        raise HTTPException(status_code=404, detail="Assistant not found")
    if org_id and a.organization_id and a.organization_id != org_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return a


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/assistants")
async def list_assistants(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    org_id = get_org_id(request)
    query = select(Assistant).order_by(Assistant.created_at.desc())
    if org_id:
        query = query.where(Assistant.organization_id == org_id)
    result = await db.execute(query)
    return [_serialize(a) for a in result.scalars().all()]


@router.post("/assistants", status_code=201)
async def create_assistant(
    request: Request,
    body: AssistantIn,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    org_id = get_org_id(request)
    now = datetime.utcnow()
    a = Assistant(
        id=uuid.uuid4(),
        organization_id=org_id,
        status=DEVELOPMENT,
        created_at=now,
        updated_at=now,
        **body.model_dump(),
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    logger.info(f"[Assistant] Created: id={a.id} name='{a.name}' org={org_id}")
    return _serialize(a)


@router.get("/assistants/{aid}")
async def get_assistant(
    aid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    org_id = get_org_id(request)
    return _serialize(await _get_or_404(db, aid, org_id))


@router.put("/assistants/{aid}")
async def update_assistant(
    aid: str,
    request: Request,
    body: AssistantUpdate,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    org_id = get_org_id(request)
    a = await _get_or_404(db, aid, org_id)

    if a.status == PRODUCTION:
        raise HTTPException(
            status_code=409,
            detail="Assistant is in production. Move it to development before making changes.",
        )

    for key, value in body.model_dump(exclude_none=True).items():
        setattr(a, key, value)

    a.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(a)
    logger.info(f"[Assistant] Updated: id={a.id} name='{a.name}'")
    return _serialize(a)


@router.delete("/assistants/{aid}", status_code=204)
async def delete_assistant(
    aid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    org_id = get_org_id(request)
    a = await _get_or_404(db, aid, org_id)

    if a.status == PRODUCTION:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a production assistant. Move it to development first.",
        )

    logger.info(f"[Assistant] Deleted: id={a.id} name='{a.name}'")
    await db.delete(a)
    await db.commit()


@router.post("/assistants/{aid}/publish")
async def publish_assistant(
    aid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    org_id = get_org_id(request)
    a = await _get_or_404(db, aid, org_id)
    a.status = PRODUCTION
    a.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(a)
    logger.info(f"[Assistant] Published to PRODUCTION: id={a.id} name='{a.name}'")
    return _serialize(a)


@router.post("/assistants/{aid}/unpublish")
async def unpublish_assistant(
    aid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    org_id = get_org_id(request)
    a = await _get_or_404(db, aid, org_id)
    a.status = DEVELOPMENT
    a.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(a)
    logger.info(f"[Assistant] Moved to DEVELOPMENT: id={a.id} name='{a.name}'")
    return _serialize(a)
