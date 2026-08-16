#!/usr/bin/env python3
"""
Embodied Pet Companion Application for SunFounder PiDog.

Coordinates:
- Multi-modal perception (Camera, Dual Touch, Ultrasonic, Ears/Sound Direction, IMU)
- Autonomous pet personality & mood engine (PetState & BehaviorEngine)
- End-to-end voice dialogue (ASR, VLM reasoning with emotion/action tag extraction, TTS)
- Physical expression & embodiment (RGB strip, Head/Tail gestures, Preset actions)
- Non-blocking asynchronous event-driven architecture with clean SIGINT/Ctrl+C handling
- Graceful fallback for non-Raspberry Pi simulation environments.
"""

import os
import sys
import time
import signal
import logging
import threading
from typing import Optional, Any

# Ensure parent directory in pythonpath if running directly
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pidog.companion.config import (
    CompanionConfig,
    ASRConfig,
    TTSConfig,
    VLMConfig,
)
from pidog.companion.core.event_bus import EventBus
from pidog.companion.behavior.state import PetState, MoodType
from pidog.companion.behavior.behavior_engine import BehaviorEngine
from pidog.companion.adapters.factory import AdapterFactory
from pidog.companion.hardware.camera_helper import CameraHelper
from pidog.companion.hardware.sensor_worker import SensorWorker
from pidog.companion.hardware.emotion_expressor import EmotionExpressor
from pidog.companion.hardware.audio_worker import MicrophoneWorker, AudioPlayer
from pidog.companion.core.orchestrator import CompanionOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("EmbodiedPetCompanion")


class MockPidog:
    """Mock Pidog hardware interface for development/testing on non-RPi environments."""

    class MockRGB:
        def set_mode(self, style, color, bps=1.0):
            logger.info(f"[MockRGB] style={style}, color={color}, bps={bps}")

        def close(self):
            logger.info("[MockRGB] close")

    class MockTouch:
        def read(self):
            return 'N'

    class MockMusic:
        def sound_play(self, name):
            logger.info(f"[MockMusic] Play sound: {name}")

    def __init__(self):
        self.rgb_strip = self.MockRGB()
        self.dual_touch = self.MockTouch()
        self.music = self.MockMusic()
        self.accData = [0.0, 0.0, 1.0]
        self.pitch = 0.0
        self.roll = 0.0
        self.is_suspended = False
        self._battery_voltage = 7.8
        logger.info("Initialized MockPidog (Non-Raspberry Pi environment).")

    def get_battery_voltage(self) -> float:
        return self._battery_voltage

    def read_distance(self) -> float:
        return 99.0

    def do_action(self, action_name: str, speed: int = 50):
        logger.info(f"[MockPidog] Action: '{action_name}', speed={speed}")

    def head_move(self, target_yrps, immediately=False, speed=50):
        logger.info(f"[MockPidog] Head move: {target_yrps}, speed={speed}")

    def tail_move(self, target_angles, immediately=False, speed=50):
        logger.info(f"[MockPidog] Tail move: {target_angles}, speed={speed}")

    def speak(self, text: str):
        logger.info(f"[MockPidog] Speak: '{text}'")

    def close(self):
        logger.info("[MockPidog] Closed.")


