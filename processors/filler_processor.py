# import random

# from pipecat.processors.frame_processor import (
#     FrameProcessor,
# )

# from pipecat.frames.frames import (
#     TTSSpeakFrame,
# )

# from frames.custom_frames import (
#     FillerRequestFrame,
# )

# FILLERS = {

#     "english": [
#         "Just a moment.",
#         "Let me check that.",
#         "One second please.",
#     ],

#     "hindi": [
#         "एक क्षण।",
#         "मैं जांच कर रही हूँ।",
#     ],

#     "bengali": [
#         "একটু দেখছি।",
#         "এক মুহূর্ত।",
#     ],
# }


# class FillerProcessor(FrameProcessor):

#     async def process_frame(
#         self,
#         frame,
#         direction,
#     ):

#         # HANDLE FILLER REQUEST
#         if isinstance(frame, FillerRequestFrame):

#             language = frame.language

#             filler = random.choice(
#                 FILLERS.get(
#                     language,
#                     FILLERS["english"],
#                 )
#             )

#             await self.push_frame(
#                 TTSSpeakFrame(filler),
#                 direction,
#             )

#             return

#         # PASS EVERYTHING ELSE
#         await self.push_frame(
#             frame,
#             direction,
#         )




# import random
# import asyncio

# from pipecat.processors.frame_processor import (
#     FrameProcessor,
# )

# from pipecat.frames.frames import (
#     TextFrame,
#     TTSSpeakFrame,
# )

# from services.filler_manager import (
#     get_filler,
# )

# from utils.session_state import (
#     language_sessions,
# )


# class FillerProcessor(FrameProcessor):

#     async def process_frame(
#         self,
#         frame,
#         direction,
#     ):

#         if not isinstance(frame, TextFrame):

#             await self.push_frame(
#                 frame,
#                 direction,
#             )

#             return

#         text = frame.text.lower()

#         # ----------------------------------------
#         # Avoid fillers for short replies
#         # ----------------------------------------

#         if len(text.split()) > 5:

#             if random.random() < 0.30:

#                 language = "english"

#                 filler = get_filler(
#                     language
#                 )

#                 await self.push_frame(
#                     TTSSpeakFrame(
#                         filler
#                     ),
#                     direction,
#                 )

#                 await asyncio.sleep(0.2)

#         await self.push_frame(
#             frame,
#             direction,
#         )



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

            print(f"[FillerProcessor] Sending filler: {filler_text}")

            await self.push_frame(
                TextFrame(filler_text),
                direction,
            )

            return

        # Pass all other frames
        await self.push_frame(frame, direction)

