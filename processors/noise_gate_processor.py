# from pipecat.processors.frame_processor import (
#     FrameProcessor,
# )

# from pipecat.frames.frames import (
#     TextFrame,
# )

# IGNORE_TEXTS = {

#     "",
#     ".",
#     "..",
#     "...",
#     "hmm",
#     "uh",
#     "umm",
#     "ah",
# }

# MINIMUM_LENGTH = 3


# class NoiseFilterProcessor(FrameProcessor):

#     async def process_frame(
#         self,
#         frame,
#         direction,
#     ):

#         # Pass non-text frames
#         if not isinstance(frame, TextFrame):

#             await self.push_frame(
#                 frame,
#                 direction,
#             )

#             return

#         text = frame.text.strip().lower()

#         # Ignore tiny fragments
#         if len(text) < MINIMUM_LENGTH:
#             return

#         # Ignore filler sounds
#         if text in IGNORE_TEXTS:
#             return

#         # Continue pipeline
#         await self.push_frame(
#             frame,
#             direction,
#         )

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

        print(f"[NoiseFilter] Received: {text}")

        # Ignore very short fragments
        if len(text) < MINIMUM_LENGTH:
            print(f"[NoiseFilter] Dropped short text: {text}")
            return

        # Ignore filler sounds
        if text in IGNORE_TEXTS:
            print(f"[NoiseFilter] Dropped filler: {text}")
            return

        # Pass valid speech
        await self.push_frame(frame, direction)