"""Unit tests for SoundLibrary, ClapDetector, ApproachBehavior and SensorContext."""
import math
import os
import struct
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pidog.companion.core.event_bus import EventBus
from pidog.companion.behavior.sound_library import SoundLibrary, BUILTIN_SOUND_DIR
from pidog.companion.behavior.approach import ApproachBehavior, normalize_angle
from pidog.companion.hardware.clap_detector import ClapDetector
from pidog.companion.core.sensor_context import SensorContext
from pidog.companion.behavior.state import PetState


def make_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    """Wrap raw PCM into a minimal WAV byte string."""
    import wave
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def synthesize_pattern(sample_rate: int, bursts: list, duration_s: float = 2.0,
                       burst_amp: int = 20000) -> bytes:
    """Synthesize PCM with loud transient bursts [(start_s, len_s), ...] over silence."""
    total = int(sample_rate * duration_s)
    samples = [0] * total
    for start_s, len_s in bursts:
        s0 = int(start_s * sample_rate)
        s1 = min(total, int((start_s + len_s) * sample_rate))
        for i in range(s0, s1):
            # decaying transient like a clap
            decay = max(0.0, 1.0 - (i - s0) / max(1, (s1 - s0)))
            samples[i] = int(burst_amp * decay * math.sin(2 * math.pi * 1000 * i / sample_rate))
    return struct.pack(f"<{len(samples)}h", *samples)


