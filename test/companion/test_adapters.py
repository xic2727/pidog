"""Unit tests for AI provider adapters."""
import os
import base64
import unittest
from unittest.mock import patch, MagicMock

from pidog.companion.adapters.base import BaseASR, BaseTTS, BaseVLM
from pidog.companion.adapters.asr_xiaomi import XiaomiASR
from pidog.companion.adapters.tts_xiaomi import XiaomiTTS
from pidog.companion.adapters.vlm_minimax import MiniMaxVLM
from pidog.companion.adapters.factory import AdapterFactory
from pidog.companion.config import ASRConfig, TTSConfig, VLMConfig


class TestXiaomiASR(unittest.TestCase):
    @patch.dict(os.environ, {"MIMO_API_KEY": "", "XIAOMI_ASR_KEY": "", "XIAOMI_API_KEY": ""}, clear=True)
    def test_missing_api_key_handles_gracefully(self):
        asr = XiaomiASR(config={})
        self.assertFalse(asr.is_available())
        res = asr.transcribe(b"dummy_audio_bytes")
        self.assertEqual(res, "")

    @patch("requests.post")
    def test_transcribe_bytes_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "你好，PiDog！"
                    }
                }
            ]
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        asr = XiaomiASR(config={"api_key": "test_key", "base_url": "https://api.xiaomimimo.com/v1"})
        self.assertTrue(asr.is_available())

        result = asr.transcribe(b"fake_audio_bytes")
        self.assertEqual(result, "你好，PiDog！")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://api.xiaomimimo.com/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test_key")
        expected_b64 = base64.b64encode(b"fake_audio_bytes").decode("utf-8")
        self.assertEqual(
            kwargs["json"]["messages"][0]["content"][0]["input_audio"]["data"],
            f"data:audio/wav;base64,{expected_b64}"
        )
        self.assertEqual(kwargs["json"]["extra_body"]["asr_options"]["language"], "zh")

    @patch("requests.post")
    def test_transcribe_network_error(self, mock_post):
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError("Network down")

        asr = XiaomiASR(config={"api_key": "test_key"})
        result = asr.transcribe(b"fake_audio_bytes")
        self.assertEqual(result, "")


class TestXiaomiTTS(unittest.TestCase):
    @patch.dict(os.environ, {"MIMO_API_KEY": "", "XIAOMI_TTS_KEY": "", "XIAOMI_API_KEY": ""}, clear=True)
    def test_missing_api_key_handles_gracefully(self):
        tts = XiaomiTTS(config={})
        self.assertFalse(tts.is_available())
        res = tts.synthesize("你好")
        self.assertIsNone(res)
        chunks = list(tts.synthesize_stream("你好"))
        self.assertEqual(chunks, [])

    def test_empty_text_returns_none(self):
        tts = XiaomiTTS(config={"api_key": "test_key"})
        self.assertIsNone(tts.synthesize(""))
        chunks = list(tts.synthesize_stream(""))
        self.assertEqual(chunks, [])

    @patch("requests.post")
    def test_synthesize_binary_response_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": "audio/wav"}
        mock_response.content = b"fake_wav_audio_content"
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        tts = XiaomiTTS(config={"api_key": "test_key", "voice": "冰糖"})
        self.assertTrue(tts.is_available())

        audio_bytes = tts.synthesize("早上好")
        self.assertEqual(audio_bytes, b"fake_wav_audio_content")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["messages"][-1]["content"], "早上好")
        self.assertEqual(kwargs["json"]["audio"]["voice"], "冰糖")
        self.assertFalse(kwargs["json"]["stream"])

    @patch("requests.post")
    def test_synthesize_stream_success(self, mock_post):
        pcm_chunk_1 = b"chunk_1_pcm_data"
        pcm_chunk_2 = b"chunk_2_pcm_data"
        b64_1 = base64.b64encode(pcm_chunk_1).decode("utf-8")
        b64_2 = base64.b64encode(pcm_chunk_2).decode("utf-8")

        sse_lines = [
            b"",
            f'data: {{"choices": [{{"delta": {{"audio": {{"data": "{b64_1}"}}}}}}]}}'.encode("utf-8"),
            f'data: {{"choices": [{{"delta": {{"audio": {{"data": "{b64_2}"}}}}}}]}}'.encode("utf-8"),
            b"data: [DONE]",
        ]

        mock_response = MagicMock()
        mock_response.iter_lines.return_value = iter(sse_lines)
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        tts = XiaomiTTS(config={"api_key": "test_key", "voice": "茉莉"})
        chunks = list(tts.synthesize_stream("今天天气真好"))

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], pcm_chunk_1)
        self.assertEqual(chunks[1], pcm_chunk_2)

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertTrue(kwargs["json"]["stream"])
        self.assertEqual(kwargs["json"]["audio"]["format"], "pcm16")
        self.assertEqual(kwargs["json"]["audio"]["voice"], "茉莉")


