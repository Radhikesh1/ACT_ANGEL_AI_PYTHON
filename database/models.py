import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import String, Text, Float, Boolean, DateTime, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.connection import Base


class Assistant(Base):
    __tablename__ = "assistants"
    __table_args__ = {"schema": "pipecat"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    welcome_message: Mapped[str] = mapped_column(
        String(500), default="Hello. How can I help you?"
    )
    default_language: Mapped[str] = mapped_column(String(50), default="english")
    voice: Mapped[str] = mapped_column(String(50), default="priya")
    llm_model: Mapped[str] = mapped_column(String(100), default="gpt-4o-mini")
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    business_hours_start: Mapped[str] = mapped_column(String(10), default="10:30")
    business_hours_end: Mapped[str] = mapped_column(String(10), default="18:30")
    prefetch_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    end_of_call_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    faq_items: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=None)
    intent_triggers: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=None)
    filler_messages: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    status: Mapped[str] = mapped_column(String(20), default="development")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    numbers: Mapped[list["VoiceNumber"]] = relationship(
        "VoiceNumber",
        primaryjoin="Assistant.id == foreign(VoiceNumber.assistant_id)",
        back_populates="assistant",
        lazy="select",
    )



class VoiceNumber(Base):
    """Provider-agnostic phone number. provider field identifies the carrier (plivo, twilio, ...)."""
    __tablename__ = "voice_numbers"
    __table_args__ = {"schema": "pipecat"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="plivo")
    number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    friendly_name: Mapped[str] = mapped_column(String(255), default="")
    country: Mapped[str] = mapped_column(String(100), default="")
    number_type: Mapped[str] = mapped_column(String(50), default="")
    # Soft FK — no DB constraint to avoid cross-schema issues; references pipecat.assistants.id
    assistant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    webhook_configured: Mapped[bool] = mapped_column(Boolean, default=False)

    assistant: Mapped["Assistant | None"] = relationship(
        "Assistant",
        primaryjoin="foreign(VoiceNumber.assistant_id) == Assistant.id",
        back_populates="numbers",
        lazy="select",
    )



class VoiceProviderSetting(Base):
    """Per-org/per-assistant Plivo/Sarvam/OpenAI/Cloudinary credential settings.
    organization_id='' stores global defaults; assistant_id=NULL means the row
    isn't assistant-scoped. Resolution priority: assistant -> org -> global.
    Rows are only ever written by Node (PATCH /api/voice/settings/:provider).
    Note: BYOK billing rates used to live here too (provider='byok_billing')
    but have since moved to versioned tables — see BYOKRateVersion (global)
    and AssistantExtension's byok_*_flat_fee_* columns (per-assistant)."""
    __tablename__ = "voice_provider_settings"
    __table_args__ = {"schema": "pipecat"}

    id: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    assistant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class AssistantExtension(Base):
    """WEB-side per-assistant settings cache (default/public schema, unlike
    the pipecat-schema tables above) — mirrors the active margin version's
    cost fields, including BYOK flat-fee overrides. Read-only from Python's
    perspective; server/routes/admin.ts is the only writer."""
    __tablename__ = "assistant_extensions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    assistant_external_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    byok_stt_flat_fee_per_minute: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    byok_llm_flat_fee_per_minute: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    byok_analytics_flat_fee_per_call: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)


class BYOKRateVersion(Base):
    """WEB-side versioned GLOBAL BYOK flat-fee rates (default/public schema).
    Only the row with is_active=True matters at read time — mirrors how
    ExchangeRateVersion-style versioning works on the WEB side, just for
    BYOK rates instead of currency rates. Read-only from Python's
    perspective; server/routes/admin.ts is the only writer."""
    __tablename__ = "byok_rate_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    stt_flat_fee_per_minute: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    llm_flat_fee_per_minute: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    analytics_flat_fee_per_call: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ModelPricingVersion(Base):
    """WEB-side versioned GLOBAL model pricing (default/public schema) — the
    actual-COST basis (what OpenAI/Sarvam/Plivo charge us), as opposed to
    BYOKRateVersion above which is the CHARGE side (what we bill an org for
    BYOK). `rates` holds the whole pricing table as one JSONB blob:
      {
        "analytics": {"inputPer1M": float, "outputPer1M": float},
        "llm": {"<model-prefix>": {"inputPer1M": float, "outputPer1M": float}, ...},
        "sttPerMinute": float, "phonePerMinute": float, "platformPerMinute": float
      }
    Only the row with is_active=True matters at read time. Read-only from
    Python's perspective; server/routes/admin.ts is the only writer."""
    __tablename__ = "model_pricing_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    rates: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CallLog(Base):
    __tablename__ = "call_logs"
    __table_args__ = {"schema": "pipecat"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Soft FK — no DB constraint; references pipecat.assistants.id
    assistant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    assistant_name: Mapped[str] = mapped_column(String(255), default="")
    from_number: Mapped[str] = mapped_column(String(50), default="")
    to_number: Mapped[str] = mapped_column(String(50), default="")
    duration: Mapped[int] = mapped_column(Integer, default=0)
    chat: Mapped[str | None] = mapped_column(Text, nullable=True)
    call_status: Mapped[str] = mapped_column(String(50), default="user-ended")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    chars_used: Mapped[int] = mapped_column(Integer, default=0)
    recording_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
