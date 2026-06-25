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
    initialize_language_session,

    is_positive_confirmation,
    is_negative_confirmation,
)


class LanguageProcessor(FrameProcessor):

    def __init__(self, call_id, default_language: str = "english"):

        super().__init__()

        self.call_id = call_id

        initialize_language_session(call_id, default_language)

    async def process_frame(
        self,
        frame,
        direction,
    ):

        await super().process_frame(frame, direction)

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