class TestMiniMaxVLM(unittest.TestCase):
    @patch.dict(os.environ, {"MINIMAX_API_KEY": "", "OPENAI_API_KEY": ""}, clear=True)
    def test_missing_api_key_handles_gracefully(self):
        vlm = MiniMaxVLM(config={})
        self.assertFalse(vlm.is_available())
        res = vlm.generate(prompt="Hello")
        self.assertEqual(res, "")

    @patch("requests.post")
    def test_generate_text_only(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "汪汪！主人好！"}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        vlm = MiniMaxVLM(config={"api_key": "minimax_key", "model": "abab6.5s-chat"})
        self.assertTrue(vlm.is_available())

        reply = vlm.generate(prompt="你是谁？")
        self.assertEqual(reply, "汪汪！主人好！")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertTrue(args[0].endswith("/chat/completions"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer minimax_key")
        payload = kwargs["json"]
        self.assertEqual(payload["model"], "abab6.5s-chat")
        # Check system message and user message
        self.assertEqual(len(payload["messages"]), 2)
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["role"], "user")
        self.assertEqual(payload["messages"][1]["content"], "你是谁？")

    @patch("requests.post")
    def test_generate_multimodal_image_bytes(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "我看到了一只红色的球。"}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        vlm = MiniMaxVLM(config={"api_key": "minimax_key"})
        image_bytes = b"fake_jpeg_image_bytes"
        expected_b64 = base64.b64encode(image_bytes).decode("utf-8")

        reply = vlm.generate(prompt="你看到了什么？", image_data=image_bytes)
        self.assertEqual(reply, "我看到了一只红色的球。")

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        payload = kwargs["json"]
        user_msg = payload["messages"][-1]
        self.assertEqual(user_msg["role"], "user")
        self.assertIsInstance(user_msg["content"], list)
        self.assertEqual(user_msg["content"][0]["text"], "你看到了什么？")
        self.assertEqual(
            user_msg["content"][1]["image_url"]["url"],
            f"data:image/jpeg;base64,{expected_b64}",
        )

    @patch("requests.post")
    def test_generate_multimodal_image_base64_string(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "这是一本书。"}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        vlm = MiniMaxVLM(config={"api_key": "minimax_key"})
        raw_b64 = "aGVsbG8="
        reply = vlm.generate(prompt="这是什么？", image_data=raw_b64)
        self.assertEqual(reply, "这是一本书。")

        _, kwargs = mock_post.call_args
        payload = kwargs["json"]
        user_msg = payload["messages"][-1]
        self.assertEqual(
            user_msg["content"][1]["image_url"]["url"],
            f"data:image/jpeg;base64,{raw_b64}",
        )


class TestAdapterFactory(unittest.TestCase):
    def test_create_asr_from_config(self):
        cfg = ASRConfig(provider="xiaomi", language="zh-CN", extra={"api_key": "abc"})
        asr = AdapterFactory.create_asr(cfg)
        self.assertIsInstance(asr, XiaomiASR)
        self.assertEqual(asr.api_key, "abc")
        self.assertEqual(asr.language, "zh-CN")

    def test_create_tts_from_config(self):
        cfg = TTSConfig(provider="xiaomi", voice="xiaomei", speed=1.2, extra={"api_key": "abc"})
        tts = AdapterFactory.create_tts(cfg)
        self.assertIsInstance(tts, XiaomiTTS)
        self.assertEqual(tts.api_key, "abc")
        self.assertEqual(tts.voice, "xiaomei")
        self.assertEqual(tts.speed, 1.2)

    def test_create_vlm_from_config(self):
        cfg = VLMConfig(provider="minimax", model="abab6.5s-chat", api_key="abc")
        vlm = AdapterFactory.create_vlm(cfg)
        self.assertIsInstance(vlm, MiniMaxVLM)
        self.assertEqual(vlm.api_key, "abc")
        self.assertEqual(vlm.model, "abab6.5s-chat")

    def test_create_unknown_provider_returns_none(self):
        cfg = ASRConfig(provider="unknown_asr")
        asr = AdapterFactory.create_asr(cfg)
        self.assertIsNone(asr)


if __name__ == "__main__":
    unittest.main()
