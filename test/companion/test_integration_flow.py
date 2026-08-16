"""Integration tests for the Embodied Pet Companion end-to-end pipeline."""
import time
import unittest
from unittest.mock import MagicMock, patch

from pidog.companion.config import CompanionConfig, VLMConfig, ASRConfig, TTSConfig
from pidog.companion.core.event_bus import EventBus
from pidog.companion.behavior.state import PetState, MoodType
from pidog.companion.behavior.behavior_engine import BehaviorEngine
from pidog.companion.hardware.sensor_worker import SensorWorker
from pidog.companion.hardware.emotion_expressor import EmotionExpressor
from pidog.companion.hardware.camera_helper import CameraHelper
from pidog.companion.core.orchestrator import CompanionOrchestrator


class TestCompanionIntegrationFlow(unittest.TestCase):
    """
    End-to-end integration test verifying interactions between:
    - SensorWorker
    - EventBus
    - BehaviorEngine & PetState
    - CompanionOrchestrator
    - EmotionExpressor
    - ASR / TTS / VLM adapters
    """

    def setUp(self):
        self.bus = EventBus()
        self.config = CompanionConfig()
        self.mock_dog = MagicMock()

        # Mock adapters
        self.mock_vlm = MagicMock()
        self.mock_vlm.is_available.return_value = True
        self.mock_asr = MagicMock()
        self.mock_asr.is_available.return_value = True
        self.mock_tts = MagicMock()
        self.mock_tts.is_available.return_value = True
        self.mock_camera = MagicMock(spec=CameraHelper)

        # Pet State & Behavior Engine
        self.state = PetState()
        self.behavior_engine = BehaviorEngine(self.state, self.bus, interval=0.05)

        # Sensor Worker & Emotion Expressor
        self.sensor_worker = SensorWorker(self.mock_dog, self.bus, poll_interval=0.01)
        self.emotion_expressor = EmotionExpressor(self.mock_dog, self.bus)

        # Orchestrator
        self.orchestrator = CompanionOrchestrator(
            config=self.config,
            bus=self.bus,
            vlm=self.mock_vlm,
            asr=self.mock_asr,
            tts=self.mock_tts,
            camera=self.mock_camera,
        )

    def tearDown(self):
        self.sensor_worker.stop()
        self.behavior_engine.stop()
        self.orchestrator.stop()
        self.emotion_expressor.close()

    def test_touch_to_behavior_to_expression_flow(self):
        """
        Touch on head -> SensorWorker publishes -> BehaviorEngine updates PetState
        -> Publishes actuator.express -> EmotionExpressor moves tail and sets RGB.
        """
        initial_intimacy = self.state.intimacy
        self.mock_dog.dual_touch.read.return_value = 'R'  # Front touch (Head)

        # Start background workers
        self.behavior_engine.start()
        self.sensor_worker.start()

        time.sleep(0.1)

        # Verify state updated
        self.assertGreater(self.state.intimacy, initial_intimacy)
        self.assertEqual(self.state.mood, MoodType.HAPPY)

        # Verify hardware actuation
        self.mock_dog.do_action.assert_called_with("wag_tail", speed=50)
        self.mock_dog.rgb_strip.set_mode.assert_called_with("breath", "yellow", bps=1.5)

    def test_voice_dialogue_to_vlm_to_tts_flow(self):
        """
        Voice audio input -> Orchestrator transcribes with ASR
        -> Passes text to VLM -> parses [emotion:happy][action:wag_tail]
        -> synthesizes speech via TTS and drives dog actuation.
        """
        self.mock_asr.transcribe.return_value = "小狗你好呀"
        self.mock_vlm.generate.return_value = "[emotion:happy][action:wag_tail] 汪汪！主人好！"
        self.mock_tts.synthesize.return_value = b"audio_wav_data"

        dialogue_responses = []
        tts_events = []
        self.bus.subscribe("dialogue.response", lambda d: dialogue_responses.append(d))
        self.bus.subscribe("tts.audio.ready", lambda d: tts_events.append(d))

        self.orchestrator.start()

        # Publish voice audio event
        self.bus.publish("voice.input.audio", b"pcm_audio_bytes")

        time.sleep(0.2)

        # Verify dialogue response
        self.assertEqual(len(dialogue_responses), 1)
        resp = dialogue_responses[0]
        self.assertEqual(resp["clean_text"], "汪汪！主人好！")
        self.assertEqual(resp["action"], "wag_tail")
        self.assertEqual(resp["emotion"], "happy")

        # Verify TTS generated
        self.assertEqual(len(tts_events), 1)
        self.assertEqual(tts_events[0]["audio"], b"audio_wav_data")

        # Verify physical expression
        self.mock_dog.do_action.assert_called_with("wag_tail", speed=50)
        self.mock_dog.rgb_strip.set_mode.assert_called_with("breath", "yellow", bps=1.5)

    def test_visual_dialogue_multimodal_flow(self):
        """
        Visual query ('看看这是什么') -> triggers CameraHelper capture
        -> VLM receives image + prompt -> triggers head tilt & curious emotion.
        """
        self.mock_camera.capture_jpeg.return_value = b"fake_jpeg_frame"
        self.mock_vlm.generate.return_value = "[emotion:curious][action:tilt_head] 我看到了一本书！"
        self.mock_tts.synthesize.return_value = b"book_tts_audio"

        self.orchestrator.start()
        self.bus.publish("voice.input.text", {"text": "看看这是什么？"})

        time.sleep(0.2)

        # Verify camera was captured and passed to VLM
        self.mock_camera.capture_jpeg.assert_called_once()
        self.mock_vlm.generate.assert_called_once()
        call_kwargs = self.mock_vlm.generate.call_args[1]
        self.assertEqual(call_kwargs["image_data"], b"fake_jpeg_frame")

        # Verify hardware action (tilt_head alias resolved to tilting_head)
        self.mock_dog.do_action.assert_called_with("tilting_head", speed=50)

    def test_lifecycle_and_graceful_shutdown(self):
        """Test complete system start and safe shutdown without hanging or leaking threads."""
        self.behavior_engine.start()
        self.sensor_worker.start()
        self.orchestrator.start()

        self.assertTrue(self.behavior_engine._running)
        self.assertTrue(self.sensor_worker._running)
        self.assertTrue(self.orchestrator._running)

        # Perform clean shutdown
        self.sensor_worker.stop()
        self.behavior_engine.stop()
        self.orchestrator.stop()
        self.emotion_expressor.close()

        self.assertFalse(self.behavior_engine._running)
        self.assertFalse(self.sensor_worker._running)
        self.assertFalse(self.orchestrator._running)


if __name__ == '__main__':
    unittest.main()