class EmbodiedPetCompanionApp:
    """
    Top-level application coordinating all companion modules.
    """

    def __init__(self, config: Optional[CompanionConfig] = None):
        self.config = config or self._build_default_config()
        self.bus = EventBus()
        self.state = PetState()
        self._running = False
        self._stop_event = threading.Event()

        # Initialize Hardware (Pidog or Mock)
        self.dog = self._init_dog()

        # Initialize AI Adapters
        self.vlm = AdapterFactory.create_vlm(self.config.vlm)
        self.asr = AdapterFactory.create_asr(self.config.asr)
        self.tts = AdapterFactory.create_tts(self.config.tts)

        # Initialize Camera
        self.camera = CameraHelper(camera_backend="auto") if self.config.enable_vision else None

        # Initialize Subsystems
        self.behavior_engine = BehaviorEngine(self.state, self.bus, interval=0.1)
        self.sensor_worker = SensorWorker(
            self.dog,
            self.bus,
            poll_interval=0.05,
            battery_check_interval=self.config.battery_check_interval,
            low_voltage_threshold=self.config.battery_low_voltage,
            critical_voltage_threshold=self.config.battery_critical_voltage,
        )
        self.emotion_expressor = EmotionExpressor(self.dog, self.bus)
        self.mic_worker = MicrophoneWorker(self.bus)
        self.orchestrator = CompanionOrchestrator(
            config=self.config,
            bus=self.bus,
            vlm=self.vlm,
            asr=self.asr,
            tts=self.tts,
            camera=self.camera,
        )

        self._setup_event_monitors()

    def _build_default_config(self) -> CompanionConfig:
        """Build default configuration using environment variables if present."""
        vlm_conf = VLMConfig(
            provider="minimax",
            model=os.getenv("MINIMAX_MODEL", "MiniMax-M2.7-highspeed"),
            api_key=os.getenv("MINIMAX_API_KEY") or os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"),
            system_prompt=(
                "你是SunFounder开发的智能伴侣机器狗PiDog（旺财）。"
                "你有着生动活泼的宠物狗性格，善解人意，偶尔调皮。"
                "你必须在回复开头使用 [emotion:xxx] 和 [action:xxx] 标签表明你的情绪和身体动作。"
                "可用emotion: happy, excited, sad, scared, sleepy, curious, neutral, confused."
                "可用action: wag_tail, bark, stretch, lie, sit, stand, nod, head_nod, tilt_head, pant, howling."
                "例如：[emotion:happy][action:wag_tail] 汪汪！主人你回来啦，我好想你！"
            )
        )

        asr_conf = ASRConfig(
            provider="xiaomi",
            model=os.getenv("XIAOMI_ASR_MODEL", "mimo-v2.5-asr"),
            language="zh",
            api_key=os.getenv("MIMO_API_KEY") or os.getenv("XIAOMI_API_KEY"),
            base_url=os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1"),
        )

        tts_conf = TTSConfig(
            provider="xiaomi",
            model=os.getenv("XIAOMI_TTS_MODEL", "mimo-v2.5-tts"),
            voice=os.getenv("XIAOMI_TTS_VOICE", "茉莉"),
            style_prompt=os.getenv("XIAOMI_TTS_STYLE", "活泼可爱的小狗语气，声音明亮、轻快、富有亲和力与陪伴感，语调微微上扬。"),
            api_key=os.getenv("MIMO_API_KEY") or os.getenv("XIAOMI_API_KEY"),
            base_url=os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1"),
        )

        return CompanionConfig(
            name="旺财",
            vlm=vlm_conf,
            asr=asr_conf,
            tts=tts_conf,
            enable_vision=True,
            enable_touch=True,
            enable_sound_direction=True,
            enable_battery_monitor=True,
        )

    def _init_dog(self) -> Any:
        """Try initializing real Pidog hardware, fall back to MockPidog."""
        try:
            from pidog.pidog import Pidog
            dog = Pidog()
            logger.info("Pidog hardware initialized successfully.")
            return dog
        except Exception as e:
            logger.warning(f"Could not initialize Pidog hardware ({e}). Using MockPidog.")
            return MockPidog()

    def _setup_event_monitors(self):
        """Log dialogue and interaction events."""
        self.bus.subscribe("dialogue.response", self._on_dialogue_response)
        self.bus.subscribe("sensor.touch.head", lambda d: logger.info(">>> Head touched!"))
        self.bus.subscribe("sensor.touch.body", lambda d: logger.info(">>> Body touched!"))
        self.bus.subscribe("sensor.ultrasonic.obstacle", lambda d: logger.warning(f">>> Obstacle close: {d.get('distance')}cm"))

    def _on_dialogue_response(self, data: dict):
        clean_text = data.get("clean_text", "")
        action = data.get("action")
        emotion = data.get("emotion")
        logger.info(f"[Dog Response] [{emotion}][{action}] {clean_text}")

    def start(self):
        """Start all services."""
        logger.info("Starting Embodied Pet Companion application...")
        self._running = True

        self.behavior_engine.start()
        self.sensor_worker.start()
        self.orchestrator.start()
        if hasattr(self, "mic_worker") and self.mic_worker:
            self.mic_worker.start()

        # Welcome express
        self.bus.publish("actuator.express", {
            "emotion": "happy",
            "action": "wag_tail",
            "sound": "single_bark_1.mp3",
            "speak_text": f"汪！我是{self.config.name}，很高兴见到你！"
        })
        logger.info("Embodied Pet Companion is running. Press Ctrl+C to exit.")

    def stop(self):
        """Graceful shutdown of all subsystems."""
        if not self._running:
            return
        logger.info("Shutting down Embodied Pet Companion...")
        self._running = False
        self._stop_event.set()

        # Stop orchestrator and workers
        if hasattr(self, "mic_worker") and self.mic_worker:
            self.mic_worker.stop()
        self.orchestrator.stop()
        self.sensor_worker.stop()
        self.behavior_engine.stop()
        self.emotion_expressor.close()

        if self.camera:
            self.camera.close()

        # Safe posture and close dog hardware
        try:
            if hasattr(self.dog, "do_action"):
                self.dog.do_action("lie", speed=50)
            if hasattr(self.dog, "rgb_strip") and hasattr(self.dog.rgb_strip, "close"):
                self.dog.rgb_strip.close()
            if hasattr(self.dog, "close"):
                self.dog.close()
        except Exception as e:
            logger.debug(f"Error during dog hardware close: {e}")

        logger.info("Embodied Pet Companion stopped safely.")

    def run(self):
        """Main run loop handling CLI interactions or waiting for shutdown."""
        self.start()

        # Handle SIGINT and SIGTERM
        def sig_handler(sig, frame):
            logger.info("\nCaught exit signal. Stopping...")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, sig_handler)
        signal.signal(signal.SIGTERM, sig_handler)

        try:
            while self._running and not self._stop_event.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.stop()


def main():
    app = EmbodiedPetCompanionApp()
    app.run()


if __name__ == "__main__":
    main()
