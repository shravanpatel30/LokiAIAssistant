"""Voice input (Whisper) and output (Piper) for the assistant."""
import io
import wave
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from piper.voice import PiperVoice

# ---- Configuration ----
WHISPER_MODEL_SIZE = "small.en"
WHISPER_DEVICE = "cpu"           # use "cpu" if you want to save VRAM for the LLM or use "cuda"
WHISPER_COMPUTE_TYPE = "int8"  # "int8" if memory-constrained

PIPER_VOICE_PATH = Path(__file__).parent / "voices" / "en_US-amy-medium.onnx"

SAMPLE_RATE = 16000  # Whisper expects 16kHz
CHANNELS = 1


# ---- Models loaded once ----
print("Loading Whisper...")
_whisper = WhisperModel(WHISPER_MODEL_SIZE, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE)
print(f"Loading Piper voice: {PIPER_VOICE_PATH.name}")
_piper = PiperVoice.load(str(PIPER_VOICE_PATH))


# ---- Speech to text ----
class Recorder:
    """Records audio while .recording is True. Returns numpy array."""
    def __init__(self):
        self.frames = []
        self.recording = False
        self.stream = None

    def start(self):
        self.frames = []
        self.recording = True

        def callback(indata, frame_count, time_info, status):
            if self.recording:
                self.frames.append(indata.copy())

        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS,
            dtype="float32", callback=callback,
        )
        self.stream.start()

    def stop(self):
        self.recording = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if not self.frames:
            return None
        return np.concatenate(self.frames, axis=0).flatten()


def transcribe(audio_array):
    """Run Whisper on a numpy float32 audio array. Returns text."""
    if audio_array is None or len(audio_array) < SAMPLE_RATE * 0.3:
        return ""  # too short — under 0.3s is almost certainly no speech
    segments, _ = _whisper.transcribe(audio_array, language="en", beam_size=5)
    return " ".join(seg.text.strip() for seg in segments).strip()

def speak(text):
    """Play text aloud through default audio output."""
    if not text or not text.strip():
        return
    try:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            _piper.synthesize_wav(text, wav)

        buffer.seek(0)
        with wave.open(buffer, "rb") as wav:
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16)

        sd.play(audio, samplerate=rate)
        sd.wait()
    except Exception as e:
        print(f"(speak failed: {e})")