class TestSoundLibrary(unittest.TestCase):
    def setUp(self):
        self.lib = SoundLibrary(cooldown=10.0)  # long cooldown for deterministic tests

    def test_builtin_tags_resolved_against_repo_sounds(self):
        for tag in ("coquettish", "enchanted", "happy_bark", "pant", "confused", "growl", "whine"):
            self.assertTrue(self.lib.is_known_tag(tag), f"tag {tag} should be known")

    def test_resolve_returns_existing_file(self):
        for tag in self.lib.available_tags():
            path = self.lib.resolve(tag, force=True)
            self.assertIsNotNone(path)
            self.assertTrue(os.path.isfile(path))

    def test_variant_random_selection(self):
        paths = {self.lib.resolve("confused", force=True) for _ in range(20)}
        self.assertGreater(len(paths), 1, "confused has 3 variants, random choice should vary")

    def test_cooldown_suppresses_repeat(self):
        first = self.lib.resolve("happy_bark", force=True)
        self.assertIsNotNone(first)
        second = self.lib.resolve("happy_bark")  # within cooldown window
        self.assertIsNone(second)

    def test_unknown_tag(self):
        self.assertFalse(self.lib.is_known_tag("nonexistent_tag_xyz"))
        self.assertIsNone(self.lib.resolve("nonexistent_tag_xyz"))

    def test_user_dir_auto_registration(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            user_dir = Path(td)
            # A new custom variant under an existing tag
            (user_dir / "coquettish_9.mp3").write_bytes(b"fake")
            # A brand new tag
            (user_dir / "giggle.mp3").write_bytes(b"fake")
            lib = SoundLibrary(sound_dirs=[BUILTIN_SOUND_DIR, user_dir], cooldown=0.0)
            self.assertTrue(lib.is_known_tag("giggle"))
            # resolve returns one of coquettish variants incl. the user one
            seen = {lib.resolve("coquettish", force=True) for _ in range(40)}
            self.assertIn(str((user_dir / "coquettish_9.mp3").resolve()), seen)

    def test_alias_tags_map_to_same_files(self):
        # coquettish and enchanted both resolve to woohoo
        c = self.lib.resolve("coquettish", force=True)
        e = self.lib.resolve("enchanted", force=True)
        self.assertEqual(c, e)


class TestClapDetector(unittest.TestCase):
    def setUp(self):
        self.detector = ClapDetector(sample_rate=16000)

    def test_single_clap(self):
        pcm = synthesize_pattern(16000, [(0.3, 0.08)])
        self.assertEqual(self.detector.analyze(make_wav(pcm)), 1)

    def test_double_clap(self):
        pcm = synthesize_pattern(16000, [(0.3, 0.06), (0.6, 0.06)])
        self.assertEqual(self.detector.analyze(make_wav(pcm)), 2)

    def test_triple_clap(self):
        pcm = synthesize_pattern(16000, [(0.2, 0.06), (0.5, 0.06), (0.8, 0.06)])
        self.assertEqual(self.detector.analyze(make_wav(pcm)), 3)

    def test_speech_not_detected_as_claps(self):
        # Sustained sound over ~1.2s (speech-like)
        pcm = synthesize_pattern(16000, [(0.2, 1.2)], burst_amp=12000)
        self.assertIsNone(self.detector.analyze(make_wav(pcm)))

    def test_too_many_bursts_rejected(self):
        bursts = [(0.1 * i, 0.05) for i in range(5)]
        pcm = synthesize_pattern(16000, bursts)
        self.assertIsNone(self.detector.analyze(make_wav(pcm)))

    def test_silence_rejected(self):
        pcm = synthesize_pattern(16000, [])
        self.assertIsNone(self.detector.analyze(make_wav(pcm)))

    def test_short_audio_rejected(self):
        self.assertIsNone(self.detector.analyze(b"\x00" * 100))


class TestApproachBehavior(unittest.TestCase):
    def test_normalize_angle(self):
        self.assertEqual(normalize_angle(0), 0.0)
        self.assertEqual(normalize_angle(90), 90.0)
        self.assertEqual(normalize_angle(180), 180.0)
        self.assertEqual(normalize_angle(270), -90.0)
        self.assertEqual(normalize_angle(355), -5.0)

    def test_approach_publishes_turn_and_forward(self):
        dog = MagicMock()
        dog.legs_action_buffer = []
        bus = EventBus()
        behavior = ApproachBehavior(dog, bus, forward_steps=3)
        try:
            bus.publish("behavior.approach", {"angle": 90.0})
            # wait for the approach thread to finish
            if behavior._thread:
                behavior._thread.join(timeout=3.0)

            actions = [c[0][0] for c in dog.do_action.call_args_list]
            self.assertIn("turn_right", actions)  # 90 deg = right side
            self.assertIn("forward", actions)
            # turn step count: 90 / 22.5 = 4
            turn_call = [c for c in dog.do_action.call_args_list if c[0][0] == "turn_right"][0]
            self.assertEqual(turn_call[1]["step_count"], 4)
        finally:
            behavior.close()

    def test_approach_left_side(self):
        dog = MagicMock()
        dog.legs_action_buffer = []
        bus = EventBus()
        behavior = ApproachBehavior(dog, bus)
        try:
            bus.publish("behavior.approach", {"angle": 300.0})  # 300 = -60 left
            if behavior._thread:
                behavior._thread.join(timeout=3.0)
            actions = [c[0][0] for c in dog.do_action.call_args_list]
            self.assertIn("turn_left", actions)
        finally:
            behavior.close()

    def test_front_angle_skips_turn(self):
        dog = MagicMock()
        dog.legs_action_buffer = []
        bus = EventBus()
        behavior = ApproachBehavior(dog, bus)
        try:
            bus.publish("behavior.approach", {"angle": 10.0})  # within deadzone
            if behavior._thread:
                behavior._thread.join(timeout=3.0)
            actions = [c[0][0] for c in dog.do_action.call_args_list]
            self.assertEqual(actions, ["forward"])
        finally:
            behavior.close()

    def test_obstacle_aborts(self):
        dog = MagicMock()
        # Non-empty leg buffer keeps _wait_legs waiting, giving the obstacle
        # event time to interrupt the sequence mid-turn.
        dog.legs_action_buffer = [1, 2, 3]
        bus = EventBus()
        behavior = ApproachBehavior(dog, bus)
        try:
            # simulate in-flight approach then obstacle
            bus.publish("behavior.approach", {"angle": 90.0})
            time.sleep(0.2)
            bus.publish("sensor.ultrasonic.obstacle", {"distance": 10.0})
            if behavior._thread:
                behavior._thread.join(timeout=3.0)
            self.assertTrue(behavior._abort.is_set())
            dog.body_stop.assert_called()
        finally:
            behavior.close()


class TestSensorContext(unittest.TestCase):
    def test_summary_includes_events_and_direction(self):
        bus = EventBus()
        state = PetState()
        ctx = SensorContext(bus, state=state)
        try:
            bus.publish("sensor.touch.head", {"type": "touch"})
            bus.publish("sensor.touch.head", {"type": "touch"})
            bus.publish("sensor.sound.direction", {"angle": 120})
            bus.publish("sensor.ultrasonic.distance", {"distance": 30.0})

            summary = ctx.summary()
            self.assertIn("摸头", summary)
            self.assertIn("120", summary)
            self.assertIn("30", summary)
            self.assertIn("亲密度", summary)

            self.assertEqual(ctx.last_sound_direction, 120.0)
        finally:
            ctx.close()

    def test_stale_direction_is_none(self):
        bus = EventBus()
        ctx = SensorContext(bus, sound_direction_max_age=0.05)
        try:
            bus.publish("sensor.sound.direction", {"angle": 90})
            time.sleep(0.15)  # let the reading go stale
            self.assertIsNone(ctx.last_sound_direction)
        finally:
            ctx.close()

    def test_empty_summary(self):
        bus = EventBus()
        ctx = SensorContext(bus)
        try:
            self.assertEqual(ctx.summary(), "")
        finally:
            ctx.close()


if __name__ == "__main__":
    unittest.main()
