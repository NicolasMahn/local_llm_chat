"""Speech for the chat window: listening until you stop talking, and reading replies aloud."""

import io
import re
import subprocess
import wave

import numpy as np

RATE = 16000
CHUNK_SECONDS = 0.1
QUIET_TO_END = 1.2  # seconds of quiet after speech that end what you said
LONGEST = 30  # seconds; a runaway recording ends here
MICROPHONE = ["pw-record", "--raw", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"]


def listen(stopped, source=MICROPHONE):
    """WAV bytes of one thing said, or None if stopped first.

    Waits for speech, then ends after a short quiet. The first half second
    only measures the room, so "speech" means clearly louder than that.
    Raises OSError when the microphone stops giving sound before any speech.
    """
    chunk_bytes = int(RATE * CHUNK_SECONDS) * 2
    process = subprocess.Popen(source, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    noise, calibration, before, speech = None, [], [], []
    quiet = 0.0
    try:
        while not stopped.is_set():
            data = process.stdout.read(chunk_bytes)
            if len(data) < chunk_bytes:
                break
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            level = float(np.sqrt(np.mean(samples ** 2)))
            if noise is None:
                calibration.append(level)
                if len(calibration) * CHUNK_SECONDS >= 0.5:
                    noise = float(np.median(calibration))
                continue
            loud = level > max(3 * noise, 300)
            if not speech:
                # Keep a moment from just before, so the first syllable is not cut.
                before = (before + [data])[-3:]
                if loud:
                    speech = before
                continue
            speech.append(data)
            quiet = 0.0 if loud else quiet + CHUNK_SECONDS
            if quiet >= QUIET_TO_END or len(speech) * CHUNK_SECONDS >= LONGEST:
                break
    finally:
        process.terminate()
        process.wait()
    if stopped.is_set():
        return None
    if not speech:
        raise OSError("no sound from the microphone")
    return to_wav(b"".join(speech))


def to_wav(pcm):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(pcm)
    return buffer.getvalue()


def speakable(markdown):
    """Reply text without the Markdown symbols a voice would read out."""
    text = re.sub(r"```.*?```", " (code omitted) ", markdown, flags=re.S)
    return re.sub(r"[*_`#>|]+", "", text).strip()


def speak(text):
    """Reads text aloud through the desktop's speech service; returns once done."""
    subprocess.run(["spd-say", "--wait", speakable(text)], check=False)


def stop_speaking():
    subprocess.run(["spd-say", "--cancel"], check=False)
