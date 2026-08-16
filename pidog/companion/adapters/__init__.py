"""Adapters package for AI cloud services."""
from .base import BaseASR, BaseTTS, BaseVLM
from .asr_xiaomi import XiaomiASR
from .tts_xiaomi import XiaomiTTS
from .vlm_minimax import MiniMaxVLM
from .factory import AdapterFactory

__all__ = [
    "BaseASR",
    "BaseTTS",
    "BaseVLM",
    "XiaomiASR",
    "XiaomiTTS",
    "MiniMaxVLM",
    "AdapterFactory",
]
