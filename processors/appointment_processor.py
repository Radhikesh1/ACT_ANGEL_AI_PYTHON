import asyncio

from loguru import logger

from pipecat.processors.frame_processor import (
    FrameProcessor,
)

from pipecat.frames.frames import (
    TextFrame,
    TTSSpeakFrame,
)

from utils.session_state import (
    conversation_states,
    language_sessions,
)

from utils.datetime_parser import (
    parse_datetime,
)

from services.appointment_api import (
    create_appointment_api,
)

from services.filler_manager import (
    get_appointment_filler,
)


class AppointmentProcessor(FrameProcessor):

    def __init__(
        self,
        call_id,
        customer_number,
        organization_id=None,
        agent_id=None,
    ):

        super().__init__()

        self.call_id = call_id
        self.customer_number = customer_number
        self.organization_id = organization_id
        self.agent_id = agent_id

    async def process_frame(
        self,
        frame,
        direction,
    ):

        await super().process_frame(frame, direction)

        if not isinstance(frame, TextFrame):

            await self.push_frame(
                frame,
                direction,
            )

            return

        user_text = frame.text.lower()

        state = conversation_states.get(
            self.call_id,
            {}
        )

        # ------------------------------------------------
        # Detect Appointment Intent
        # ------------------------------------------------

        if any(x in user_text for x in [

            "callback",
            "call me",
            "appointment",
            "site visit",
            "visit",
            "see villa",

            "কল করুন",
            "ভিজিট",
            "অ্যাপয়েন্টমেন্ট",

        ]):

            appointment_type = (
                "site_visit"
                if "visit" in user_text
                else "callback"
            )

            conversation_states[
                self.call_id
            ] = {

                "awaiting_datetime": True,

                "appointment_type": appointment_type,
            }

            await self.push_frame(
                TTSSpeakFrame(
                    "What date and time works best for you?"
                ),
                direction,
            )

            return

        # ------------------------------------------------
        # Awaiting Datetime
        # ------------------------------------------------

        if state.get("awaiting_datetime"):

            parsed = parse_datetime(
                user_text
            )

            if not parsed:

                await self.push_frame(
                    TTSSpeakFrame(
                        "Please provide both date and time."
                    ),
                    direction,
                )

                return

            # ----------------------------------------
            # Language
            # ----------------------------------------

            language = language_sessions.get(
                self.call_id,
                {}
            ).get(
                "language",
                "english",
            )

            # ----------------------------------------
            # Speak filler FIRST
            # ----------------------------------------

            filler = get_appointment_filler(
                language
            )

            await self.push_frame(
                TTSSpeakFrame(filler),
                direction,
            )

            # ----------------------------------------
            # API CALL IN BACKGROUND
            # ----------------------------------------

            task = asyncio.create_task(

                self.handle_appointment(
                    parsed,
                    state,
                    direction,
                )
            )

            task.add_done_callback(
                lambda t: logger.error(
                    f"Appointment task failed: {t.exception()}"
                ) if not t.cancelled() and t.exception() else None
            )

            return

        await self.push_frame(
            frame,
            direction,
        )

    # ------------------------------------------------
    # Background Appointment Task
    # ------------------------------------------------

    async def handle_appointment(
        self,
        parsed,
        state,
        direction,
    ):

        try:

            result = await create_appointment_api(

                appointment_type=state[
                    "appointment_type"
                ],

                appointment_datetime=parsed,

                from_number=self.customer_number,

                session_id=self.call_id,

                organization_id=self.organization_id,

                agent_id=self.agent_id,
            )

            conversation_states.pop(
                self.call_id,
                None,
            )

            await self.push_frame(
                TTSSpeakFrame(
                    f"Your appointment has been scheduled for {parsed}."
                ),
                direction,
            )

        except Exception as e:

            logger.error(
                f"Appointment creation failed for call_id={self.call_id}: {e}"
            )

            await self.push_frame(
                TTSSpeakFrame(
                    "Sorry, I could not create the appointment right now."
                ),
                direction,
            )