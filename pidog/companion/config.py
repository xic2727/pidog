from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import os
from pathlib import Path

def load_dotenv(env_path: Optional[str] = None):
    """
    轻量级 .env 文件加载器（无需额外安装 python-dotenv 依赖，对 Pi Zero 极度友好）
    """
    if env_path is None:
        # 依次查找当前工作目录、用户主目录下的 .env
        candidates = [
            Path.cwd() / ".env",
            Path(__file__).resolve().parent.parent.parent / ".env",
            Path.home() / ".env",
        ]
        for candidate in candidates:
            if candidate.is_file():
                env_path = str(candidate)
                break

    if not env_path or not os.path.isfile(env_path):
        return

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                # 不覆盖已存在的系统环境变量
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception as e:
        print(f"[CompanionConfig] Warning: Failed to load {env_path}: {e}")

# 模块加载时自动寻找并加载 .env
load_dotenv()


@dataclass
class ASRConfig:
    """Automatic Speech Recognition configuration."""
    provider: str = field(default_factory=lambda: os.getenv("ASR_PROVIDER", "xiaomi"))
    model: str = field(default_factory=lambda: os.getenv("XIAOMI_ASR_MODEL", "mimo-v2.5-asr"))
    language: str = "zh"
    sample_rate: int = 16000
    channels: int = 1
    chunk_size: int = 1024
    api_key: Optional[str] = field(default_factory=lambda: os.getenv("MIMO_API_KEY") or os.getenv("XIAOMI_ASR_KEY") or os.getenv("XIAOMI_API_KEY"))
    base_url: Optional[str] = field(default_factory=lambda: os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1"))
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TTSConfig:
    """Text-To-Speech configuration."""
    provider: str = field(default_factory=lambda: os.getenv("TTS_PROVIDER", "xiaomi"))
    model: str = field(default_factory=lambda: os.getenv("XIAOMI_TTS_MODEL", "mimo-v2.5-tts"))
    voice: str = field(default_factory=lambda: os.getenv("XIAOMI_TTS_VOICE", "茉莉"))
    style_prompt: str = field(default_factory=lambda: os.getenv("XIAOMI_TTS_STYLE", "活泼可爱的小狗语气，声音明亮、轻快、富有亲和力与陪伴感。"))
    speed: float = 1.0
    volume: float = 1.0
    sample_rate: int = 22050
    api_key: Optional[str] = field(default_factory=lambda: os.getenv("MIMO_API_KEY") or os.getenv("XIAOMI_TTS_KEY") or os.getenv("XIAOMI_API_KEY"))
    base_url: Optional[str] = field(default_factory=lambda: os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1"))
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VLMConfig:
    """Vision Language Model configuration."""
    provider: str = field(default_factory=lambda: os.getenv("VLM_PROVIDER", "minimax"))
    model: str = field(default_factory=lambda: os.getenv("MINIMAX_MODEL", "MiniMax-M2.7-highspeed"))
    api_key: Optional[str] = field(default_factory=lambda: os.getenv("MINIMAX_API_KEY") or os.getenv("OPENAI_API_KEY"))
    base_url: Optional[str] = field(default_factory=lambda: os.getenv("MINIMAX_BASE_URL") or os.getenv("OPENAI_BASE_URL", "https://api.minimaxi.com/v1"))
    max_tokens: int = 512
    temperature: float = 0.7
    system_prompt: str = "You are PiDog, an embodied companion robotic dog."
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CompanionConfig:
    """Overall Companion configuration."""
    name: str = "PiDog"
    asr: ASRConfig = field(default_factory=ASRConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    vlm: VLMConfig = field(default_factory=VLMConfig)
    wake_words: List[str] = field(default_factory=lambda: ["pidog", "hi pidog"])
    enable_vision: bool = True
    enable_touch: bool = True
    enable_sound_direction: bool = True
    enable_battery_monitor: bool = True
    # Voice mode:
    #   "builtin" - mute dog: built-in sound effects only, no TTS speech
    #   "tts"     - classic talking dog: LLM replies spoken via TTS
    voice_mode: str = field(default_factory=lambda: os.getenv("PIDOG_VOICE_MODE", "builtin"))
    # Clap-count reflex commands (1 sit / 2 spin / 3 come here)
    enable_clap_commands: bool = field(default_factory=lambda: os.getenv("PIDOG_CLAP_COMMANDS", "1").lower() not in ("0", "false", "no"))
    # Approach behavior (turn toward sound source and walk up)
    enable_approach: bool = field(default_factory=lambda: os.getenv("PIDOG_APPROACH", "1").lower() not in ("0", "false", "no"))
    approach_turn_degrees_per_step: float = field(default_factory=lambda: float(os.getenv("PIDOG_TURN_DEG_PER_STEP", "22.5")))
    approach_forward_steps: int = field(default_factory=lambda: int(os.getenv("PIDOG_APPROACH_FORWARD_STEPS", "4")))
    # Reflex layer tuning
    touch_cooldown: float = field(default_factory=lambda: float(os.getenv("PIDOG_TOUCH_COOLDOWN", "1.5")))
    neglect_timeout: float = field(default_factory=lambda: float(os.getenv("PIDOG_NEGLECT_TIMEOUT", "300")))
    # Dog voice sound library cooldown (seconds between same-tag plays)
    sound_cooldown: float = field(default_factory=lambda: float(os.getenv("PIDOG_SOUND_COOLDOWN", "2.0")))
    battery_check_interval: float = field(default_factory=lambda: float(os.getenv("BATTERY_CHECK_INTERVAL", "15.0")))
    battery_low_voltage: float = field(default_factory=lambda: float(os.getenv("BATTERY_LOW_VOLTAGE", "7.0")))
    battery_critical_voltage: float = field(default_factory=lambda: float(os.getenv("BATTERY_CRITICAL_VOLTAGE", "6.6")))
    battery_low_msg: str = field(default_factory=lambda: os.getenv("BATTERY_LOW_MSG", "主人，我的电量有点低了，记得给我充电哦！"))
    battery_critical_msg: str = field(default_factory=lambda: os.getenv("BATTERY_CRITICAL_MSG", "主人，我快要没电啦，马上就要休息了，快帮我充电吧！"))
    extra: Dict[str, Any] = field(default_factory=dict)

