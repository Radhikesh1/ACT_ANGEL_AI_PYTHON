import uuid

from loguru import logger

from database.connection import AsyncSessionLocal
from database.models import MessageLog


async def log_message(
    *,
    channel: str,
    organization_id: str | None,
    assistant_id: uuid.UUID | None,
    contact_id: str,
    channel_number: str,
    external_message_id: str | None,
    direction: str,
    message_type: str,
    content: str | None,
    status: str = "received",
    error_message: str | None = None,
) -> None:
    try:
        async with AsyncSessionLocal() as db:
            db.add(MessageLog(
                organization_id=organization_id,
                assistant_id=assistant_id,
                channel=channel,
                contact_id=contact_id,
                channel_number=channel_number,
                external_message_id=external_message_id,
                direction=direction,
                message_type=message_type,
                content=(content or "")[:20000],
                status=status,
                error_message=error_message,
            ))
            await db.commit()
    except Exception as e:
        # external_message_id has a unique constraint — a redelivered webhook
        # will violate it here, which is the intended dedupe signal, not a bug.
        logger.warning(f"[{channel}] Failed to log message ({message_type}, contact={contact_id}): {e}")
