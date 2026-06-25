import os
import time
import json
import uuid
from datetime import datetime

import httpx
from dotenv import load_dotenv
from loguru import logger

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketTransport,
    FastAPIWebsocketParams,
)
from pipecat.serializers.plivo import PlivoFrameSerializer
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.frames.frames import TTSSpeakFrame

from processors.language_processor import LanguageProcessor
from processors.filler_processor import FillerProcessor
from processors.appointment_processor import AppointmentProcessor
from processors.intent_router import IntentRouterProcessor
from processors.noise_gate_processor import NoiseFilterProcessor

from system_prompt import SYSTEM_PROMPT
from utils.session_state import call_sessions
from utils.tts_factory import create_tts
from database.connection import AsyncSessionLocal
from database.models import CallLog
from services.recording_service import start_recording, fetch_and_upload

load_dotenv()

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
        "metadata": {},
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


async def run_bot(websocket_client):

    # -----------------------------------
    # Parse websocket
    # -----------------------------------

    transport_type, call_data = await parse_telephony_websocket(websocket_client)

    stream_id = call_data["stream_id"]
    call_id = call_data["call_id"]

    session = call_sessions.get(call_id, {})
    customer_number: str = session.get("from_number", "")
    to_number: str = session.get("to_number", "")
    assistant_config: dict = session.get("assistant_config") or {}

    # Resolve config — fall back to defaults if no assistant is assigned
    system_prompt: str = assistant_config.get("system_prompt") or SYSTEM_PROMPT
    welcome_message: str = (
        assistant_config.get("welcome_message")
        or "Hello. I am Ciya. How can I help you?"
    )
    llm_model: str = assistant_config.get("llm_model") or "gpt-4o-mini"
    temperature: float = float(assistant_config.get("temperature") or 0.2)
    default_language: str = assistant_config.get("default_language") or "english"
    configured_voice: str | None = assistant_config.get("voice") or None
    agent_id: str = assistant_config.get("id") or ""
    prefetch_url: str | None = assistant_config.get("prefetch_webhook_url") or None
    end_of_call_url: str | None = assistant_config.get("end_of_call_webhook_url") or None

    logger.info(
        f"Starting bot for call {call_id} | "
        f"assistant: {assistant_config.get('name', 'default')} | "
        f"model: {llm_model}"
    )

    start_ts = time.time()

    # -----------------------------------
    # Start call recording (optional)
    # -----------------------------------

    await start_recording(call_id)

    # -----------------------------------
    # Prefetch webhook (optional)
    # -----------------------------------

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
        messages=[{"role": "system", "content": system_prompt}]
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

    filler_processor = FillerProcessor(call_id=call_id)

    intent_router = IntentRouterProcessor(
        call_id=call_id,
        filler_processor=filler_processor,
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

    task = PipelineTask(pipeline, params=PipelineParams())

    # -----------------------------------
    # Welcome message
    # -----------------------------------

    await task.queue_frame(TTSSpeakFrame(welcome_message))

    # -----------------------------------
    # Run
    # -----------------------------------

    runner = PipelineRunner()
    logger.info("Pipeline running")
    call_status = "user-ended"
    error_message = None
    try:
        await runner.run(task)
    except Exception as e:
        call_status = "error"
        error_message = str(e)
        raise
    finally:
        ended_at = datetime.utcnow()
        duration = int(time.time() - start_ts)
        chat_messages = [
            {"role": m["role"], "content": m.get("content") or ""}
            for m in context.messages
            if m.get("role") != "system"
        ]
        chars_used = sum(len(m["content"]) for m in chat_messages)

        # Fetch recording from Plivo and upload to Cloudinary
        recording_url: str | None = None
        try:
            recording_url = await fetch_and_upload(call_id)
        except Exception as rec_err:
            logger.error(f"[Recording] Unexpected error: {rec_err}")

        # Save to database
        try:
            async with AsyncSessionLocal() as db:
                log = CallLog(
                    id=uuid.uuid4(),
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
                    recording_url=recording_url,
                    started_at=datetime.utcfromtimestamp(start_ts),
                    ended_at=ended_at,
                )
                db.add(log)
                await db.commit()
                logger.info(
                    f"[CallLog] Saved call log for session {call_id} "
                    f"(duration={duration}s, recording={'yes' if recording_url else 'no'})"
                )
        except Exception as db_err:
            logger.error(f"[CallLog] Failed to save call log: {db_err}")

        # Fire end-of-call webhook
        if end_of_call_url:
            await _call_end_of_call_webhook(
                url=end_of_call_url,
                session_id=call_id,
                call_id=call_id,
                agent_id=agent_id,
                from_number=customer_number,
                to_number=to_number,
                messages=context.messages,
                start_ts=start_ts,
                assistant_config=assistant_config,
                call_status=call_status,
                error_message=error_message,
            )

        call_sessions.pop(call_id, None)
