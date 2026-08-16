"""MiniMax Vision Language Model (VLM) adapter implementation."""
import os
import re
import base64
import logging
from typing import Optional, Dict, Any, List, Union
import requests

from .base import BaseVLM

logger = logging.getLogger(__name__)


class MiniMaxVLM(BaseVLM):
    """MiniMax Vision Language Model adapter supporting OpenAI-compatible chat completions.

    Default Base URL: https://api.minimaxi.com/v1
    Default Model: MiniMax-M2.7-highspeed (High Speed, ~100 TPS)
    """

    DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
    DEFAULT_MODEL = "MiniMax-M2.7-highspeed"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.api_key = (
            self.config.get("api_key")
            or os.getenv("MINIMAX_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
        self.base_url = (
            self.config.get("base_url")
            or os.getenv("MINIMAX_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or self.DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = (
            self.config.get("model")
            or os.getenv("MINIMAX_MODEL", self.DEFAULT_MODEL)
        )
        self.max_tokens = self.config.get("max_tokens", 512)
        self.temperature = self.config.get("temperature", 0.7)
        self.default_system_prompt = self.config.get(
            "system_prompt", "You are PiDog, an embodied companion robotic dog."
        )

    def is_available(self) -> bool:
        """Check if required credentials are provided."""
        return bool(self.api_key)

    def _prepare_image_url(self, image_data: Union[bytes, str]) -> Optional[str]:
        """Convert image bytes, base64, or file path to data URI or URL string."""
        if not image_data:
            return None

        if isinstance(image_data, str):
            if image_data.startswith("data:image/") or image_data.startswith("http://") or image_data.startswith("https://"):
                return image_data
            # Check if it's a file path
            if os.path.exists(image_data):
                try:
                    with open(image_data, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                    return f"data:image/jpeg;base64,{b64}"
                except Exception as e:
                    logger.error(f"Failed to read image file {image_data}: {e}")
                    return None
            # Otherwise assume raw base64 string
            return f"data:image/jpeg;base64,{image_data}"

        elif isinstance(image_data, bytes):
            b64 = base64.b64encode(image_data).decode("utf-8")
            return f"data:image/jpeg;base64,{b64}"

        return None

    def generate(
        self,
        prompt: str,
        image_data: Optional[Union[bytes, str]] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Generate response using MiniMax VLM chat completions API.

        Args:
            prompt: Text query or instruction.
            image_data: Optional image as raw bytes, base64 string, or filepath.
            history: Optional list of past messages.
            system_prompt: Optional override for system prompt.

        Returns:
            Generated text string (with thinking tags cleanly filtered if present).
        """
        if not self.is_available():
            logger.warning("MiniMax API key missing. Skipping generation.")
            return ""

        effective_system_prompt = system_prompt or self.default_system_prompt
        messages: List[Dict[str, Any]] = []

        if effective_system_prompt:
            messages.append({"role": "system", "content": effective_system_prompt})

        if history:
            messages.extend(history)

        # Build current user message (multimodal if image_data provided)
        image_uri = self._prepare_image_url(image_data) if image_data else None
        if image_uri:
            user_content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_uri,
                        "detail": "default"
                    },
                },
            ]
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": prompt})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        endpoint = f"{self.base_url}/chat/completions"

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "extra_body": {
                "reasoning_split": True
            }
        }

        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=self.config.get("timeout", 30.0),
            )
            response.raise_for_status()
            result_json = response.json()

            # Parse OpenAI-compatible response format
            choices = result_json.get("choices", [])
            raw_content = ""
            if choices and len(choices) > 0:
                message = choices[0].get("message", {})
                raw_content = message.get("content", "")
            elif "reply" in result_json:
                raw_content = result_json["reply"]

            # Filter out any lingering <think>...</think> blocks from content
            if raw_content:
                cleaned = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).strip()
                return cleaned

            return ""
        except requests.exceptions.RequestException as e:
            logger.error(f"MiniMax VLM request failed: {e}")
            return ""
        except Exception as e:
            logger.error(f"MiniMax VLM generation failed: {e}")
            return ""
