# from pipecat.processors.frame_processor import (
#     FrameProcessor,
# )

# from pipecat.frames.frames import (
#     TextFrame,
#     TTSSpeakFrame,
# )

# from utils.session_state import (
#     language_sessions,
# )

# # ----------------------------------------
# # LANGUAGE DETECTION
# # ----------------------------------------

# LANGUAGE_KEYWORDS = {

#     "hindi": [

#         # Explicit
#         "hindi",
#         "हिंदी",

#         # Intent phrases
#         "mujhe english samajh nahi aati",
#         "मुझे अंग्रेजी समझ नहीं आती",
#         "hindi mein",
#         "हिंदी में",
#     ],

#     "bengali": [

#         # Explicit
#         "bengali",
#         "bangla",
#         "বাংলা",

#         # Intent phrases
#         "ami english bujhi na",
#         "আমি ইংরেজি বুঝি না",
#         "bangla bolo",
#         "বাংলায় বলুন",
#     ],
# }

# # ----------------------------------------
# # CONFIRMATIONS
# # ----------------------------------------

# YES_WORDS = {

#     "yes",
#     "haan",
#     "ok",
#     "okay",
#     "sure",
#     "correct",
# }

# NO_WORDS = {

#     "no",
#     "nah",
#     "continue english",
# }


# class LanguageProcessor(FrameProcessor):

#     def __init__(self, call_id):

#         super().__init__()

#         self.call_id = call_id

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

#         text = frame.text.lower().strip()

#         # ----------------------------------------
#         # SESSION
#         # ----------------------------------------

#         session = language_sessions.get(

#             self.call_id,

#             {
#                 "language": "english",
#                 "pending": None,
#                 "locked": False,
#             }
#         )

#         # ----------------------------------------
#         # HANDLE CONFIRMATION
#         # ----------------------------------------

#         if session["pending"]:

#             pending_lang = session["pending"]

#             # YES
#             if text in YES_WORDS:

#                 session["language"] = pending_lang
#                 session["pending"] = None
#                 session["locked"] = True

#                 language_sessions[
#                     self.call_id
#                 ] = session

#                 await self.push_frame(
#                     TTSSpeakFrame(
#                         f"Okay. Continuing in {pending_lang}."
#                     ),
#                     direction,
#                 )

#                 return

#             # NO
#             elif text in NO_WORDS:

#                 session["pending"] = None

#                 language_sessions[
#                     self.call_id
#                 ] = session

#                 await self.push_frame(
#                     TTSSpeakFrame(
#                         "Okay. Continuing in English."
#                     ),
#                     direction,
#                 )

#                 return

#         # ----------------------------------------
#         # ALREADY LOCKED
#         # ----------------------------------------

#         if session["locked"]:

#             await self.push_frame(
#                 frame,
#                 direction,
#             )

#             return

#         # ----------------------------------------
#         # DETECT LANGUAGE REQUEST
#         # ----------------------------------------

#         current_language = session["language"]

#         for lang, keywords in (
#             LANGUAGE_KEYWORDS.items()
#         ):

#             for keyword in keywords:

#                 if keyword in text:

#                     # already active
#                     if lang == current_language:

#                         await self.push_frame(
#                             frame,
#                             direction,
#                         )

#                         return

#                     session["pending"] = lang

#                     language_sessions[
#                         self.call_id
#                     ] = session

#                     await self.push_frame(
#                         TTSSpeakFrame(
#                             f"I detected {lang}. "
#                             f"Would you like me to continue in {lang}?"
#                         ),
#                         direction,
#                     )

#                     return

#         # ----------------------------------------
#         # CONTINUE
#         # ----------------------------------------

#         await self.push_frame(
#             frame,
#             direction,
#         )



# from pipecat.processors.frame_processor import (
#     FrameProcessor,
# )

# from pipecat.frames.frames import (
#     TextFrame,
#     TTSSpeakFrame,
# )

# from utils.session_state import (
#     language_sessions,
# )

# from data.multilingual_keywords import (
#     LANGUAGE_KEYWORDS,
#     LANGUAGE_CONFIRMATIONS,
#     LANGUAGE_REJECTIONS,
# )

# from utils.language_manager import (

#     get_language,
#     set_language,
#     set_pending_language,
#     clear_pending_language,

#     is_positive_confirmation,
#     is_negative_confirmation,
# )


# class LanguageProcessor(FrameProcessor):

