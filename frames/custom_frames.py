

from pipecat.frames.frames import Frame


class FillerRequestFrame(Frame):

    def __init__(
        self,
        language="english",
        message_type="thinking",
    ):

        super().__init__()

        self.language = language
        self.message_type = message_type