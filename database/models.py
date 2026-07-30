import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, Float, Boolean, DateTime, Integer, PrimaryKeyConstraint
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
    """Per-org, per-provider key-value settings. organization_id='' for global defaults."""
    __tablename__ = "voice_provider_settings"
    __table_args__ = (
        PrimaryKeyConstraint("organization_id", "provider", "key"),
        {"schema": "pipecat"},
    )

    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


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
