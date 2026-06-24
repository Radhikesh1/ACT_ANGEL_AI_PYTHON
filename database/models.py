import uuid
from datetime import datetime

from sqlalchemy import String, Text, Float, Boolean, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.connection import Base


class Assistant(Base):
    __tablename__ = "assistants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    welcome_message: Mapped[str] = mapped_column(
        String(500), default="Hello. I am Ciya. How can I help you?"
    )
    default_language: Mapped[str] = mapped_column(String(50), default="english")
    voice: Mapped[str] = mapped_column(String(50), default="priya")
    llm_model: Mapped[str] = mapped_column(String(100), default="gpt-4o-mini")
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    business_hours_start: Mapped[str] = mapped_column(String(10), default="10:30")
    business_hours_end: Mapped[str] = mapped_column(String(10), default="18:30")
    status: Mapped[str] = mapped_column(String(20), default="development")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    numbers: Mapped[list["PlivoNumber"]] = relationship(
        "PlivoNumber", back_populates="assistant", lazy="select"
    )


class PlivoNumber(Base):
    __tablename__ = "plivo_numbers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    friendly_name: Mapped[str] = mapped_column(String(255), default="")
    assistant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistants.id", ondelete="SET NULL"), nullable=True
    )
    webhook_configured: Mapped[bool] = mapped_column(Boolean, default=False)

    assistant: Mapped["Assistant | None"] = relationship(
        "Assistant", back_populates="numbers"
    )
