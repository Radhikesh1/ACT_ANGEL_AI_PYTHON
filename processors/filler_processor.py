import random

from pipecat.processors.frame_processor import FrameProcessor
from pipecat.frames.frames import TextFrame

from frames.custom_frames import FillerRequestFrame
from utils.filler_text import FILLERS_ENGLISH, FILLERS_HINDI, FILLERS_BENGALI

_DEFAULT_FILLERS: dict[str, list[str]] = {
    "en": FILLERS_ENGLISH,
    "hi": FILLERS_HINDI,
    "bn": FILLERS_BENGALI,
}


class FillerProcessor(FrameProcessor):

    def __init__(self, call_id: str = "", filler_messages: dict | None = None):
        super().__init__()
        self.call_id = call_id
        self.filler_messages = filler_messages or {}

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, FillerRequestFrame):
            lang = frame.language  # "en", "hi", "bn"

            custom = self.filler_messages.get(lang)
            if custom:
                filler_text = custom
            else:
                pool = _DEFAULT_FILLERS.get(lang, _DEFAULT_FILLERS["en"])
                filler_text = random.choice(pool)

            await self.push_frame(TextFrame(filler_text), direction)
            return

        await self.push_frame(frame, direction)
