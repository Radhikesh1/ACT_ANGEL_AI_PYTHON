# intent_router.py

from pipecat.processors.frame_processor import FrameProcessor

from pipecat.frames.frames import (
    TranscriptionFrame,
)

from frames.custom_frames import FillerRequestFrame


class IntentRouterProcessor(FrameProcessor):

    async def process_frame(self, frame, direction):

        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):

            text = frame.text.lower()

            print(f"[IntentRouter] User said: {text}")

            # Example trigger
            if "appointment" in text:

                # Push filler first
                await self.push_frame(
                    FillerRequestFrame(
                        language="bn",
                        message_type="thinking",
                    ),
                    direction,
                )

        # Continue normal pipeline
        await self.push_frame(frame, direction)