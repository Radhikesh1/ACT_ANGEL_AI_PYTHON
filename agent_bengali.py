import os

from dotenv import load_dotenv

from loguru import logger

from pipecat.pipeline.pipeline import Pipeline

from pipecat.pipeline.runner import (
    PipelineRunner,
)

from pipecat.pipeline.task import (
    PipelineTask,
    PipelineParams,
)

from pipecat.runner.utils import (
    parse_telephony_websocket,
)

from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketTransport,
    FastAPIWebsocketParams,
)

from pipecat.serializers.plivo import (
    PlivoFrameSerializer,
)

from pipecat.services.sarvam.stt import (
    SarvamSTTService,
)

# from pipecat.services.sarvam.tts import (
#     SarvamTTSService,
# )

from processors.filler_processor import (
    FillerProcessor,
)

from utils.tts_factory import (
    create_tts,
)

from pipecat.services.openai.llm import (
    OpenAILLMService,
)

from pipecat.processors.aggregators.llm_context import (
    LLMContext,
)

from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)

from pipecat.frames.frames import (
    TTSSpeakFrame,
)


from processors.language_processor import (
    LanguageProcessor,
)

from processors.filler_processor import (
    FillerProcessor,
)

from processors.appointment_processor import (
    AppointmentProcessor,
)

from processors.intent_router import (
    IntentRouterProcessor,
)

from processors.noise_gate_processor import (
    NoiseFilterProcessor,
)

from system_prompt import SYSTEM_PROMPT

from utils.session_state import call_sessions

load_dotenv()

OPENAI_API_KEY: str = os.getenv(
    "OPENAI_API_KEY"
) or ""

SARVAM_API_KEY: str = os.getenv(
    "SARVAM_API_KEY"
) or ""

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY missing")

if not SARVAM_API_KEY:
    raise ValueError("SARVAM_API_KEY missing")


async def run_bot(websocket_client):

    # -----------------------------------
    # Parse websocket
    # -----------------------------------

    transport_type, call_data = (
        await parse_telephony_websocket(
            websocket_client
        )
    )

    stream_id = call_data["stream_id"]

    call_id = call_data["call_id"]

    customer_number = call_sessions.get(
        call_id,
        {}
    ).get(
        "from_number",
        ""
    )
    logger.info(f"Call ID: {call_id}")

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

        settings=SarvamSTTService.Settings(
            model="saaras:v3",
        ),
    )

    # -----------------------------------
    # LLM
    # -----------------------------------

    llm = OpenAILLMService(

        api_key=OPENAI_API_KEY,

        settings=OpenAILLMService.Settings(
            model="gpt-4o-mini",
            temperature=0.2,
        ),
    )

    # -----------------------------------
    # TTS
    # -----------------------------------

    # tts = SarvamTTSService(

    #     api_key=SARVAM_API_KEY,

    #     settings=SarvamTTSService.Settings(
    #         model="bulbul:v3",
    #         voice="roopa",
    #     ),
    # )

    tts = create_tts(call_id)

    # -----------------------------------
    # Context
    # -----------------------------------

    context = LLMContext(
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            }
        ]
    )

    context_aggregator = (
        LLMContextAggregatorPair(context)
    )

    # -----------------------------------
    # Processors
    # -----------------------------------

    noise_filter = NoiseFilterProcessor()

    language_processor = LanguageProcessor(
        call_id
    )

    filler_processor = FillerProcessor(
        call_id=call_id
    )


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

    task = PipelineTask(
        pipeline,
        params=PipelineParams(),
    )

    # -----------------------------------
    # Welcome
    # -----------------------------------

    await task.queue_frame(
        TTSSpeakFrame(
            "Hello. I am Ciya. How can I help you?"
        )
    )

    # -----------------------------------
    # Run
    # -----------------------------------

    runner = PipelineRunner()

    logger.info(
        "Starting production voice assistant"
    )

    await runner.run(task)