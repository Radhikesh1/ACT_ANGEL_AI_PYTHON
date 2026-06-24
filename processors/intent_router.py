# from pipecat.processors.frame_processor import (
#     FrameProcessor,
# )

# from pipecat.frames.frames import (
#     TextFrame,
#     TTSSpeakFrame,
# )

# from data.faq_data import (
#     FAQ_RESPONSES,
#     FAQ_KEYWORDS,
# )


# class IntentRouterProcessor(FrameProcessor):

#     def __init__(self):
#         super().__init__()

#     async def process_frame(self, frame, direction):

#         # VERY IMPORTANT
#         # Let parent process lifecycle frames
#         await super().process_frame(frame, direction)

#         # ----------------------------------------
#         # Ignore non-text frames
#         # ----------------------------------------

#         if not isinstance(frame, TextFrame):

#             await self.push_frame(frame, direction)
#             return

#         user_text = frame.text.lower()

#         # ----------------------------------------
#         # FAQ Routing
#         # ----------------------------------------

#         for intent, keywords in FAQ_KEYWORDS.items():

#             for keyword in keywords:

#                 if keyword in user_text:

#                     response = FAQ_RESPONSES[intent]

#                     await self.push_frame(
#                         TTSSpeakFrame(response),
#                         direction,
#                     )

#                     return

#         # ----------------------------------------
#         # Continue to GPT
#         # ----------------------------------------

#         await self.push_frame(frame, direction)





# from loguru import logger

# from pipecat.processors.frame_processor import (
#     FrameProcessor,
# )

# from pipecat.frames.frames import (
#     TextFrame,
#     TTSSpeakFrame,
#     StartFrame,
# )

# from data.faq_data import (
#     FAQ_RESPONSES,
#     FAQ_KEYWORDS,
# )


# class IntentRouterProcessor(FrameProcessor):

#     def __init__(self):
#         super().__init__()
#         self._started = False

#     async def process_frame(self, frame, direction):

#         # ----------------------------------------
#         # IMPORTANT
#         # Let Pipecat initialize processor first
#         # ----------------------------------------

#         await super().process_frame(frame, direction)

#         # ----------------------------------------
#         # Handle StartFrame
#         # ----------------------------------------

#         if isinstance(frame, StartFrame):
#             self._started = True

#             logger.info(
#                 "IntentRouterProcessor started"
#             )

#             await self.push_frame(frame, direction)
#             return

#         # ----------------------------------------
#         # Ignore frames before startup
#         # ----------------------------------------

#         if not self._started:
#             return

#         # ----------------------------------------
#         # Ignore non-text frames
#         # ----------------------------------------

#         if not isinstance(frame, TextFrame):

#             await self.push_frame(frame, direction)
#             return

#         user_text = frame.text.lower().strip()

#         logger.info(f"USER TEXT: {user_text}")

#         # ----------------------------------------
#         # FAQ Routing
#         # ----------------------------------------

#         for intent, keywords in FAQ_KEYWORDS.items():

#             for keyword in keywords:

#                 if keyword in user_text:

#                     response = FAQ_RESPONSES.get(
#                         intent
#                     )

#                     logger.info(
#                         f"FAQ MATCH: {intent}"
#                     )

#                     await self.push_frame(
#                         TTSSpeakFrame(response), # type: ignore
#                         direction,
#                     )

#                     return

#         # ----------------------------------------
#         # Continue to GPT
#         # ----------------------------------------

#         await self.push_frame(frame, direction)




# from pipecat.processors.frame_processor import (
#     FrameProcessor,
# )

# from pipecat.frames.frames import (
#     TextFrame,
#     TTSSpeakFrame,
# )

# from data.faq_data import (
#     FAQ_RESPONSES,
#     FAQ_KEYWORDS,
# )


# from frames.custom_frames import (
#     FillerRequestFrame,
# )

# class IntentRouterProcessor(FrameProcessor):

#     async def process_frame(
#         self,
#         frame,
#         direction,
#     ):

#         await super().process_frame(
#             frame,
#             direction,
#         )

#         if not isinstance(frame, TextFrame):

#             await self.push_frame(
#                 frame,
#                 direction,
#             )

#             return

#         text = frame.text.lower()

#         # -----------------------------------
#         # FAQ Match
#         # -----------------------------------

#         for intent, keywords in (
#             FAQ_KEYWORDS.items()
#         ):

#             for keyword in keywords:

#                 if keyword in text:

#                     response = FAQ_RESPONSES[
#                         intent
#                     ]

#                     await self.push_frame(
#                         TTSSpeakFrame(
#                             response
#                         ),
#                         direction,
#                     )

#                     return

#         await self.push_frame(
#             frame,
#             direction,
#         )

# intent_router.py

from pipecat.processors.frame_processor import FrameProcessor

from pipecat.frames.frames import (
    TranscriptionFrame,
)

from frames.custom_frames import FillerRequestFrame


class IntentRouterProcessor(FrameProcessor):

    async def process_frame(self, frame, direction):

        await super().process_frame(frame, direction)

        # Detect user transcription
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