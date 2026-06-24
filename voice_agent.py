import os

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

load_dotenv()

OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY") or ""
SARVAM_API_KEY: str = os.getenv("SARVAM_API_KEY") or ""

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY missing")

if not SARVAM_API_KEY:
    raise ValueError("SARVAM_API_KEY missing")


async def run_bot(websocket_client):

    # -----------------------------------
    # Parse websocket
    # -----------------------------------

    transport_type, call_data = await parse_telephony_websocket(websocket_client)

    stream_id = call_data["stream_id"]
    call_id = call_data["call_id"]

    session = call_sessions.get(call_id, {})
    customer_number: str = session.get("from_number", "")
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

    logger.info(
        f"Starting bot for call {call_id} | "
        f"assistant: {assistant_config.get('name', 'default')} | "
        f"model: {llm_model}"
    )

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
    await runner.run(task)