#     def __init__(self, call_id):

#         super().__init__()

#         self.call_id = call_id

#         # ----------------------------------------
#         # Initialize Session
#         # ----------------------------------------

#         if self.call_id not in language_sessions:

#             language_sessions[self.call_id] = {

#                 "language": "english",

#                 "locked": False,

#                 "pending": None,
#             }

#     async def process_frame(
#         self,
#         frame,
#         direction,
#     ):

#         # ----------------------------------------
#         # Ignore Non-Text Frames
#         # ----------------------------------------

#         if not isinstance(frame, TextFrame):

#             await self.push_frame(
#                 frame,
#                 direction,
#             )

#             return

#         text = frame.text.lower().strip()

#         session = language_sessions.get(
#             self.call_id,
#             {}
#         )

#         # ----------------------------------------
#         # Pending Language Confirmation
#         # ----------------------------------------

#         if session.get("pending"):

#             pending_lang = session[
#                 "pending"
#             ]

#             # ------------------------------------
#             # User Confirmed
#             # ------------------------------------

#             if text in LANGUAGE_CONFIRMATIONS:

#                 set_language(
#                     self.call_id,
#                     pending_lang,
#                 )

#                 confirmation_message = {

#                     "english":
#                         "Okay. Continuing in English.",

#                     "hindi":
#                         "ठीक है। अब हिंदी में बात करेंगे।",

#                     "bengali":
#                         "ঠিক আছে। এখন বাংলা বলছি।",

#                     "telugu":
#                         "సరే. ఇప్పుడు తెలుగు లో మాట్లాడుతాను.",

#                     "gujarati":
#                         "બરાબર. હવે ગુજરાતી માં વાત કરીશું.",
#                 }

#                 await self.push_frame(
#                     TTSSpeakFrame(
#                         confirmation_message.get(
#                             pending_lang,
#                             "Language updated."
#                         )
#                     ),
#                     direction,
#                 )

#                 return

#             # ------------------------------------
#             # User Rejected
#             # ------------------------------------

#             if text in LANGUAGE_REJECTIONS:

#                 clear_pending_language(
#                     self.call_id
#                 )

#                 await self.push_frame(
#                     TTSSpeakFrame(
#                         "Okay. Continuing in English."
#                     ),
#                     direction,
#                 )

#                 return

#             # ------------------------------------
#             # Unclear Confirmation
#             # ------------------------------------

#             await self.push_frame(
#                 TTSSpeakFrame(
#                     "Please say yes or no."
#                 ),
#                 direction,
#             )

#             return

#         # ----------------------------------------
#         # Detect Language Request
#         # ONLY if language not locked
#         # ----------------------------------------

#         if not session.get("locked"):

#             for lang, keywords in (
#                 LANGUAGE_KEYWORDS.items()
#             ):

#                 for keyword in keywords:

#                     if keyword in text:

#                         current_language = (
#                             get_language(
#                                 self.call_id
#                             )
#                         )

#                         # ----------------------------
#                         # Ignore Same Language
#                         # ----------------------------

#                         if lang == current_language:

#                             await self.push_frame(
#                                 frame,
#                                 direction,
#                             )

#                             return

#                         # ----------------------------
#                         # Ask Confirmation
#                         # ----------------------------

#                         set_pending_language(
#                             self.call_id,
#                             lang,
#                         )

#                         confirmation_question = {

#                             "english":
#                                 "Would you like me to continue in English?",

#                             "hindi":
#                                 "क्या आप चाहते हैं कि मैं हिंदी में बात करूं?",

#                             "bengali":
#                                 "আপনি কি চান আমি বাংলায় কথা বলি?",

#                             "telugu":
#                                 "మీరు తెలుగు లో మాట్లాడాలని అనుకుంటున్నారా?",

#                             "gujarati":
#                                 "શું તમે ઇચ્છો છો કે હું ગુજરાતી માં વાત કરું?",
#                         }

#                         await self.push_frame(
#                             TTSSpeakFrame(
#                                 confirmation_question.get(
#                                     lang,
#                                     "Would you like to change language?"
#                                 )
#                             ),
#                             direction,
#                         )

#                         return

#         # ----------------------------------------
#         # Continue Pipeline
#         # ----------------------------------------

#         await self.push_frame(
#             frame,
#             direction,
#         )



from pipecat.processors.frame_processor import (
    FrameProcessor,
)

