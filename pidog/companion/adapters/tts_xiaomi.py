"""Xiaomi MiMo TTS adapter implementation."""
import os
import json
import base64
import logging
from typing import Optional, Dict, Any, Generator
import requests

from .base import BaseTTS

logger = logging.getLogger(__name__)


class XiaomiTTS(BaseTTS):
    """Xiaomi Cloud Text-To-Speech (MiMo-V2.5-TTS) adapter.

    Compatible with Xiaomi MiMo TTS API (OpenAI Chat Completions audio streaming format).
    Endpoint: https://api.xiaomimimo.com/v1/chat/completions
    Model: mimo-v2.5-tts / mimo-v2.5-tts-voicedesign / mimo-v2.5-tts-voiceclone
    """

    DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"
    DEFAULT_MODEL = "mimo-v2.5-tts"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.api_key = (
            self.config.get("api_key")
            or os.getenv("MIMO_API_KEY")
            or os.getenv("XIAOMI_TTS_KEY")
            or os.getenv("XIAOMI_API_KEY")
        )
        self.base_url = (
            self.config.get("base_url")
            or os.getenv("MIMO_BASE_URL")
            or self.DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = self.config.get("model") or os.getenv("XIAOMI_TTS_MODEL", self.DEFAULT_MODEL)
        self.voice = self.config.get("voice") or os.getenv("XIAOMI_TTS_VOICE", "冰糖")
        self.speed = self.config.get("speed", 1.0)
        self.default_style_prompt = self.config.get("style_prompt") or "活泼可爱的小狗语气，声音明亮、轻快、富有亲和力与陪伴感。"

    def is_available(self) -> bool:
        """Check if required credentials are provided."""
        return bool(self.api_key)

    def synthesize_stream(
        self,
        text: str,
        style_prompt: Optional[str] = None,
        format: str = "pcm16"
    ) -> Generator[bytes, None, None]:
        """Low-latency streaming speech synthesis using MiMo-V2.5-TTS (Server-Sent Events).

        Args:
            text: Target synthesis text (placed in assistant message).
            style_prompt: Optional style / director description for tone control (in user message).
            format: Audio format (pcm16 or wav). Default is pcm16 for streaming.

        Yields:
            bytes: Audio chunk data (PCM16 / WAV bytes).
        """
        if not text:
            logger.warning("Empty text passed to Xiaomi TTS.")
            return

        if not self.is_available():
            logger.warning("Xiaomi TTS API key missing. Skipping streaming synthesis.")
            return

        endpoint = f"{self.base_url}/chat/completions" if not self.base_url.endswith("/chat/completions") else self.base_url

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        user_content = style_prompt if style_prompt is not None else self.default_style_prompt

        messages = []
        if user_content:
            messages.append({
                "role": "user",
                "content": user_content
            })
        messages.append({
            "role": "assistant",
            "content": text
        })

        payload = {
            "model": self.model,
            "messages": messages,
            "audio": {
                "format": format,
                "voice": self.voice
            },
            "stream": True
        }

        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                stream=True,
                timeout=self.config.get("timeout", 20.0),
            )
            response.raise_for_status()

            for line in response.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8") if isinstance(line, bytes) else line
                if line_str.startswith("data:"):
                    data_str = line_str[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk_json = json.loads(data_str)
                        choices = chunk_json.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        audio_info = delta.get("audio")
                        if audio_info and isinstance(audio_info, dict) and "data" in audio_info:
                            pcm_chunk = base64.b64decode(audio_info["data"])
                            if pcm_chunk:
                                yield pcm_chunk
                    except json.JSONDecodeError:
                        continue
        except requests.exceptions.RequestException as e:
            logger.error(f"Xiaomi TTS stream request failed: {e}")
        except Exception as e:
            logger.error(f"Xiaomi TTS stream processing failed: {e}")

    def synthesize(
        self,
        text: str,
        output_path: Optional[str] = None,
        style_prompt: Optional[str] = None,
        format: str = "wav"
    ) -> Optional[bytes]:
        """Synthesize text into complete audio using Xiaomi MiMo TTS API (Non-streaming or collected).

        Args:
            text: Text to synthesize.
            output_path: Optional path to save the audio file.
            style_prompt: Optional user style prompt.
            format: Audio format ('wav' or 'pcm16').

        Returns:
            Audio bytes if successful, None otherwise.
        """
        if not text:
            logger.warning("Empty text passed to Xiaomi TTS.")
            return None

        if not self.is_available():
            logger.warning("Xiaomi TTS API key missing. Skipping synthesis.")
            return None

        endpoint = f"{self.base_url}/chat/completions" if not self.base_url.endswith("/chat/completions") else self.base_url

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        user_content = style_prompt if style_prompt is not None else self.default_style_prompt

        messages = []
        if user_content:
            messages.append({
                "role": "user",
                "content": user_content
            })
        messages.append({
            "role": "assistant",
            "content": text
        })

        payload = {
            "model": self.model,
            "messages": messages,
            "audio": {
                "format": format,
                "voice": self.voice
            },
            "stream": False
        }

        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=self.config.get("timeout", 15.0),
            )
            response.raise_for_status()
            result_json = response.json()

            audio_bytes: Optional[bytes] = None
            choices = result_json.get("choices", [])
            if choices and isinstance(choices, list):
                message = choices[0].get("message", {})
                audio_info = message.get("audio", {})
                if isinstance(audio_info, dict) and "data" in audio_info:
                    audio_bytes = base64.b64decode(audio_info["data"])

            # Fallback handling for raw/binary audio if returned
            if not audio_bytes and response.content:
                content_type = response.headers.get("Content-Type", "")
                if "audio" in content_type or "octet-stream" in content_type:
                    audio_bytes = response.content

            if audio_bytes and output_path:
                os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(audio_bytes)

            return audio_bytes
        except requests.exceptions.RequestException as e:
            logger.error(f"Xiaomi TTS request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Xiaomi TTS synthesis failed: {e}")
            return None

