import numpy as np


def is_silence(audio_bytes, threshold=500):

    audio_np = np.frombuffer(
        audio_bytes,
        dtype=np.int16,
    )

    rms = np.sqrt(
        np.mean(audio_np.astype(np.float32) ** 2)
    )

    return rms < threshold