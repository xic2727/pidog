"""Audio recording and playback worker using PyAudio / ALSA / speech_recognition."""
import os
import io
import time
import wave
import subprocess
import threading
import logging
import tempfile
from typing import Optional, Any
from ..core.event_bus import EventBus

logger = logging.getLogger(__name__)


class AudioPlayer:
    """Handles audio playback for TTS and sound effects on Raspberry Pi and development machines."""

    @staticmethod
    def play_wav_bytes(audio_bytes: bytes):
        """Play WAV bytes using available system audio backend (aplay, afplay, ffplay)."""
        if not audio_bytes:
            return

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(audio_bytes)
                tmp_path = f.name

            AudioPlayer.play_file(tmp_path)
        except Exception as e:
            logger.error(f"Failed to play wav bytes: {e}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    @staticmethod
    def play_pcm_bytes(pcm_bytes: bytes, sample_rate: int = 24000, channels: int = 1, sample_width: int = 2):
        """Play raw PCM16 bytes by wrapping in a temporary WAV container."""
        if not pcm_bytes:
            return
        try:
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(sample_width)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm_bytes)
            AudioPlayer.play_wav_bytes(wav_buffer.getvalue())
        except Exception as e:
            logger.error(f"Failed to play PCM bytes: {e}")

    @staticmethod
    def play_file(file_path: str):
        """Play audio file using aplay (Linux/RPi) or afplay (macOS)."""
        if not os.path.exists(file_path):
            return

        # 1. Try aplay on Raspberry Pi / Linux
        try:
            res = subprocess.run(["which", "aplay"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode == 0:
                subprocess.run(["aplay", "-q", file_path], check=False)
                return
        except Exception:
            pass

        # 2. Try afplay on macOS
        try:
            res = subprocess.run(["which", "afplay"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode == 0:
                subprocess.run(["afplay", file_path], check=False)
                return
        except Exception:
            pass

        # 3. Fallback to ffplay or mpg123
        for player in ["ffplay", "mpv", "mpg123"]:
            try:
                res = subprocess.run(["which", player], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if res.returncode == 0:
                    cmd = [player, "-nodisp", "-autoexit", file_path] if player == "ffplay" else [player, file_path]
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                    return
            except Exception:
                continue


class MicrophoneWorker:
    """
    Background microphone listener.
    Supports:
    1. SpeechRecognition (PyAudio / ALSA)
    2. Zero-dependency arecord fallback (ALSA standard tool on Raspberry Pi)
    Publishes raw WAV bytes to 'voice.input.audio' on the EventBus.
    """

    def __init__(
        self,
        bus: EventBus,
        device: Optional[str] = None,
        sample_rate: int = 16000,
        energy_threshold: int = 2000,
        pause_threshold: float = 0.8,
        dynamic_energy_threshold: bool = True,
    ):
        self.bus = bus
        self.device = device or os.getenv("AUDIO_INPUT_DEVICE", "plughw:1,0")
        self.sample_rate = sample_rate
        self.energy_threshold = energy_threshold
        self.pause_threshold = pause_threshold
        self.dynamic_energy_threshold = dynamic_energy_threshold

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._backend = "none"
        self._recognizer = None
        self._microphone = None

        self._init_audio_input()

    def _init_audio_input(self):
        # 1. Try SpeechRecognition
        try:
            import speech_recognition as sr
            self._recognizer = sr.Recognizer()
            self._recognizer.energy_threshold = self.energy_threshold
            self._recognizer.pause_threshold = self.pause_threshold
            self._recognizer.dynamic_energy_threshold = self.dynamic_energy_threshold
            self._microphone = sr.Microphone(sample_rate=self.sample_rate)
            self._backend = "speech_recognition"
            logger.info("MicrophoneWorker initialized with speech_recognition backend.")
            return
        except Exception as e:
            logger.debug(f"speech_recognition backend not available ({e}), falling back to arecord...")

        # 2. Try ALSA arecord (built into Raspberry Pi OS)
        try:
            res = subprocess.run(["which", "arecord"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode == 0:
                self._backend = "arecord"
                logger.info("MicrophoneWorker initialized with ALSA arecord backend.")
                return
        except Exception as e:
            logger.debug(f"arecord check failed: {e}")

        logger.warning("No microphone backend available (install SpeechRecognition or ALSA arecord).")
        self._backend = "none"

    def is_available(self) -> bool:
        return self._backend != "none"

    def start(self):
        """Start background microphone listening loop."""
        if self._running or not self.is_available():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, name="MicrophoneWorker", daemon=True)
        self._thread.start()
        logger.info(f"Microphone listening worker started (backend: {self._backend}).")

    def stop(self):
        """Stop background microphone listening loop."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.info("Microphone listening worker stopped.")

    def _run_loop(self):
        if self._backend == "speech_recognition":
            self._listen_sr_loop()
        elif self._backend == "arecord":
            self._listen_arecord_loop()

    def _listen_sr_loop(self):
        import speech_recognition as sr
        while self._running:
            try:
                with self._microphone as source:
                    if self.dynamic_energy_threshold:
                        self._recognizer.adjust_for_ambient_noise(source, duration=0.5)

                    audio_data = self._recognizer.listen(source, timeout=3.0, phrase_time_limit=10.0)

                    if audio_data and self._running:
                        wav_bytes = audio_data.get_wav_data()
                        if wav_bytes and len(wav_bytes) > 2000:
                            logger.info(f"Captured voice utterance ({len(wav_bytes)} bytes), publishing to ASR...")
                            self.bus.publish("voice.input.audio", {"audio": wav_bytes})
            except sr.WaitTimeoutError:
                continue
            except Exception as e:
                logger.debug(f"SR capture cycle exception: {e}")
                time.sleep(0.5)

    @staticmethod
    def _compute_rms_energy(audio_bytes: bytes) -> float:
        """Calculate RMS (root mean square) volume energy of 16-bit PCM/WAV data."""
        if not audio_bytes or len(audio_bytes) < 44:
            return 0.0
        try:
            # Check for standard WAV header
            if audio_bytes[:4] == b'RIFF':
                pcm_data = audio_bytes[44:]
            else:
                pcm_data = audio_bytes

            count = len(pcm_data) // 2
            if count == 0:
                return 0.0

            import struct
            shorts = struct.unpack(f"<{count}h", pcm_data[:count*2])
            sum_squares = sum(s * s for s in shorts)
            rms = (sum_squares / count) ** 0.5
            return rms
        except Exception:
            return 0.0

    def _listen_arecord_loop(self):
        """Zero-dependency chunk-based capture on Raspberry Pi using arecord with energy VAD filtering."""
        tmp_wav = os.path.join(tempfile.gettempdir(), "pidog_mic_chunk.wav")
        while self._running:
            try:
                # Use configured device (e.g. plughw:1,0 or default)
                cmd = [
                    "arecord",
                    "-q",
                    "-D", str(self.device),
                    "-f", "S16_LE",
                    "-r", str(self.sample_rate),
                    "-c", "1",
                    "-d", "3",
                    tmp_wav
                ]
                proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                if proc.returncode == 0 and os.path.exists(tmp_wav) and self._running:
                    file_size = os.path.getsize(tmp_wav)
                    if file_size > 4000:
                        with open(tmp_wav, "rb") as f:
                            wav_bytes = f.read()

                        # Check RMS energy level (VAD) to ignore complete silence / low background noise
                        rms = self._compute_rms_energy(wav_bytes)
                        # Threshold for audible speech vs silence (relaxed to 150 for Pi Zero mic gain)
                        if rms >= 150:
                            logger.info(f"Captured voice utterance via arecord ({len(wav_bytes)} bytes, energy={rms:.1f}), publishing to ASR...")
                            self.bus.publish("voice.input.audio", {"audio": wav_bytes})
                        else:
                            logger.debug(f"Ignored silent audio chunk ({len(wav_bytes)} bytes, energy={rms:.1f} < 150)")
            except Exception as e:
                logger.debug(f"Arecord capture exception: {e}")
                time.sleep(0.5)
            finally:
                if os.path.exists(tmp_wav):
                    try:
                        os.remove(tmp_wav)
                    except Exception:
                        pass
