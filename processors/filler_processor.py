# filler_processor.py

from pipecat.processors.frame_processor import FrameProcessor

from pipecat.frames.frames import (
    TextFrame,
)

from frames.custom_frames import FillerRequestFrame


class FillerProcessor(FrameProcessor):

    async def process_frame(self, frame, direction):

        await super().process_frame(frame, direction)

        # Handle custom filler request
        if isinstance(frame, FillerRequestFrame):

            if frame.language == "bn":

                filler_text = "একটু ভাবছি"

            elif frame.language == "hi":

                filler_text = "एक क्षण सोच रहा हूँ"

            else:

                filler_text = "Just thinking"


            await self.push_frame(
                TextFrame(filler_text),
                direction,
            )

            return

        # Pass all other frames
        await self.push_frame(frame, direction)

