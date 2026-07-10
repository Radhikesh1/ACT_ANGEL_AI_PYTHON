# noise_gate_processor.py

from pipecat.processors.frame_processor import FrameProcessor

from pipecat.frames.frames import (
    TranscriptionFrame,
    InterimTranscriptionFrame,
)

from data.multilingual_keywords import IGNORE_TEXTS



MINIMUM_LENGTH = 3


class NoiseFilterProcessor(FrameProcessor):

    async def process_frame(self, frame, direction):

        # IMPORTANT
        await super().process_frame(frame, direction)

        # Pass non transcription frames
        if not isinstance(
            frame,
            (
                TranscriptionFrame,
                InterimTranscriptionFrame,
            ),
        ):
            await self.push_frame(frame, direction)
            return

        text = frame.text.strip().lower()


        # Ignore very short fragments
        if len(text) < MINIMUM_LENGTH:
            return

        # Ignore filler sounds
        if text in IGNORE_TEXTS:
            return

        # Pass valid speech
        await self.push_frame(frame, direction)