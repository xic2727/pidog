"""Clap (hand-clap) pattern detector for raw PCM/WAV audio.

Distinguishes short transient bursts (hand claps) from sustained speech so
clap-count commands can be handled at reflex level with near-zero latency:

    1 clap  -> sit
    2 claps -> spin around
    3 claps -> come to the owner

Detection heuristics on a 16-bit mono PCM window:
- Split audio into short frames (default 20ms) and compute per-frame RMS.
- A burst is a run of frames above `clap_threshold` whose total duration is
  between `min_burst_ms` and `max_burst_ms` (claps are transient; speech is not).
- Bursts must be separated by quiet gaps of at least `min_gap_ms`.
- The overall duty cycle (sound / total duration) must stay low, otherwise the
  audio is speech or noise rather than isolated claps.
"""
import logging
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class ClapDetector:
    """Stateless analyzer: feed it a WAV/PCM chunk, get back a clap count."""

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = 20,
        clap_threshold: float = 600.0,
        min_burst_ms: int = 20,
        max_burst_ms: int = 200,
        min_gap_ms: int = 120,
        max_claps: int = 3,
        max_duty_cycle: float = 0.4,
    ):
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.clap_threshold = clap_threshold
        self.min_burst_ms = min_burst_ms
        self.max_burst_ms = max_burst_ms
        self.min_gap_ms = min_gap_ms
        self.max_claps = max_claps
        self.max_duty_cycle = max_duty_cycle

    # ------------------------------------------------------------------ #
    # Analysis helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_pcm(wav_bytes: bytes) -> bytes:
        """Strip the standard 44-byte WAV header if present."""
        if len(wav_bytes) > 44 and wav_bytes[:4] == b"RIFF" and wav_bytes[8:12] == b"WAVE":
            return wav_bytes[44:]
        return wav_bytes

    def _frame_rms(self, pcm_bytes: bytes) -> List[float]:
        """Compute per-frame RMS energy of 16-bit little-endian mono PCM."""
        import struct

        frame_samples = max(1, int(self.sample_rate * self.frame_ms / 1000.0))
        frame_bytes = frame_samples * 2
        energies = []
        for offset in range(0, len(pcm_bytes) - frame_bytes + 1, frame_bytes):
            chunk = pcm_bytes[offset:offset + frame_bytes]
            count = len(chunk) // 2
            shorts = struct.unpack(f"<{count}h", chunk[: count * 2])
            energies.append((sum(s * s for s in shorts) / count) ** 0.5)
        return energies

    def _find_bursts(self, energies: List[float]) -> List[Tuple[int, int]]:
        """Find (start_frame, end_frame) runs above the clap threshold."""
        bursts = []
        start = None
        for i, e in enumerate(energies):
            if e >= self.clap_threshold:
                if start is None:
                    start = i
            else:
                if start is not None:
                    bursts.append((start, i))
                    start = None
        if start is not None:
            bursts.append((start, len(energies)))
        return bursts

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def analyze(self, wav_bytes: bytes) -> Optional[int]:
        """
        Analyze an audio chunk for a clap pattern.

        :param wav_bytes: WAV file bytes or raw 16-bit mono PCM data
        :return: clap count (1..max_claps) if a clap pattern is detected,
                 otherwise None (silence, speech, or noise).
        """
        pcm = self._extract_pcm(wav_bytes)
        if len(pcm) < self.sample_rate * 2 * 0.2:  # ignore < 0.2s of audio
            return None

        energies = self._frame_rms(pcm)
        if not energies:
            return None

        bursts = self._find_bursts(energies)
        if not bursts or len(bursts) > self.max_claps:
            return None

        total_duration_ms = len(energies) * self.frame_ms
        on_time_ms = 0
        claps = []
        prev_end_ms = None
        for start_f, end_f in bursts:
            duration_ms = (end_f - start_f) * self.frame_ms
            if duration_ms < self.min_burst_ms or duration_ms > self.max_burst_ms:
                return None  # too short (click) or too long (speech)
            start_ms = start_f * self.frame_ms
            if prev_end_ms is not None:
                gap_ms = start_ms - prev_end_ms
                if gap_ms < self.min_gap_ms:
                    return None  # bursts merge into continuous sound -> speech
            prev_end_ms = end_f * self.frame_ms
            on_time_ms += duration_ms
            claps.append((start_ms, duration_ms))

        # Duty-cycle guard: claps are sparse transients within the chunk
        if on_time_ms / total_duration_ms > self.max_duty_cycle:
            return None

        return len(claps)
