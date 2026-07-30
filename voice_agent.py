import asyncio
import os
import time
import json
import uuid
from datetime import datetime
from typing import Any, cast

import httpx
from dotenv import load_dotenv
from loguru import logger

from pipecat.pipeline.pipeline import Pipeline
from pipecat.workers.runner import WorkerRunner
from pipecat.pipeline.worker import PipelineWorker, PipelineParams
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketTransport,
    FastAPIWebsocketParams,
)
from pipecat.serializers.plivo import PlivoFrameSerializer
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.processors.aggregators.llm_context import LLMContext, LLMContextMessage
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.frames.frames import TTSSpeakFrame

from processors.language_processor import LanguageProcessor
from processors.filler_processor import FillerProcessor
from processors.appointment_processor import AppointmentProcessor
from processors.intent_router import IntentRouterProcessor
from processors.noise_gate_processor import NoiseFilterProcessor

from utils.session_state import call_sessions
from utils.tts_factory import create_tts
from utils import call_log_manager
from database.connection import AsyncSessionLocal
from database.models import CallLog, Assistant, VoiceNumber
from services.recording_service import start_recording, fetch_and_upload
from services.cost_service import calculate_cost, cost_to_json

load_dotenv()


def mask_phone(phone: str) -> str:
    """Mask phone number for logging, keeping only last 4 digits."""
    if not phone:
        return ""
    digits = ''.join(c for c in phone if c.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 4) + digits[-4:]


OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY") or ""
SARVAM_API_KEY: str = os.getenv("SARVAM_API_KEY") or ""

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY missing")

if not SARVAM_API_KEY:
    raise ValueError("SARVAM_API_KEY missing")


