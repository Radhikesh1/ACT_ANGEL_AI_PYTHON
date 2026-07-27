import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_org_id
from database.connection import get_db
from database.models import Assistant
from services.config_generator import generate_assistant_config

router = APIRouter()

DEVELOPMENT = "development"
PRODUCTION = "production"


# ── Schemas ───────────────────────────────────────────────────────────────────

class FaqItem(BaseModel):
    question: str
    answer: str


class IntentTrigger(BaseModel):
    name: str
    keywords: list[str]
    action: str


class FillerMessages(BaseModel):
    en: str = ""
    hi: str = ""
    bn: str = ""


class AssistantIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    system_prompt: str = Field(..., min_length=1)
    welcome_message: str = "Hello. How can I help you?"
    default_language: str = "english"
    voice: str = "priya"
    llm_model: str = "gpt-4o-mini"
    temperature: float = Field(0.2, ge=0.0, le=2.0)
    business_hours_start: str = "10:30"
    business_hours_end: str = "18:30"
    prefetch_webhook_url: Optional[HttpUrl] = None
    end_of_call_webhook_url: Optional[HttpUrl] = None
    faq_items: Optional[list[FaqItem]] = None
    intent_triggers: Optional[list[IntentTrigger]] = None
    filler_messages: Optional[FillerMessages] = None


class AssistantUpdate(BaseModel):
    name: str | None = None
    system_prompt: str | None = Field(None, min_length=1)
    welcome_message: str | None = None
    default_language: str | None = None
    voice: str | None = None
    llm_model: str | None = None
    temperature: float | None = None
    business_hours_start: str | None = None
    business_hours_end: str | None = None
    prefetch_webhook_url: str | None = None
    end_of_call_webhook_url: str | None = None
    faq_items: Optional[list[FaqItem]] = None
    intent_triggers: Optional[list[IntentTrigger]] = None
    filler_messages: Optional[FillerMessages] = None


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
        "faq_items": a.faq_items or [],
        "intent_triggers": a.intent_triggers or [],
        "filler_messages": a.filler_messages or {},
        "status": a.status,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


async def _get_or_404(db: AsyncSession, aid: uuid.UUID, org_id: str | None = None) -> Assistant:
    a = await db.get(Assistant, aid)
    if not a:
        raise HTTPException(status_code=404, detail="Not found")
    if not org_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if a.organization_id != org_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return a


def _pydantic_list(items: Any) -> list | None:
    """Convert a list of Pydantic models to plain dicts, or pass through if already dicts."""
    if items is None:
        return None
    return [i.model_dump() if hasattr(i, "model_dump") else i for i in items]


def _pydantic_dict(obj: Any) -> dict | None:
    if obj is None:
        return None
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/assistants")
async def list_assistants(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    org_id = get_org_id(request)
    if not org_id:
        raise HTTPException(status_code=400, detail="X-Org-Id header required")
    query = (
        select(Assistant)
        .where(Assistant.organization_id == org_id)
        .order_by(Assistant.created_at.desc())
    )
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
    now = datetime.now(timezone.utc)
    body_data = body.model_dump(mode="json", exclude={"faq_items", "intent_triggers", "filler_messages"})
    a = Assistant(
        id=uuid.uuid4(),
        organization_id=org_id,
        status=DEVELOPMENT,
        created_at=now,
        updated_at=now,
        faq_items=_pydantic_list(body.faq_items),
        intent_triggers=_pydantic_list(body.intent_triggers),
        filler_messages=_pydantic_dict(body.filler_messages),
        **body_data,
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    logger.info(f"[Assistant] Created: id={a.id} name='{a.name}' org={org_id}")
    return _serialize(a)


@router.get("/assistants/{aid}")
async def get_assistant(
    aid: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    org_id = get_org_id(request)
    return _serialize(await _get_or_404(db, aid, org_id))


@router.put("/assistants/{aid}")
async def update_assistant(
    aid: uuid.UUID,
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

    updates = body.model_dump(exclude_none=True, exclude={"faq_items", "intent_triggers", "filler_messages"})
    for key, value in updates.items():
        setattr(a, key, value)

    if body.faq_items is not None:
        a.faq_items = _pydantic_list(body.faq_items)
    if body.intent_triggers is not None:
        a.intent_triggers = _pydantic_list(body.intent_triggers)
    if body.filler_messages is not None:
        a.filler_messages = _pydantic_dict(body.filler_messages)

    a.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(a)
    logger.info(f"[Assistant] Updated: id={a.id} name='{a.name}'")
    return _serialize(a)


@router.delete("/assistants/{aid}", status_code=204)
async def delete_assistant(
    aid: uuid.UUID,
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


@router.post("/assistants/{aid}/generate-config")
async def generate_config(
    aid: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """Regenerate FAQ, intent triggers, and filler messages from the system prompt."""
    org_id = get_org_id(request)
    a = await _get_or_404(db, aid, org_id)

    generated = await generate_assistant_config(a.system_prompt)
    if not generated:
        raise HTTPException(status_code=502, detail="Config generation failed — check OPENAI_API_KEY")

    if generated.get("faq_items"):
        a.faq_items = generated["faq_items"]
    if generated.get("intent_triggers"):
        a.intent_triggers = generated["intent_triggers"]
    if generated.get("filler_messages"):
        a.filler_messages = generated["filler_messages"]

    a.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(a)
    logger.info(f"[Assistant] Config generated: id={a.id}")
    return _serialize(a)


@router.post("/assistants/{aid}/publish")
async def publish_assistant(
    aid: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    org_id = get_org_id(request)
    a = await _get_or_404(db, aid, org_id)

    # Auto-generate config if not yet configured
    if not a.faq_items and not a.intent_triggers and not a.filler_messages:
        logger.info(f"[Assistant] Auto-generating config for publish: id={a.id}")
        generated = await generate_assistant_config(a.system_prompt)
        if generated:
            a.faq_items = generated.get("faq_items") or a.faq_items
            a.intent_triggers = generated.get("intent_triggers") or a.intent_triggers
            a.filler_messages = generated.get("filler_messages") or a.filler_messages

    a.status = PRODUCTION
    a.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(a)
    logger.info(f"[Assistant] Published to PRODUCTION: id={a.id} name='{a.name}'")
    return _serialize(a)


@router.post("/assistants/{aid}/unpublish")
async def unpublish_assistant(
    aid: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    org_id = get_org_id(request)
    a = await _get_or_404(db, aid, org_id)
    a.status = DEVELOPMENT
    a.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(a)
    logger.info(f"[Assistant] Moved to DEVELOPMENT: id={a.id} name='{a.name}'")
    return _serialize(a)
