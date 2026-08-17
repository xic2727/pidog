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
from .clap_detector import ClapDetector

logger = logging.getLogger(__name__)


class AudioPlayer:
    """Handles audio playback for TTS and sound effects on Raspberry Pi and development machines."""

    _is_playing: bool = False
    _playback_lock = threading.Lock()

    @classmethod
    def is_playing(cls) -> bool:
        """Check if audio is currently being played back."""
        with cls._playback_lock:
            return cls._is_playing

    @classmethod
    def _set_playing(cls, playing: bool):
        with cls._playback_lock:
            cls._is_playing = playing

    @classmethod
    def play_wav_bytes(cls, audio_bytes: bytes):
        """Play WAV bytes using available system audio backend (aplay, afplay, ffplay)."""
        if not audio_bytes:
            return

        cls._set_playing(True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(audio_bytes)
                tmp_path = f.name

            cls.play_file(tmp_path)
        except Exception as e:
            logger.error(f"Failed to play wav bytes: {e}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            cls._set_playing(False)

    @classmethod
    def play_pcm_bytes(cls, pcm_bytes: bytes, sample_rate: int = 24000, channels: int = 1, sample_width: int = 2):
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
            cls.play_wav_bytes(wav_buffer.getvalue())
        except Exception as e:
            logger.error(f"Failed to play PCM bytes: {e}")

    @classmethod
    def play_file(cls, file_path: str):
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
        enable_clap_detection: bool = True,
    ):
        self.bus = bus
        self.device = device or os.getenv("AUDIO_INPUT_DEVICE", "plughw:1,0")
        self.sample_rate = sample_rate
        self.energy_threshold = energy_threshold
        self.pause_threshold = pause_threshold
        self.dynamic_energy_threshold = dynamic_energy_threshold
        # Reflex-level clap commands: 1/2/3 claps handled without LLM roundtrip
        self.enable_clap_detection = enable_clap_detection
        self.clap_detector = ClapDetector(sample_rate=sample_rate) if enable_clap_detection else None

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._backend = "none"
        self._recognizer = None
        self._microphone = None

        self._init_audio_input()

    def _init_audio_input(self):
        # Prefer ALSA arecord on Linux/RPi when specific ALSA device is configured
        # Or if SpeechRecognition is not installed / PyAudio fails
        logger.info(f"Configuring audio input device: '{self.device}'")

        # 1. Check arecord first if we are on Linux/RPi or if SpeechRecognition fails with custom ALSA devices
        has_arecord = False
        try:
            res = subprocess.run(["which", "arecord"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            has_arecord = (res.returncode == 0)
        except Exception:
            has_arecord = False

        # If on Raspberry Pi / Linux and device is explicitly set (like plughw:1,0) or arecord exists
        if has_arecord:
            self._backend = "arecord"
            logger.info(f"MicrophoneWorker initialized with ALSA arecord backend (device: {self.device}).")
            return

        # 2. Try SpeechRecognition
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
            logger.debug(f"speech_recognition backend not available ({e})")

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
                if AudioPlayer.is_playing():
                    time.sleep(0.5)
                    continue

                with self._microphone as source:
                    if self.dynamic_energy_threshold:
                        self._recognizer.adjust_for_ambient_noise(source, duration=0.5)

                    audio_data = self._recognizer.listen(source, timeout=3.0, phrase_time_limit=10.0)

                    if AudioPlayer.is_playing():
                        continue

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
                # If dog is currently speaking/playing audio, skip recording to prevent self-hearing acoustic feedback loop
                if AudioPlayer.is_playing():
                    logger.debug("Audio playback in progress, suppressing mic capture...")
                    time.sleep(0.5)
                    continue

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

                # Re-check if audio started playing during recording
                if AudioPlayer.is_playing():
                    logger.debug("Playback occurred during recording chunk, discarding chunk.")
                    continue

                if proc.returncode == 0 and os.path.exists(tmp_wav) and self._running:
                    file_size = os.path.getsize(tmp_wav)
                    if file_size > 4000:
                        with open(tmp_wav, "rb") as f:
                            wav_bytes = f.read()

                        # Check RMS energy level (VAD) to ignore complete silence / low background noise
                        rms = self._compute_rms_energy(wav_bytes)
                        logger.info(f"[Mic Audio Captured] size={len(wav_bytes)} bytes, energy={rms:.1f}")

                        # Clap commands take priority: transient burst patterns are
                        # reflex commands, not speech, so skip ASR when matched.
                        if self.clap_detector:
                            try:
                                clap_count = self.clap_detector.analyze(wav_bytes)
                            except Exception as e:
                                logger.debug(f"Clap detection error: {e}")
                                clap_count = None
                            if clap_count:
                                logger.info(f"Clap pattern detected: {clap_count} clap(s)")
                                self.bus.publish("sensor.clap.detected", {"count": clap_count, "energy": rms})
                                continue

                        # Threshold for audible speech vs silence
                        if rms >= 50:
                            logger.info(f"Publishing voice utterance to ASR (energy={rms:.1f} >= 50)...")
                            self.bus.publish("voice.input.audio", {"audio": wav_bytes})
                        else:
                            logger.info(f"Skipped silent chunk (energy={rms:.1f} < 50)")
            except Exception as e:
                logger.debug(f"Arecord capture exception: {e}")
                time.sleep(0.5)
            finally:
                if os.path.exists(tmp_wav):
                    try:
                        os.remove(tmp_wav)
                    except Exception:
                        pass