async def _call_prefetch_webhook(url: str, session_id: str, agent_id: str, from_number: str, to_number: str) -> dict:
    """GET the prefetch webhook and return any extra context data."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url, params={
                "session_id": session_id,
                "agent_id": agent_id,
                "from": from_number,
                "to": to_number,
            })
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
            logger.info(f"[Prefetch] {url} → {resp.status_code} | data keys: {list(data.keys())}")
            return data
    except Exception as e:
        logger.warning(f"[Prefetch] Webhook call failed ({url}): {e}")
        return {}


async def _call_end_of_call_webhook(
    url: str,
    session_id: str,
    call_id: str,
    agent_id: str,
    from_number: str,
    to_number: str,
    messages: list,
    start_ts: float,
    assistant_config: dict,
    call_status: str = "user-ended",
    error_message: str | None = None,
    metadata: dict | None = None,
):
    """POST call summary to the end-of-call webhook."""
    duration = int(time.time() - start_ts)

    # Build chat history — skip the system message
    chat = json.dumps([
        {"role": m["role"], "content": m.get("content") or ""}
        for m in messages
        if m.get("role") != "system"
    ])

    payload = {
        "session_id": session_id,
        "call_id": call_id,
        "agent_id": agent_id,
        "ts": start_ts,
        "duration": duration,
        "chat": chat,
        "function_calls": [],
        "agent_config": {
            k: v for k, v in assistant_config.items()
            if k not in ("prefetch_webhook_url", "end_of_call_webhook_url")
        },
        "voip": {
            "provider": "plivo",
            "from": from_number,
            "to": to_number,
        },
        "recording": {"recording_url": None},
        "metadata": metadata or {},
        "call_status": call_status,
        "error_message": error_message,
        "cost_breakdown": [],
        "chars_used": sum(len(m.get("content") or "") for m in messages),
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            logger.info(f"[EndOfCall] {url} → {resp.status_code}")
    except Exception as e:
        logger.warning(f"[EndOfCall] Webhook call failed ({url}): {e}")


async def _finalize_call(
    log_id: uuid.UUID | None,
    call_id: str,
    agent_id: str,
    organization_id: str | None,
    assistant_config: dict,
    customer_number: str,
    to_number: str,
    duration: int,
    chat_messages: list,
    chars_used: int,
    call_status: str,
    error_message: str | None,
    start_ts: float,
    started_at: datetime,
    ended_at: datetime,
    end_of_call_url: str | None,
    llm_model: str,
    metadata: dict | None = None,
):
    """Detached task: update (or insert) call log, fire webhooks, then save recording URL.
    Runs independently of the websocket handler so CancelledError cannot abort it."""

    # Resolve missing organization_id — try assistant record first, then VoiceNumber.
    if not organization_id and agent_id:
        try:
            async with AsyncSessionLocal() as db:
                asst = await db.get(Assistant, uuid.UUID(agent_id))
                if asst and asst.organization_id:
                    organization_id = asst.organization_id
                    logger.info(f"[ActAngel Ingest] Resolved organization_id={organization_id} from assistant")
        except Exception as e:
            logger.warning(f"[ActAngel Ingest] Could not resolve organization_id from assistant: {e}")

    if not organization_id and to_number:
        try:
            to_variants = [to_number, "+" + to_number.lstrip("+")]
            async with AsyncSessionLocal() as db:
                from sqlalchemy import select as _select
                result = await db.execute(
                    _select(VoiceNumber).where(VoiceNumber.number.in_(to_variants))
                )
                vn = result.scalar_one_or_none()
                if vn and vn.organization_id:
                    organization_id = vn.organization_id
                    logger.info(f"[ActAngel Ingest] Resolved organization_id={organization_id} from VoiceNumber {mask_phone(to_number)}")
        except Exception as e:
            logger.warning(f"[ActAngel Ingest] Could not resolve organization_id from VoiceNumber: {e}")

    # 1. Update the in-progress log row that was created at call start.
    #    Fall back to INSERT if the initial creation failed (log_id is None).
    try:
        async with AsyncSessionLocal() as db:
            if log_id is not None:
                log = await db.get(CallLog, log_id)
                if log:
                    cost = calculate_cost(duration, chat_messages, llm_model)
                    log.duration = duration
                    log.chat = json.dumps(chat_messages)
                    log.call_status = call_status
                    log.error_message = error_message
                    log.chars_used = chars_used
                    log.cost_breakdown = cost_to_json(cost)
                    log.total_cost = cost["total_usd"]
                    log.ended_at = ended_at
                    if organization_id and not log.organization_id:
                        log.organization_id = organization_id
                    await db.commit()
                    logger.info(
                        f"[CallLog] Updated {log_id} "
                        f"(duration={duration}s, status={call_status}, cost=${cost['total_usd']:.4f})"
                    )
                else:
                    log_id = None  # row disappeared — fall through to INSERT
            if log_id is None:
                cost = calculate_cost(duration, chat_messages, llm_model)
                log_id = uuid.uuid4()
                db.add(CallLog(
                    id=log_id,
                    organization_id=organization_id,
                    session_id=call_id,
                    assistant_id=uuid.UUID(agent_id) if agent_id else None,
                    assistant_name=assistant_config.get("name") or "",
                    from_number=customer_number,
                    to_number=to_number,
                    duration=duration,
                    chat=json.dumps(chat_messages),
                    call_status=call_status,
                    error_message=error_message,
                    chars_used=chars_used,
                    recording_url=None,
                    cost_breakdown=cost_to_json(cost),
                    total_cost=cost["total_usd"],
                    started_at=started_at,
                    ended_at=ended_at,
                ))
                await db.commit()
                logger.info(f"[CallLog] Inserted {log_id} (fallback, duration={duration}s, cost=${cost['total_usd']:.4f})")
    except Exception as db_err:
        logger.error(f"[CallLog] Failed to finalize call log: {db_err}")
        log_id = None

    # 2. Fire end-of-call webhook
    if end_of_call_url:
        try:
            await _call_end_of_call_webhook(
                url=end_of_call_url,
                session_id=call_id,
                call_id=call_id,
                agent_id=agent_id,
                from_number=customer_number,
                to_number=to_number,
                messages=[{"role": "system", "content": ""}, *chat_messages],
                start_ts=start_ts,
                assistant_config=assistant_config,
                call_status=call_status,
                error_message=error_message,
                metadata=metadata,
            )
        except Exception as wh_err:
            logger.warning(f"[EndOfCall] Webhook error: {wh_err}")

    # 3. Fetch recording from Plivo → upload to Cloudinary → patch DB row
    recording_url: str | None = None
    final_duration: int = duration
    if log_id:
        try:
            recording_url, recording_duration = await fetch_and_upload(call_id)
            if recording_url:
                async with AsyncSessionLocal() as db:
                    saved = await db.get(CallLog, log_id)
                    if saved:
                        saved.recording_url = recording_url
                        if recording_duration and recording_duration > 0:
                            saved.duration = recording_duration
                            final_duration = recording_duration
                            logger.info(
                                f"[CallLog] Duration corrected to {recording_duration}s "
                                f"(from Plivo recording) for {log_id}"
                            )
                        await db.commit()
                        logger.info(f"[CallLog] Recording URL saved for {log_id}")
        except Exception as rec_err:
            logger.error(f"[CallLog] Recording update failed for {log_id}: {rec_err}")

    # 4. Fire actAngel ingest webhook → WEB Node stores call in its DB
    ingest_base = os.getenv("ACTANGEL_INGEST_URL", "").rstrip("/")
    ingest_url = f"{ingest_base}/api/webhook/call-session-actangel" if ingest_base else None
    ingest_secret = os.getenv("ACTANGEL_INGEST_SECRET")
    if ingest_url and not organization_id:
        logger.warning("[ActAngel Ingest] Skipping — organization_id could not be resolved")
    if ingest_url and organization_id:
        try:
            cost_obj: dict = json.loads(cost_to_json(calculate_cost(final_duration, chat_messages, llm_model)))
            raw_cost = {
                "llm": cost_obj.get("llm_usd", 0),
                "stt": cost_obj.get("stt_usd", 0),
                "phone": cost_obj.get("phone_usd", 0),
                "platform": cost_obj.get("platform_usd", 0),
            }
            payload = {
                "agent_id": str(agent_id),
                "assistant_name": assistant_config.get("name") or "",
                "session_id": call_id,
                "organization_id": organization_id,
                "ts": start_ts,
                "duration": final_duration,
                "chat": json.dumps(chat_messages),
                "voip": {
                    "from": customer_number,
                    "to": to_number,
                    "direction": "inbound",
                },
                "recording": {"recording_url": recording_url},
                "cost_breakdown": raw_cost,
                "call_status": call_status,
                "error_message": error_message,
                "metadata": metadata or {},
            }
            headers: dict = {"Content-Type": "application/json"}
            if ingest_secret:
                headers["X-Internal-Secret"] = ingest_secret
            logger.info(f"[ActAngel Ingest] Sending → agent_id={agent_id} org={organization_id} session={call_id}")
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(ingest_url, json=payload, headers=headers)
                logger.info(f"[ActAngel Ingest] {ingest_url} → {resp.status_code}")
                if resp.status_code not in (200, 201):
                    logger.warning(f"[ActAngel Ingest] Unexpected response: {resp.text[:500]}")
        except Exception as ingest_err:
            logger.warning(f"[ActAngel Ingest] Webhook failed: {ingest_err}")


async def run_bot(websocket_client):

    # -----------------------------------
    # Parse websocket
    # -----------------------------------

    transport_type, call_data = await parse_telephony_websocket(websocket_client)

    stream_id = call_data["stream_id"]
    call_id = call_data["call_id"]

    session: dict = call_sessions.get(call_id) or {}
    customer_number: str = session.get("from_number", "")
    to_number: str = session.get("to_number", "")
    assistant_config: dict = session.get("assistant_config") or {}
    organization_id: str | None = session.get("organization_id")

    system_prompt: str | None = assistant_config.get("system_prompt")
    if not system_prompt:
        logger.error(f"[{call_id}] No system_prompt configured for this assistant — call aborted")
        return
    welcome_message: str = (
        assistant_config.get("welcome_message")
        or "Hello. How can I help you?"
    )
    llm_model: str = assistant_config.get("llm_model") or "gpt-4o-mini"
    temperature: float = float(assistant_config.get("temperature") or 0.2)
    default_language: str = assistant_config.get("default_language") or "english"
    configured_voice: str | None = assistant_config.get("voice") or None
    agent_id: str = assistant_config.get("id") or ""
    prefetch_url: str | None = assistant_config.get("prefetch_webhook_url") or None
    end_of_call_url: str | None = assistant_config.get("end_of_call_webhook_url") or None
    faq_items: list = assistant_config.get("faq_items") or []
    intent_triggers_config: list | None = assistant_config.get("intent_triggers")
    filler_messages_config: dict = assistant_config.get("filler_messages") or {}

    # Inject FAQ pairs into system prompt so the LLM handles them naturally
    if faq_items:
        faq_block = "\n\n--- Frequently Asked Questions ---\n" + "\n".join(
            f"Q: {item.get('question', '')}\nA: {item.get('answer', '')}"
            for item in faq_items
            if item.get("question") and item.get("answer")
        )
        system_prompt = system_prompt + faq_block

    # Start per-call log file: logs/calls/{call_id}.log
    clog = call_log_manager.start(call_id)

    clog.info(
        f"Call connected | call_id={call_id} | "
        f"from={mask_phone(customer_number)} | to={mask_phone(to_number)} | "
        f"assistant={assistant_config.get('name', 'default')} | "
        f"model={llm_model}"
    )

    start_ts = time.time()
    started_at = datetime.now()

    # -----------------------------------
    # Create initial call log (in-progress)
    # -----------------------------------

    log_id: uuid.UUID | None = None
    try:
        async with AsyncSessionLocal() as db:
            log_id = uuid.uuid4()
            db.add(CallLog(
                id=log_id,
                organization_id=organization_id,
                session_id=call_id,
                assistant_id=uuid.UUID(agent_id) if agent_id else None,
                assistant_name=assistant_config.get("name") or "",
                from_number=customer_number,
                to_number=to_number,
                duration=0,
                chat=None,
                call_status="in-progress",
                error_message=None,
                chars_used=0,
                recording_url=None,
                started_at=started_at,
                ended_at=started_at,
            ))
            await db.commit()
            clog.info(f"[DB] Created in-progress log {log_id}")
    except Exception as e:
        clog.error(f"[DB] Failed to create initial log: {e}")
        log_id = None

    # -----------------------------------
    # Start call recording (optional)
    # -----------------------------------

    await start_recording(call_id)
    clog.info("[Recording] Recording started")

    # -----------------------------------
    # Prefetch webhook (optional)
    # -----------------------------------

    customer_metadata: dict = {}
    if prefetch_url:
        prefetch_data = await _call_prefetch_webhook(
            url=prefetch_url,
            session_id=call_id,
            agent_id=agent_id,
            from_number=customer_number,
            to_number=to_number,
        )
        # Merge any extra_context returned by the webhook into the system prompt
        extra_context = prefetch_data.get("context") or prefetch_data.get("extra_context") or ""
        if extra_context:
            system_prompt = f"{system_prompt}\n\n--- Customer Context ---\n{extra_context}"

        # Extract customer identity fields for Node's ingest (contact linking)
        _name_keys  = ("name", "customer_name", "full_name")
        _email_keys = ("email", "customer_email")
        for k in _name_keys:
            if prefetch_data.get(k):
                customer_metadata["name"] = prefetch_data[k]
                break
        for k in _email_keys:
            if prefetch_data.get(k):
                customer_metadata["email"] = prefetch_data[k]
                break
        for k in ("phone", "company"):
            if prefetch_data.get(k):
                customer_metadata[k] = prefetch_data[k]

    # -----------------------------------
    # Serializer
    # -----------------------------------

    serializer = PlivoFrameSerializer(
        stream_id=stream_id,
        call_id=call_id,
        auth_id=os.getenv("PLIVO_AUTH_ID"),
        auth_token=os.getenv("PLIVO_AUTH_TOKEN"),
    )

    # -----------------------------------
    # Transport
    # -----------------------------------

    transport = FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
        ),
    )

    # -----------------------------------
    # STT
    # -----------------------------------

    stt = SarvamSTTService(
        api_key=SARVAM_API_KEY,
        settings=SarvamSTTService.Settings(model="saaras:v3"),
    )

    # -----------------------------------
    # LLM  (model + temperature from assistant config)
    # -----------------------------------

    llm = OpenAILLMService(
        api_key=OPENAI_API_KEY,
        settings=OpenAILLMService.Settings(
            model=llm_model,
            temperature=temperature,
        ),
    )

    # -----------------------------------
    # TTS  (voice resolved from assistant default_language)
    # -----------------------------------

    tts = create_tts(call_id, default_language=default_language, voice=configured_voice)

    # -----------------------------------
    # Context  (dynamic system prompt)
    # -----------------------------------

    context = LLMContext(
        messages=[cast(LLMContextMessage, {"role": "system", "content": system_prompt})]
    )

    context_aggregator = LLMContextAggregatorPair(context)

    # -----------------------------------
    # Processors
    # -----------------------------------

    noise_filter = NoiseFilterProcessor()

    language_processor = LanguageProcessor(
        call_id,
        default_language=default_language,
    )

    filler_processor = FillerProcessor(
        call_id=call_id,
        filler_messages=filler_messages_config,
    )

    intent_router = IntentRouterProcessor(
        call_id=call_id,
        filler_processor=filler_processor,
        intent_triggers=intent_triggers_config,
    )

    appointment_processor = AppointmentProcessor(
        call_id=call_id,
        customer_number=customer_number,
    )

    # -----------------------------------
    # Pipeline
    # -----------------------------------

    pipeline = Pipeline([
        transport.input(),
        stt,
        noise_filter,
        language_processor,
        intent_router,
        appointment_processor,
        context_aggregator.user(),
        llm,
        filler_processor,
        tts,
        transport.output(),
        context_aggregator.assistant(),
    ])

    # -----------------------------------
    # Task
    # -----------------------------------

    task = PipelineWorker(pipeline, params=PipelineParams())

    # -----------------------------------
    # Welcome message
    # -----------------------------------

    await task.queue_frame(TTSSpeakFrame(welcome_message))

    # -----------------------------------
    # Run
    # -----------------------------------

    runner = WorkerRunner()
    clog.info("Pipeline running")
    call_status = "user-ended"
    error_message = None
    pipeline_start_ts = time.time()   # measure only actual voice call time
    try:
        await runner.add_workers(task)
        await runner.run()
    except BaseException as e:
        if not isinstance(e, asyncio.CancelledError):
            call_status = "error"
            error_message = str(e)
            clog.error(f"Pipeline error: {e}")
        raise
    finally:
        ended_at = datetime.now()
        duration = int(time.time() - pipeline_start_ts)
        clog.info(f"Call ended | duration={duration}s | status={call_status}")
        # Prepend welcome message so the transcript starts from the assistant greeting
        raw_messages: list[Any] = list(context.messages)
        chat_messages = [{"role": "assistant", "content": welcome_message}] + [
            {"role": m.get("role", ""), "content": m.get("content") or ""}
            for m in raw_messages
            if m.get("role") != "system"
        ]
        chars_used = sum(len(m["content"]) for m in chat_messages)

        call_sessions.pop(call_id, None)
        call_log_manager.end(call_id)  # flush & close per-call log file

        # Spawn a detached task so CancelledError from WebSocket disconnect
        # cannot abort the DB save or webhook calls.
        asyncio.get_running_loop().create_task(_finalize_call(
            log_id=log_id,
            call_id=call_id,
            agent_id=agent_id,
            organization_id=organization_id,
            assistant_config=assistant_config,
            customer_number=customer_number,
            to_number=to_number,
            duration=duration,
            chat_messages=chat_messages,
            chars_used=chars_used,
            call_status=call_status,
            error_message=error_message,
            start_ts=start_ts,
            started_at=started_at,
            ended_at=ended_at,
            end_of_call_url=end_of_call_url,
            llm_model=llm_model,
            metadata=customer_metadata,
        ))
