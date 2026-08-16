"""Xiaomi MiMo ASR adapter implementation."""
import os
import base64
import logging
from typing import Optional, Dict, Any, Union
import requests

from .base import BaseASR

logger = logging.getLogger(__name__)


class XiaomiASR(BaseASR):
    """Xiaomi Cloud Speech Recognition (MiMo-V2.5-ASR) adapter.

    Compatible with Xiaomi MiMo API (OpenAI Chat Completions audio input format).
    Endpoint: https://api.xiaomimimo.com/v1/chat/completions
    Model: mimo-v2.5-asr
    """

    DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"
    DEFAULT_MODEL = "mimo-v2.5-asr"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.api_key = (
            self.config.get("api_key")
            or os.getenv("MIMO_API_KEY")
            or os.getenv("XIAOMI_ASR_KEY")
            or os.getenv("XIAOMI_API_KEY")
        )
        self.base_url = (
            self.config.get("base_url")
            or os.getenv("MIMO_BASE_URL")
            or os.getenv("XIAOMI_ASR_ENDPOINT")
            or self.DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = self.config.get("model") or os.getenv("XIAOMI_ASR_MODEL", self.DEFAULT_MODEL)
        self.language = self.config.get("language", "zh")
        self.format = self.config.get("format", "wav")

    def is_available(self) -> bool:
        """Check if required credentials are provided."""
        return bool(self.api_key)

    def transcribe(self, audio_data: Union[bytes, str], format: Optional[str] = None) -> str:
        """Transcribe audio data (bytes or file path) using Xiaomi MiMo-V2.5-ASR.

        Args:
            audio_data: Raw audio bytes (WAV/MP3/PCM) or path to audio file.
            format: Optional audio format override ('wav', 'mp3', 'pcm16').

        Returns:
            Transcribed text.
        """
        if not self.is_available():
            logger.warning("Xiaomi ASR API key missing. Skipping transcription.")
            return ""

        raw_bytes: bytes
        target_format = format or self.format
        if isinstance(audio_data, str):
            if not os.path.exists(audio_data):
                logger.error(f"Audio file not found: {audio_data}")
                return ""
            try:
                # Infer format from extension if not explicitly specified
                if not format:
                    ext = os.path.splitext(audio_data)[1].lstrip(".").lower()
                    if ext in ["wav", "mp3", "pcm", "ogg", "flac"]:
                        target_format = ext
                with open(audio_data, "rb") as f:
                    raw_bytes = f.read()
            except Exception as e:
                logger.error(f"Failed to read audio file {audio_data}: {e}")
                return ""
        elif isinstance(audio_data, bytes):
            raw_bytes = audio_data
        else:
            logger.error(f"Invalid audio_data type: {type(audio_data)}")
            return ""

        if not raw_bytes:
            return ""

        audio_base64 = base64.b64encode(raw_bytes).decode("utf-8")
        mime_format = "wav" if target_format == "wav" else target_format

        endpoint = f"{self.base_url}/chat/completions" if not self.base_url.endswith("/chat/completions") else self.base_url

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": f"data:audio/{mime_format};base64,{audio_base64}"
                            }
                        }
                    ]
                }
            ],
            "extra_body": {
                "asr_options": {
                    "language": self.language
                }
            }
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

            # 1. Parse OpenAI Chat Completion response format (choices[0].message.content)
            choices = result_json.get("choices", [])
            if choices and isinstance(choices, list):
                message = choices[0].get("message", {})
                content = message.get("content", "")
                if content:
                    return content.strip()

            # 2. Fallback to legacy/custom Xiaomi response formats
            if "result" in result_json:
                res = result_json["result"]
                if isinstance(res, list) and len(res) > 0:
                    return res[0]
                elif isinstance(res, str):
                    return res
            elif "text" in result_json:
                return result_json["text"]
            elif "data" in result_json and isinstance(result_json["data"], dict):
                return result_json["data"].get("text", "")

            return ""
        except requests.exceptions.RequestException as e:
            logger.error(f"Xiaomi ASR request failed: {e}")
            return ""
        except Exception as e:
            logger.error(f"Xiaomi ASR processing failed: {e}")
            return ""

