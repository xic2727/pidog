"""Pidog companion infrastructure."""
from .config import ASRConfig, TTSConfig, VLMConfig, CompanionConfig
from .core import EventBus, ConversationContext, CompanionOrchestrator
from .adapters import (
    BaseASR,
    BaseTTS,
    BaseVLM,
    XiaomiASR,
    XiaomiTTS,
    MiniMaxVLM,
    AdapterFactory,
)
from .behavior import (
    PetState,
    MoodType,
    BehaviorEngine,
)
from .hardware import (
    CameraHelper,
    SensorWorker,
    EmotionExpressor,
)

__all__ = [
    "ASRConfig",
    "TTSConfig",
    "VLMConfig",
    "CompanionConfig",
    "EventBus",
    "ConversationContext",
    "CompanionOrchestrator",
    "BaseASR",
    "BaseTTS",
    "BaseVLM",
    "XiaomiASR",
    "XiaomiTTS",
    "MiniMaxVLM",
    "AdapterFactory",
    "PetState",
    "MoodType",
    "BehaviorEngine",
    "CameraHelper",
    "SensorWorker",
    "EmotionExpressor",
]


