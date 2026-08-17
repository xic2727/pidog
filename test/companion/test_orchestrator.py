"""Unit tests for ConversationContext, tag extraction, visual intent detection, and CompanionOrchestrator."""
import time
import threading
import unittest
from unittest.mock import MagicMock, patch

from pidog.companion.core.context import ConversationContext
from pidog.companion.core.orchestrator import CompanionOrchestrator
from pidog.companion.core.event_bus import EventBus
from pidog.companion.config import CompanionConfig, VLMConfig, ASRConfig, TTSConfig


class TestConversationContext(unittest.TestCase):
    def setUp(self):
        self.context = ConversationContext(max_history=4, system_prompt="You are a dog.")

    def test_add_message_and_history(self):
        self.context.add_user_message("Hello")
        self.context.add_assistant_message("Woof!")
        history = self.context.get_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0], {"role": "user", "content": "Hello"})
        self.assertEqual(history[1], {"role": "assistant", "content": "Woof!"})

    def test_sliding_window_trimming(self):
        for i in range(10):
            self.context.add_user_message(f"Msg {i}")

        self.assertEqual(len(self.context), 4)
        history = self.context.get_history()
        self.assertEqual(len(history), 4)
        self.assertEqual(history[0]["content"], "Msg 6")
        self.assertEqual(history[3]["content"], "Msg 9")

    def test_clear_and_metadata(self):
        self.context.add_user_message("Test")
        self.context.set_metadata("session_id", "12345")
        self.assertEqual(self.context.get_metadata("session_id"), "12345")
        self.assertIsNone(self.context.get_metadata("none_key"))

        self.context.clear()
        self.assertEqual(len(self.context), 0)
        self.assertIsNone(self.context.get_metadata("session_id"))

    def test_thread_safety(self):
        context = ConversationContext(max_history=100)
        threads = []

        def worker(tid):
            for i in range(50):
                context.add_user_message(f"{tid}-{i}")
                context.set_metadata(f"meta-{tid}", i)

        for t in range(5):
            th = threading.Thread(target=worker, args=(t,))
            threads.append(th)
            th.start()

        for th in threads:
            th.join()

        self.assertLessEqual(len(context), 100)
        self.assertEqual(len(context.get_history()), len(context))


class TestTagExtraction(unittest.TestCase):
    def test_standard_tags(self):
        text = "[emotion:happy][action:wag_tail] 你好呀，主人！"
        action, emotion, clean_text = CompanionOrchestrator.extract_tags(text)
        self.assertEqual(action, "wag_tail")
        self.assertEqual(emotion, "happy")
        self.assertEqual(clean_text, "你好呀，主人！")

    def test_tags_with_spaces_and_short_forms(self):
        text = "[emo: excited ] [act: sit_down] 开饭啦！"
        action, emotion, clean_text = CompanionOrchestrator.extract_tags(text)
        self.assertEqual(action, "sit_down")
        self.assertEqual(emotion, "excited")
        self.assertEqual(clean_text, "开饭啦！")

    def test_xml_style_tags(self):
        text = "<emotion>curious</emotion><action>tilt_head</action>你在做什么呢？"
        action, emotion, clean_text = CompanionOrchestrator.extract_tags(text)
        self.assertEqual(action, "tilt_head")
        self.assertEqual(emotion, "curious")
        self.assertEqual(clean_text, "你在做什么呢？")

    def test_mixed_and_embedded_tags(self):
        text = "主人[action:bark]，我很想你[emotion:sad]啊！"
        action, emotion, clean_text = CompanionOrchestrator.extract_tags(text)
        self.assertEqual(action, "bark")
        self.assertEqual(emotion, "sad")
        self.assertEqual(clean_text, "主人，我很想你啊！")

    def test_no_tags(self):
        text = "今天天气真好。"
        action, emotion, clean_text = CompanionOrchestrator.extract_tags(text)
        self.assertIsNone(action)
        self.assertIsNone(emotion)
        self.assertEqual(clean_text, "今天天气真好。")

    def test_empty_string(self):
        action, emotion, clean_text = CompanionOrchestrator.extract_tags("")
        self.assertIsNone(action)
        self.assertIsNone(emotion)
        self.assertEqual(clean_text, "")


class TestVisualIntentDetection(unittest.TestCase):
    def setUp(self):
        self.orchestrator = CompanionOrchestrator()

    def test_detect_visual_chinese(self):
        self.assertTrue(self.orchestrator.detect_visual_intent("看看这是什么东西？"))
        self.assertTrue(self.orchestrator.detect_visual_intent("你看到了什么？"))
        self.assertTrue(self.orchestrator.detect_visual_intent("帮我找找钥匙"))
        self.assertTrue(self.orchestrator.detect_visual_intent("瞧瞧前方"))
        self.assertFalse(self.orchestrator.detect_visual_intent("今天几点了？"))
        self.assertFalse(self.orchestrator.detect_visual_intent("给我讲个故事"))

    def test_detect_visual_english(self):
        self.assertTrue(self.orchestrator.detect_visual_intent("What is this on the table?"))
        self.assertTrue(self.orchestrator.detect_visual_intent("Look at me!"))
        self.assertTrue(self.orchestrator.detect_visual_intent("Can you see the ball?"))
        self.assertTrue(self.orchestrator.detect_visual_intent("Take a photo"))
        self.assertFalse(self.orchestrator.detect_visual_intent("How are you today?"))


