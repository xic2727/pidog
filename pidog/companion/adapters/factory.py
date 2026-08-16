"""Factory for instantiating AI provider adapters."""
import logging
from typing import Optional, Dict, Any, Union

from ..config import ASRConfig, TTSConfig, VLMConfig
from .base import BaseASR, BaseTTS, BaseVLM
from .asr_xiaomi import XiaomiASR
from .tts_xiaomi import XiaomiTTS
from .vlm_minimax import MiniMaxVLM

logger = logging.getLogger(__name__)


class AdapterFactory:
    """Factory creating ASR, TTS, and VLM adapter instances."""

    _ASR_REGISTRY = {
        "xiaomi": XiaomiASR,
    }

    _TTS_REGISTRY = {
        "xiaomi": XiaomiTTS,
    }

    _VLM_REGISTRY = {
        "minimax": MiniMaxVLM,
    }

    @classmethod
    def register_asr(cls, name: str, asr_cls):
        """Register a custom ASR adapter."""
        cls._ASR_REGISTRY[name.lower()] = asr_cls

    @classmethod
    def register_tts(cls, name: str, tts_cls):
        """Register a custom TTS adapter."""
        cls._TTS_REGISTRY[name.lower()] = tts_cls

    @classmethod
    def register_vlm(cls, name: str, vlm_cls):
        """Register a custom VLM adapter."""
        cls._VLM_REGISTRY[name.lower()] = vlm_cls

    @classmethod
    def create_asr(cls, config: Union[ASRConfig, Dict[str, Any], None] = None) -> Optional[BaseASR]:
        """Create an ASR adapter from ASRConfig or dict."""
        if config is None:
            config_dict = {}
            provider = "xiaomi"
        elif isinstance(config, ASRConfig):
            config_dict = {
                "provider": config.provider,
                "language": config.language,
                "sample_rate": config.sample_rate,
                "channels": config.channels,
                "chunk_size": config.chunk_size,
                **config.extra,
            }
            provider = config.provider
        elif isinstance(config, dict):
            config_dict = config.copy()
            provider = config_dict.get("provider", "xiaomi")
        else:
            raise TypeError(f"Invalid config type for ASR: {type(config)}")

        adapter_cls = cls._ASR_REGISTRY.get(provider.lower())
        if not adapter_cls:
            logger.warning(f"Unsupported ASR provider: {provider}")
            return None

        return adapter_cls(config=config_dict)

    @classmethod
    def create_tts(cls, config: Union[TTSConfig, Dict[str, Any], None] = None) -> Optional[BaseTTS]:
        """Create a TTS adapter from TTSConfig or dict."""
        if config is None:
            config_dict = {}
            provider = "xiaomi"
        elif isinstance(config, TTSConfig):
            config_dict = {
                "provider": config.provider,
                "voice": config.voice,
                "speed": config.speed,
                "volume": config.volume,
                "sample_rate": config.sample_rate,
                **config.extra,
            }
            provider = config.provider
        elif isinstance(config, dict):
            config_dict = config.copy()
            provider = config_dict.get("provider", "xiaomi")
        else:
            raise TypeError(f"Invalid config type for TTS: {type(config)}")

        adapter_cls = cls._TTS_REGISTRY.get(provider.lower())
        if not adapter_cls:
            logger.warning(f"Unsupported TTS provider: {provider}")
            return None

        return adapter_cls(config=config_dict)

    @classmethod
    def create_vlm(cls, config: Union[VLMConfig, Dict[str, Any], None] = None) -> Optional[BaseVLM]:
        """Create a VLM adapter from VLMConfig or dict."""
        if config is None:
            config_dict = {}
            provider = "minimax"
        elif isinstance(config, VLMConfig):
            config_dict = {
                "provider": config.provider,
                "model": config.model,
                "api_key": config.api_key,
                "base_url": config.base_url,
                "max_tokens": config.max_tokens,
                "temperature": config.temperature,
                "system_prompt": config.system_prompt,
                **config.extra,
            }
            provider = config.provider
        elif isinstance(config, dict):
            config_dict = config.copy()
            provider = config_dict.get("provider", "minimax")
        else:
            raise TypeError(f"Invalid config type for VLM: {type(config)}")

        adapter_cls = cls._VLM_REGISTRY.get(provider.lower())
        if not adapter_cls:
            logger.warning(f"Unsupported VLM provider: {provider}")
            return None

        return adapter_cls(config=config_dict)