from pipecat.frames.frames import (
    TextFrame,
    TTSSpeakFrame,
)

from utils.session_state import (
    language_sessions,
)

from data.multilingual_keywords import (
    LANGUAGE_KEYWORDS,
)

from utils.language_manager import (

    get_language,
    set_language,
    set_pending_language,
    clear_pending_language,

    is_positive_confirmation,
    is_negative_confirmation,
)


class LanguageProcessor(FrameProcessor):

    def __init__(self, call_id):

        super().__init__()

        self.call_id = call_id

        # ----------------------------------------
        # Initialize Session
        # ----------------------------------------

        if self.call_id not in language_sessions:

            language_sessions[self.call_id] = {

                "language": "english",

                # Prevent random switching
                "locked": False,

                # Pending confirmation language
                "pending": None,
            }

    async def process_frame(
        self,
        frame,
        direction,
    ):

        # ----------------------------------------
        # Ignore Non-Text Frames
        # ----------------------------------------

        if not isinstance(frame, TextFrame):

            await self.push_frame(
                frame,
                direction,
            )

            return

        text = frame.text.lower().strip()

        session = language_sessions.get(
            self.call_id,
            {}
        )

        # ----------------------------------------
        # HANDLE PENDING LANGUAGE CONFIRMATION
        # ----------------------------------------

        if session.get("pending"):

            pending_language = session[
                "pending"
            ]

            # ------------------------------------
            # Positive Confirmation
            # ------------------------------------

            if is_positive_confirmation(
                text
            ):

                set_language(
                    self.call_id,
                    pending_language,
                )

                confirmation_messages = {

                    "english":
                        "Okay. Continuing in English.",

                    "hindi":
                        "ठीक है। अब हिंदी में बात करेंगे।",

                    "bengali":
                        "ঠিক আছে। এখন বাংলা বলছি।",

                    "telugu":
                        "సరే. ఇప్పుడు తెలుగు లో మాట్లాడుతాను.",

                    "gujarati":
                        "બરાબર. હવે ગુજરાતી માં વાત કરીશું.",
                }

                await self.push_frame(
                    TTSSpeakFrame(
                        confirmation_messages.get(
                            pending_language,
                            "Language updated."
                        )
                    ),
                    direction,
                )

                return

            # ------------------------------------
            # Negative Confirmation
            # ------------------------------------

            if is_negative_confirmation(
                text
            ):

                clear_pending_language(
                    self.call_id
                )

                await self.push_frame(
                    TTSSpeakFrame(
                        "Okay. Continuing in English."
                    ),
                    direction,
                )

                return

            # ------------------------------------
            # User gave another sentence
            # Treat it as unclear
            # ------------------------------------

            await self.push_frame(
                TTSSpeakFrame(
                    "Please say yes or no if you want to change language."
                ),
                direction,
            )

            return

        # ----------------------------------------
        # DETECT LANGUAGE REQUEST
        # ONLY IF NOT LOCKED
        # ----------------------------------------

        if not session.get("locked"):

            for language, keywords in (
                LANGUAGE_KEYWORDS.items()
            ):

                for keyword in keywords:

                    if keyword in text:

                        current_language = (
                            get_language(
                                self.call_id
                            )
                        )

                        # ----------------------------
                        # Ignore same language
                        # ----------------------------

                        if language == current_language:

                            await self.push_frame(
                                frame,
                                direction,
                            )

                            return

                        # ----------------------------
                        # Set pending language
                        # ----------------------------

                        set_pending_language(
                            self.call_id,
                            language,
                        )

                        confirmation_questions = {

                            "english":
                                "Would you like me to continue in English?",

                            "hindi":
                                "क्या आप चाहते हैं कि मैं हिंदी में बात करूं?",

                            "bengali":
                                "আপনি কি চান আমি বাংলায় কথা বলি?",

                            "telugu":
                                "మీరు తెలుగు లో మాట్లాడాలని అనుకుంటున్నారా?",

                            "gujarati":
                                "શું તમે ઇચ્છો છો કે હું ગુજરાતી માં વાત કરું?",
                        }

                        await self.push_frame(
                            TTSSpeakFrame(
                                confirmation_questions.get(
                                    language,
                                    "Would you like to change language?"
                                )
                            ),
                            direction,
                        )

                        return

        # ----------------------------------------
        # CONTINUE NORMAL PIPELINE
        # ----------------------------------------

        await self.push_frame(
            frame,
            direction,
        )