class TestCompanionOrchestrator(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.mock_vlm = MagicMock()
        self.mock_vlm.is_available.return_value = True
        self.mock_asr = MagicMock()
        self.mock_asr.is_available.return_value = True
        self.mock_tts = MagicMock()
        self.mock_tts.is_available.return_value = True
        self.mock_camera = MagicMock()

        self.config = CompanionConfig(voice_mode="tts")  # legacy talking-dog mode
        self.orchestrator = CompanionOrchestrator(
            config=self.config,
            bus=self.bus,
            vlm=self.mock_vlm,
            asr=self.mock_asr,
            tts=self.mock_tts,
            camera=self.mock_camera,
        )

    def tearDown(self):
        self.orchestrator.stop()

    def test_process_dialogue_text_only(self):
        self.mock_vlm.generate.return_value = "[emotion:happy][action:wag_tail] 汪！你好！"
        self.mock_tts.synthesize.return_value = b"fake_tts_wav"

        express_events = []
        dialogue_events = []
        tts_events = []
        self.bus.subscribe("actuator.express", lambda d: express_events.append(d))
        self.bus.subscribe("dialogue.response", lambda d: dialogue_events.append(d))
        self.bus.subscribe("tts.audio.ready", lambda d: tts_events.append(d))

        res = self.orchestrator.process_dialogue("你好机器狗")

        self.assertTrue(res["success"])
        self.assertEqual(res["clean_text"], "汪！你好！")
        self.assertEqual(res["action"], "wag_tail")
        self.assertEqual(res["emotion"], "happy")
        self.assertFalse(res["has_image"])

        # Check VLM call
        self.mock_vlm.generate.assert_called_once()
        args, kwargs = self.mock_vlm.generate.call_args
        self.assertEqual(kwargs["prompt"], "你好机器狗")
        self.assertIsNone(kwargs["image_data"])

        # Check published events
        self.assertEqual(len(dialogue_events), 1)
        self.assertEqual(len(express_events), 1)
        self.assertEqual(express_events[0]["emotion"], "happy")
        self.assertEqual(express_events[0]["action"], "wag_tail")

        self.assertEqual(len(tts_events), 1)
        self.assertEqual(tts_events[0]["audio"], b"fake_tts_wav")

    def test_process_dialogue_with_visual_intent(self):
        self.mock_camera.capture_jpeg.return_value = b"jpeg_frame_bytes"
        self.mock_vlm.generate.return_value = "[emotion:curious][action:tilt_head] 这是一只可爱的猫咪！"

        express_events = []
        self.bus.subscribe("actuator.express", lambda d: express_events.append(d))

        res = self.orchestrator.process_dialogue("看，这是什么？")

        self.mock_camera.capture_jpeg.assert_called_once()
        self.mock_vlm.generate.assert_called_once()
        args, kwargs = self.mock_vlm.generate.call_args
        self.assertEqual(kwargs["image_data"], b"jpeg_frame_bytes")
        self.assertTrue(res["has_image"])
        self.assertEqual(res["action"], "tilt_head")
        self.assertEqual(res["emotion"], "curious")

    def test_event_driven_voice_input_text(self):
        self.mock_vlm.generate.return_value = "[emotion:excited][action:jump] 收到！"
        express_events = []
        self.bus.subscribe("actuator.express", lambda d: express_events.append(d))

        self.orchestrator.start()
        self.bus.publish("voice.input.text", {"text": "跳一个"})

        # Wait for queue processing in worker thread
        time.sleep(0.3)
        self.orchestrator.stop()

        self.assertEqual(len(express_events), 1)
        self.assertEqual(express_events[0]["action"], "jump")
        self.assertEqual(express_events[0]["emotion"], "excited")

    def test_event_driven_voice_audio_with_asr(self):
        self.mock_asr.transcribe.return_value = "坐下"
        self.mock_vlm.generate.return_value = "[emotion:neutral][action:sit] 好的，我坐下了。"
        express_events = []
        self.bus.subscribe("actuator.express", lambda d: express_events.append(d))

        self.orchestrator.start()
        self.bus.publish("voice.input.audio", b"mock_audio_pcm")

        time.sleep(0.3)
        self.orchestrator.stop()

        self.mock_asr.transcribe.assert_called_once_with(b"mock_audio_pcm")
        self.assertEqual(len(express_events), 1)
        self.assertEqual(express_events[0]["action"], "sit")

    def test_dialogue_request_event(self):
        self.mock_vlm.generate.return_value = "[emotion:happy][action:wag_tail] 欢迎！"
        dialogue_events = []
        self.bus.subscribe("dialogue.response", lambda d: dialogue_events.append(d))

        self.orchestrator.start()
        self.bus.publish("dialogue.request", {"prompt": "欢迎主人"})

        time.sleep(0.3)
        self.orchestrator.stop()

        self.assertEqual(len(dialogue_events), 1)
        self.assertEqual(dialogue_events[0]["clean_text"], "欢迎！")


class TestMuteDogMode(unittest.TestCase):
    """Builtin voice mode: the dog expresses itself via sound tags, never TTS."""

    def setUp(self):
        self.bus = EventBus()
        self.mock_vlm = MagicMock()
        self.mock_vlm.is_available.return_value = True
        self.mock_tts = MagicMock()
        self.mock_tts.is_available.return_value = True

        self.config = CompanionConfig(voice_mode="builtin")
        self.orchestrator = CompanionOrchestrator(
            config=self.config,
            bus=self.bus,
            vlm=self.mock_vlm,
            asr=MagicMock(),
            tts=self.mock_tts,
            camera=MagicMock(),
        )

    def tearDown(self):
        self.orchestrator.stop()

    def test_sound_and_owner_emotion_tags_extracted(self):
        text = "[owner_emotion:sad][emotion:happy][action:wag_tail][sound:pant] 主人别难过"
        tags = CompanionOrchestrator.extract_semantic_tags(text)
        self.assertEqual(tags["action"], "wag_tail")
        self.assertEqual(tags["emotion"], "happy")
        self.assertEqual(tags["sound"], "pant")
        self.assertEqual(tags["owner_emotion"], "sad")
        self.assertEqual(tags["clean_text"], "主人别难过")

    def test_xml_style_sound_tag(self):
        text = "<sound>howling</sound><action>howling</action>"
        tags = CompanionOrchestrator.extract_semantic_tags(text)
        self.assertEqual(tags["sound"], "howling")
        self.assertEqual(tags["action"], "howling")

    def test_extract_tags_backward_compatible(self):
        action, emotion, clean = CompanionOrchestrator.extract_tags("[action:sit][emotion:neutral] 坐下")
        self.assertEqual((action, emotion, clean), ("sit", "neutral", "坐下"))

    def test_builtin_mode_skips_tts(self):
        self.mock_vlm.generate.return_value = "[owner_emotion:happy][emotion:happy][action:wag_tail][sound:happy_bark] 汪汪！"
        tts_events = []
        self.bus.subscribe("tts.audio.ready", lambda d: tts_events.append(d))

        res = self.orchestrator.process_dialogue("旺财你好呀")

        self.assertEqual(res["sound"], "happy_bark")
        self.assertEqual(res["owner_emotion"], "happy")
        self.assertEqual(len(tts_events), 0)
        self.mock_tts.synthesize.assert_not_called()

    def test_builtin_mode_express_carries_sound(self):
        self.mock_vlm.generate.return_value = "[emotion:happy][sound:coquettish][action:wag_tail]"
        express_events = []
        self.bus.subscribe("actuator.express", lambda d: express_events.append(d))

        self.orchestrator.process_dialogue("摸摸头")
        self.assertEqual(express_events[0]["sound"], "coquettish")
        self.assertEqual(express_events[0]["action"], "wag_tail")

    def test_approach_action_dispatches_with_sound_direction(self):
        self.mock_vlm.generate.return_value = "[emotion:excited][action:approach][sound:excited_bark] 来啦！"
        approach_events = []
        express_events = []
        self.bus.subscribe("behavior.approach", lambda d: approach_events.append(d))
        self.bus.subscribe("actuator.express", lambda d: express_events.append(d))

        # Simulate a fresh sound direction detection
        self.bus.publish("sensor.sound.direction", {"angle": 120})
        self.orchestrator.process_dialogue("旺财过来！")

        self.assertEqual(len(approach_events), 1)
        self.assertEqual(approach_events[0]["angle"], 120)
        # Approach replaces the pose action
        self.assertIsNone(express_events[0]["action"])
        self.assertEqual(express_events[0]["sound"], "excited_bark")

    def test_sensor_context_appended_to_prompt(self):
        self.mock_vlm.generate.return_value = "[emotion:happy][sound:happy_bark]"
        self.bus.publish("sensor.sound.direction", {"angle": 45})
        self.orchestrator.process_dialogue("你好")

        args, kwargs = self.mock_vlm.generate.call_args
        self.assertIn("你好", kwargs["prompt"])
        self.assertIn("45", kwargs["prompt"])  # direction digest included
        # Original utterance only in history (no digest pollution)
        self.assertEqual(self.orchestrator.context.get_history()[0]["content"], "你好")

    def test_actuator_speak_ignored_in_builtin_mode(self):
        tts_events = []
        self.bus.subscribe("tts.audio.ready", lambda d: tts_events.append(d))

        self.bus.publish("actuator.speak", {"text": "你好主人"})
        time.sleep(0.1)

        self.assertEqual(len(tts_events), 0)


if __name__ == '__main__':
    unittest.main()
