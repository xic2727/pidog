"""Base adapter classes for Companion AI services."""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, Union


class BaseASR(ABC):
    """Abstract base class for Automatic Speech Recognition adapters."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    @abstractmethod
    def transcribe(self, audio_data: Union[bytes, str]) -> str:
        """Transcribe audio data (bytes or file path) to text.

        Args:
            audio_data: Raw audio bytes or path to audio file.

        Returns:
            Transcribed text. Empty string if transcription fails or no speech detected.
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the ASR service is properly configured and available."""
        pass


class BaseTTS(ABC):
    """Abstract base class for Text-To-Speech adapters."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    @abstractmethod
    def synthesize(self, text: str, output_path: Optional[str] = None) -> Optional[bytes]:
        """Synthesize text to audio bytes (complete payload).

        Args:
            text: Text to synthesize.
            output_path: Optional path to save audio file.

        Returns:
            Audio bytes if successful, None otherwise.
        """
        pass

    def synthesize_stream(self, text: str, style_prompt: Optional[str] = None):
        """Synthesize text into a low-latency audio stream (generator yielding PCM/WAV chunks).

        Args:
            text: Text to synthesize (in assistant role).
            style_prompt: Optional user style / director prompt for tone/mood.

        Yields:
            bytes: Audio chunks.
        """
        # Default fallback to complete synthesis if streaming not overridden
        audio = self.synthesize(text)
        if audio:
            yield audio

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the TTS service is properly configured and available."""
        pass


class BaseVLM(ABC):
    """Abstract base class for Vision Language Model adapters."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    @abstractmethod
    def generate(
        self,
        prompt: str,
        image_data: Optional[Union[bytes, str]] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Generate text response from text prompt and optional image.

        Args:
            prompt: Text prompt / user question.
            image_data: Optional raw image bytes, base64-encoded string, or path to image.
            history: Optional conversation history in format [{'role': 'user'|'assistant'|'system', 'content': ...}].
            system_prompt: Optional override for system prompt.

        Returns:
            Generated text response.
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the VLM service is properly configured and available."""
        pass
