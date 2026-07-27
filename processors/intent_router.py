from pipecat.processors.frame_processor import FrameProcessor
from pipecat.frames.frames import TranscriptionFrame

from frames.custom_frames import FillerRequestFrame
from utils.session_state import language_sessions

_LANGUAGE_CODE_MAP = {
    "english": "en",
    "hindi": "hi",
    "bengali": "bn",
}

# Used when no intent_triggers are configured for the assistant
_DEFAULT_INTENT_TRIGGERS = [
    {"keywords": ["appointment", "book", "schedule", "booking"], "action": "appointment"},
]


class IntentRouterProcessor(FrameProcessor):

    def __init__(
        self,
        call_id: str,
        filler_processor=None,
        intent_triggers: list | None = None,
    ):
        super().__init__()
        self.call_id = call_id
        self.filler_processor = filler_processor
        # None means "not configured" — use defaults.
        # An explicit empty list means "no triggers".
        self.intent_triggers = (
            intent_triggers if intent_triggers is not None else _DEFAULT_INTENT_TRIGGERS
        )

    def _language_code(self) -> str:
        session = language_sessions.get(self.call_id, {})
        lang = session.get("language", "english")
        return _LANGUAGE_CODE_MAP.get(lang, "en")

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            text = frame.text.lower()
            for trigger in self.intent_triggers:
                keywords = trigger.get("keywords", [])
                if any(kw in text for kw in keywords):
                    await self.push_frame(
                        FillerRequestFrame(
                            language=self._language_code(),
                            message_type="thinking",
                        ),
                        direction,
                    )
                    break

        await self.push_frame(frame, direction)
