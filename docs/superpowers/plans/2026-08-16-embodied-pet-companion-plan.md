# Pidog 具身多模态智能伴侣与自主宠物系统实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于树莓派 Pi Zero 2W 构建 Pidog 的具身多模态对话伴侣（MiniMax M3 + 小米 ASR/TTS + 意图拍照）与自主拟真宠物生态系统（状态机 + 传感器反射 + 优先级抢占调度）。

**Architecture:** 采用事件驱动的分层架构：下层为硬件监控/执行器与按需抓帧服务；中层为 AI Provider 适配器抽象（可插拔）与自主宠物状态机；上层通过 Orchestrator 事件总线协调意图解析、音动协同与抢占调度。

**Tech Stack:** Python 3.7+, `robot-hat`, `numpy`, `requests`, `unittest`/`pytest`, `threading`, `dataclasses`.

**Spec:** [docs/superpowers/specs/2026-08-16-embodied-pet-companion-design.md](docs/superpowers/specs/2026-08-16-embodied-pet-companion-design.md)

## Global Constraints

- 硬件平台：Raspberry Pi Zero 2W (512MB RAM)，所有线程必须非阻塞且低 CPU 占用（轮询控制在 20~30Hz）。
- 摄像头按需捕获：不开启常驻推流，仅在检测到视觉意图时抓取单帧 640x480 并转 Base64。
- 模块解耦：所有外部 AI 服务继承自 `BaseASR`, `BaseTTS`, `BaseVLM`，禁止在业务核心直接硬编码特定 SDK。
- 容错保护：所有外部网络 API 失败时必须有优雅降级表现（困惑叫声 + 歪头 + 黄灯），系统不崩溃。

---

### Task 1: 核心事件总线与配置基础设施

**Files:**
- Create: `pidog/companion/__init__.py`
- Create: `pidog/companion/config.py`
- Create: `pidog/companion/core/event_bus.py`
- Test: `test/companion/test_event_bus.py`

**Interfaces:**
- Consumes: Standard Python `dataclasses`, `typing`, `collections`
- Produces: `EventBus` (`subscribe(topic, callback)`, `publish(topic, data)`), `CompanionConfig`

- [ ] **Step 1: 编写 EventBus 单元测试**

```python
# test/companion/test_event_bus.py
import unittest
from pidog.companion.core.event_bus import EventBus

class TestEventBus(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()

    def test_publish_subscribe(self):
        received = []
        def handler(data):
            received.append(data)
        
        self.bus.subscribe("touch.head", handler)
        self.bus.publish("touch.head", {"action": "pet"})
        
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["action"], "pet")

    def test_unsubscribe(self):
        received = []
        def handler(data):
            received.append(data)
            
        unsub = self.bus.subscribe("sensor.imu", handler)
        unsub()
        self.bus.publish("sensor.imu", {"pitch": 10})
        self.assertEqual(len(received), 0)

if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行测试并验证失败**

Run: `python3 -m unittest test/companion/test_event_bus.py`
Expected: FAIL with "No module named 'pidog.companion'"

- [ ] **Step 3: 实现配置模型与 EventBus**

```python
# pidog/companion/__init__.py
"""Pidog Companion System Package"""

# pidog/companion/config.py
from dataclasses import dataclass, field
import os

@dataclass
class ASRConfig:
    provider: str = "xiaomi"
    api_key: str = field(default_factory=lambda: os.getenv("XIAOMI_ASR_KEY", ""))
    app_id: str = field(default_factory=lambda: os.getenv("XIAOMI_APP_ID", ""))

@dataclass
class TTSConfig:
    provider: str = "xiaomi"
    api_key: str = field(default_factory=lambda: os.getenv("XIAOMI_TTS_KEY", ""))
    app_id: str = field(default_factory=lambda: os.getenv("XIAOMI_APP_ID", ""))
    voice_name: str = "cute_pet"

@dataclass
class VLMConfig:
    provider: str = "minimax"
    api_key: str = field(default_factory=lambda: os.getenv("MINIMAX_API_KEY", ""))
    group_id: str = field(default_factory=lambda: os.getenv("MINIMAX_GROUP_ID", ""))
    model: str = "MiniMax-Text-01"

@dataclass
class CompanionConfig:
    asr: ASRConfig = field(default_factory=ASRConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    vlm: VLMConfig = field(default_factory=VLMConfig)
    sensor_poll_interval_s: float = 0.05
    visual_intent_keywords: list = field(default_factory=lambda: [
        "看", "这是什么", "这是啥", "我手里", "什么颜色", "长什么样", "辨认"
    ])

# pidog/companion/core/event_bus.py
import threading
from typing import Callable, Dict, List, Any

class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Any], None]]] = {}
        self._lock = threading.RLock()

    def subscribe(self, topic: str, callback: Callable[[Any], None]) -> Callable[[], None]:
        with self._lock:
            if topic not in self._subscribers:
                self._subscribers[topic] = []
            self._subscribers[topic].append(callback)

        def unsubscribe():
            with self._lock:
                if topic in self._subscribers and callback in self._subscribers[topic]:
                    self._subscribers[topic].remove(callback)
        return unsubscribe

    def publish(self, topic: str, data: Any = None) -> None:
        with self._lock:
            handlers = list(self._subscribers.get(topic, []))
        for handler in handlers:
            try:
                handler(data)
            except Exception as e:
                print(f"[EventBus] Error handling {topic}: {e}")
```

- [ ] **Step 4: 运行测试并验证通过**

Run: `python3 -m unittest test/companion/test_event_bus.py`
Expected: PASS

- [ ] **Step 5: 提交代码**

```bash
git add pidog/companion/ test/companion/
git commit -m "feat(companion): add config and event bus infrastructure"
```

---

### Task 2: AI Provider 抽象基类与适配器实现

**Files:**
- Create: `pidog/companion/adapters/__init__.py`
- Create: `pidog/companion/adapters/base.py`
- Create: `pidog/companion/adapters/asr_xiaomi.py`
- Create: `pidog/companion/adapters/tts_xiaomi.py`
- Create: `pidog/companion/adapters/vlm_minimax.py`
- Create: `pidog/companion/adapters/factory.py`
- Test: `test/companion/test_adapters.py`

**Interfaces:**
- Consumes: `CompanionConfig`, `requests`
- Produces: `BaseASR`, `BaseTTS`, `BaseVLM`, `AdapterFactory`

- [ ] **Step 1: 编写适配器抽象与 Mock 单元测试**

```python
# test/companion/test_adapters.py
import unittest
from unittest.mock import patch, MagicMock
from pidog.companion.adapters.base import BaseASR, BaseTTS, BaseVLM
from pidog.companion.adapters.vlm_minimax import MiniMaxVLM
from pidog.companion.config import VLMConfig

class TestAdapters(unittest.TestCase):
    def test_minimax_payload_with_image(self):
        vlm = MiniMaxVLM(VLMConfig(api_key="mock_key", group_id="mock_grp"))
        messages = [{"role": "user", "content": "你看这是什么"}]
        image_bytes = b"fake_jpeg_data"

        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "choices": [{"message": {"content": "[emotion:happy] 这是一个苹果！"}}]
            }
            reply = vlm.chat(messages, image_bytes=image_bytes)
            self.assertIn("[emotion:happy]", reply)
            self.assertTrue(mock_post.called)
            payload = mock_post.call_args[1]["json"]
            self.assertIn("messages", payload)

if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行测试并验证失败**

Run: `python3 -m unittest test/companion/test_adapters.py`
Expected: FAIL with "No module named 'pidog.companion.adapters.base'"

- [ ] **Step 3: 实现 Provider 抽象基类与具体实现**

```python
# pidog/companion/adapters/__init__.py
"""AI Adapters Package"""

# pidog/companion/adapters/base.py
from abc import ABC, abstractmethod
from typing import Optional, Generator, Dict, Any, List

class BaseASR(ABC):
    @abstractmethod
    def recognize(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """语音识别"""
        pass

class BaseTTS(ABC):
    @abstractmethod
    def synthesize(self, text: str, voice_params: Optional[Dict[str, Any]] = None) -> bytes:
        """语音合成"""
        pass

class BaseVLM(ABC):
    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, Any]],
        image_bytes: Optional[bytes] = None,
        system_prompt: Optional[str] = None
    ) -> str:
        """多模态大模型对话"""
        pass

# pidog/companion/adapters/asr_xiaomi.py
import requests
from .base import BaseASR
from ..config import ASRConfig

class XiaomiASR(BaseASR):
    def __init__(self, config: ASRConfig):
        self.config = config

    def recognize(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        if not self.config.api_key:
            # 兼容本地无 Key 测试
            return ""
        # 封装小米 ASR REST / WS 调用
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "audio/raw;rate=16000"
        }
        try:
            resp = requests.post(
                "https://api.ai.xiaomi.com/asr/v1/recognize",
                headers=headers,
                data=audio_data,
                timeout=5.0
            )
            if resp.status_code == 200:
                return resp.json().get("result", {}).get("text", "")
        except Exception as e:
            print(f"[XiaomiASR] API Error: {e}")
        return ""

# pidog/companion/adapters/tts_xiaomi.py
import requests
from typing import Optional, Dict, Any
from .base import BaseTTS
from ..config import TTSConfig

class XiaomiTTS(BaseTTS):
    def __init__(self, config: TTSConfig):
        self.config = config

    def synthesize(self, text: str, voice_params: Optional[Dict[str, Any]] = None) -> bytes:
        if not self.config.api_key:
            return b""
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "text": text,
            "voice": (voice_params or {}).get("voice", self.config.voice_name),
            "format": "mp3"
        }
        try:
            resp = requests.post(
                "https://api.ai.xiaomi.com/tts/v1/synthesize",
                headers=headers,
                json=payload,
                timeout=5.0
            )
            if resp.status_code == 200:
                return resp.content
        except Exception as e:
            print(f"[XiaomiTTS] API Error: {e}")
        return b""

# pidog/companion/adapters/vlm_minimax.py
import base64
import requests
from typing import Optional, List, Dict, Any
from .base import BaseVLM
from ..config import VLMConfig

class MiniMaxVLM(BaseVLM):
    def __init__(self, config: VLMConfig):
        self.config = config

    def chat(
        self,
        messages: List[Dict[str, Any]],
        image_bytes: Optional[bytes] = None,
        system_prompt: Optional[str] = None
    ) -> str:
        if not self.config.api_key:
            return "[emotion:happy][action:wag_tail] 汪！很高兴见到你！"

        url = f"https://api.minimax.chat/v1/text/chatcompletion_v2?GroupId={self.config.group_id}"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json"
        }

        formatted_msgs = []
        if system_prompt:
            formatted_msgs.append({"role": "system", "content": system_prompt})

        for msg in messages:
            if msg == messages[-1] and image_bytes and msg["role"] == "user":
                img_b64 = base64.b64encode(image_bytes).decode("utf-8")
                content = [
                    {"type": "text", "text": msg["content"]},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                ]
                formatted_msgs.append({"role": "user", "content": content})
            else:
                formatted_msgs.append(msg)

        payload = {
            "model": self.config.model,
            "messages": formatted_msgs,
            "temperature": 0.7
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=8.0)
            if resp.status_code == 200:
                res = resp.json()
                return res.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            print(f"[MiniMaxVLM] API Error: {e}")
            return "[emotion:confused] 汪……网络好像开小差了。"
        return ""

# pidog/companion/adapters/factory.py
from .base import BaseASR, BaseTTS, BaseVLM
from .asr_xiaomi import XiaomiASR
from .tts_xiaomi import XiaomiTTS
from .vlm_minimax import MiniMaxVLM
from ..config import CompanionConfig

class AdapterFactory:
    @staticmethod
    def create_asr(config: CompanionConfig) -> BaseASR:
        if config.asr.provider == "xiaomi":
            return XiaomiASR(config.asr)
        raise ValueError(f"Unsupported ASR provider: {config.asr.provider}")

    @staticmethod
    def create_tts(config: CompanionConfig) -> BaseTTS:
        if config.tts.provider == "xiaomi":
            return XiaomiTTS(config.tts)
        raise ValueError(f"Unsupported TTS provider: {config.tts.provider}")

    @staticmethod
    def create_vlm(config: CompanionConfig) -> BaseVLM:
        if config.vlm.provider == "minimax":
            return MiniMaxVLM(config.vlm)
        raise ValueError(f"Unsupported VLM provider: {config.vlm.provider}")
```

- [ ] **Step 4: 运行测试并验证通过**

Run: `python3 -m unittest test/companion/test_adapters.py`
Expected: PASS

- [ ] **Step 5: 提交代码**

```bash
git add pidog/companion/adapters/ test/companion/test_adapters.py
git commit -m "feat(companion): implement AI provider adapters for Xiaomi and MiniMax"
```

---

### Task 3: 宠物生理状态机与自发行为引擎

**Files:**
- Create: `pidog/companion/behavior/__init__.py`
- Create: `pidog/companion/behavior/state.py`
- Create: `pidog/companion/behavior/behavior_engine.py`
- Test: `test/companion/test_behavior.py`

**Interfaces:**
- Consumes: `EventBus`
- Produces: `PetState`, `MoodType`, `BehaviorEngine`

- [ ] **Step 1: 编写生理状态与自发行为流转测试**

```python
# test/companion/test_behavior.py
import unittest
from pidog.companion.core.event_bus import EventBus
from pidog.companion.behavior.state import PetState, MoodType
from pidog.companion.behavior.behavior_engine import BehaviorEngine

class TestBehavior(unittest.TestCase):
    def test_touch_increases_intimacy_and_happiness(self):
        state = PetState()
        bus = EventBus()
        engine = BehaviorEngine(state, bus)
        
        bus.publish("sensor.touch.head", {"type": "touch"})
        self.assertGreater(state.intimacy, 50)
        self.assertEqual(state.mood, MoodType.HAPPY)

    def test_idle_decay_increases_boredom(self):
        state = PetState(boredom=60)
        state.tick(delta_seconds=30.0)
        self.assertGreater(state.boredom, 60)

if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行测试并验证失败**

Run: `python3 -m unittest test/companion/test_behavior.py`
Expected: FAIL with "No module named 'pidog.companion.behavior'"

- [ ] **Step 3: 实现 PetState 与 BehaviorEngine**

```python
# pidog/companion/behavior/__init__.py
"""Behavior and State Engine Package"""

# pidog/companion/behavior/state.py
from dataclasses import dataclass
from enum import Enum
import time

class MoodType(Enum):
    HAPPY = "happy"
    CURIOUS = "curious"
    NEUTRAL = "neutral"
    SCARED = "scared"
    SLEEPY = "sleepy"
    CONFUSED = "confused"

@dataclass
class PetState:
    energy: float = 100.0     # 0 ~ 100
    boredom: float = 0.0      # 0 ~ 100
    intimacy: float = 50.0    # 0 ~ 100
    mood: MoodType = MoodType.NEUTRAL
    last_interaction_time: float = time.time()

    def tick(self, delta_seconds: float = 1.0):
        # 精力缓慢衰减
        self.energy = max(0.0, self.energy - (0.05 * delta_seconds))
        # 无聊度随时间增长
        self.boredom = min(100.0, self.boredom + (0.1 * delta_seconds))
        if self.energy < 20:
            self.mood = MoodType.SLEEPY
        elif self.boredom > 70:
            self.mood = MoodType.NEUTRAL

    def on_interact(self, intimacy_bonus: float = 2.0, mood: MoodType = MoodType.HAPPY):
        self.last_interaction_time = time.time()
        self.boredom = max(0.0, self.boredom - 20.0)
        self.intimacy = min(100.0, self.intimacy + intimacy_bonus)
        self.mood = mood

# pidog/companion/behavior/behavior_engine.py
import threading
import time
from .state import PetState, MoodType
from ..core.event_bus import EventBus

class BehaviorEngine:
    def __init__(self, state: PetState, bus: EventBus):
        self.state = state
        self.bus = bus
        self._running = False
        self._thread = None
        self._setup_subscribers()

    def _setup_subscribers(self):
        self.bus.subscribe("sensor.touch.head", self._on_head_touch)
        self.bus.subscribe("sensor.touch.body", self._on_body_touch)
        self.bus.subscribe("sensor.imu.suspended", self._on_suspended)

    def _on_head_touch(self, data):
        self.state.on_interact(intimacy_bonus=3.0, mood=MoodType.HAPPY)
        self.bus.publish("actuator.express", {
            "emotion": "happy",
            "action": "wag_tail",
            "sound": "single_bark_1.mp3"
        })

    def _on_body_touch(self, data):
        self.state.on_interact(intimacy_bonus=1.5, mood=MoodType.HAPPY)
        self.bus.publish("actuator.express", {
            "emotion": "happy",
            "action": "stretch"
        })

    def _on_suspended(self, data):
        self.state.mood = MoodType.SCARED
        self.bus.publish("actuator.express", {
            "emotion": "scared",
            "action": "lie",
            "sound": "growl_1.mp3"
        })

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        last_t = time.time()
        while self._running:
            now = time.time()
            dt = now - last_t
            last_t = now
            self.state.tick(dt)

            # 自发心跳：无聊时偶发伸懒腰或叹气
            if self.state.boredom > 60 and (now - self.state.last_interaction_time > 15):
                self.bus.publish("actuator.express", {
                    "emotion": "neutral",
                    "action": "stretch",
                    "sound": "pant.mp3"
                })
                self.state.last_interaction_time = now

            time.sleep(2.0)
```

- [ ] **Step 4: 运行测试并验证通过**

Run: `python3 -m unittest test/companion/test_behavior.py`
Expected: PASS

- [ ] **Step 5: 提交代码**

```bash
git add pidog/companion/behavior/ test/companion/test_behavior.py
git commit -m "feat(companion): implement pet state model and autonomous behavior engine"
```

---

### Task 4: 硬件桥接层（传感器采集、摄像头抓帧与音动表现器）

**Files:**
- Create: `pidog/companion/hardware/__init__.py`
- Create: `pidog/companion/hardware/camera_helper.py`
- Create: `pidog/companion/hardware/sensor_worker.py`
- Create: `pidog/companion/hardware/emotion_expressor.py`
- Test: `test/companion/test_hardware_bridge.py`

**Interfaces:**
- Consumes: `Pidog` (可选/Mock), `EventBus`
- Produces: `CameraHelper`, `SensorWorker`, `EmotionExpressor`

- [ ] **Step 1: 编写硬件桥接 Mock 测试**

```python
# test/companion/test_hardware_bridge.py
import unittest
from unittest.mock import MagicMock
from pidog.companion.core.event_bus import EventBus
from pidog.companion.hardware.emotion_expressor import EmotionExpressor

class TestHardwareBridge(unittest.TestCase):
    def test_emotion_expressor_dispatches_action_and_rgb(self):
        mock_dog = MagicMock()
        bus = EventBus()
        expressor = EmotionExpressor(mock_dog, bus)

        bus.publish("actuator.express", {
            "emotion": "happy",
            "action": "wag_tail"
        })
        
        # 验证是否调用了 dog 动作方法或 rgb
        self.assertTrue(mock_dog.tail_move.called or mock_dog.rgb_strip.called or mock_dog.do_action.called)

if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行测试并验证失败**

Run: `python3 -m unittest test/companion/test_hardware_bridge.py`
Expected: FAIL with "No module named 'pidog.companion.hardware'"

- [ ] **Step 3: 实现 CameraHelper, SensorWorker, EmotionExpressor**

```python
# pidog/companion/hardware/__init__.py
"""Hardware Bridge Package"""

# pidog/companion/hardware/camera_helper.py
import io
import time
from typing import Optional

class CameraHelper:
    """按需单帧抓图工具类 (针对 Pi Zero 2W 优化，无长推流内存开销)"""
    @staticmethod
    def capture_frame_jpeg(quality: int = 75) -> Optional[bytes]:
        try:
            # 尝试调用 vilib 或 cv2 / picamera2
            import cv2
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return None
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            ret, frame = cap.read()
            cap.release()
            if not ret:
                return None
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
            _, encimg = cv2.imencode('.jpg', frame, encode_param)
            return encimg.tobytes()
        except Exception as e:
            print(f"[CameraHelper] Capture frame fallback/error: {e}")
            # 返回空或 Mock 字节用于无摄像头调试
            return None

# pidog/companion/hardware/sensor_worker.py
import threading
import time
from ..core.event_bus import EventBus

class SensorWorker:
    """低频传感器轮询工作线程 (20Hz)"""
    def __init__(self, dog_instance, bus: EventBus, poll_interval: float = 0.05):
        self.dog = dog_instance
        self.bus = bus
        self.interval = poll_interval
        self._running = False
        self._thread = None

    def start(self):
        if not self.dog:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _poll_loop(self):
        while self._running:
            try:
                # 1. 触摸传感器检查
                if hasattr(self.dog, 'dual_touch') and self.dog.dual_touch:
                    touch_state = self.dog.dual_touch.read()
                    if touch_state == 'N': # Head touch
                        self.bus.publish("sensor.touch.head", {"type": "head"})
                    elif touch_state in ['S', 'F', 'B']: # Body / Slide
                        self.bus.publish("sensor.touch.body", {"type": "body", "style": touch_state})

                # 2. 超声波距离检查
                if hasattr(self.dog, 'ultrasonic') and self.dog.ultrasonic:
                    dist = self.dog.ultrasonic.read()
                    if 0 < dist < 25.0:
                        self.bus.publish("sensor.ultrasonic.near", {"distance": dist})

                # 3. 声源方向检查
                if hasattr(self.dog, 'sound_direction') and self.dog.sound_direction:
                    direction = self.dog.sound_direction.read()
                    if direction and direction != -1:
                        self.bus.publish("sensor.sound.direction", {"angle": direction})

            except Exception as e:
                pass
            time.sleep(self.interval)

# pidog/companion/hardware/emotion_expressor.py
from ..core.event_bus import EventBus

class EmotionExpressor:
    """声、光、动协同表现器"""
    def __init__(self, dog_instance, bus: EventBus):
        self.dog = dog_instance
        self.bus = bus
        self.bus.subscribe("actuator.express", self.express)

    def express(self, payload: dict):
        if not self.dog:
            return
        emotion = payload.get("emotion", "neutral")
        action = payload.get("action")
        sound = payload.get("sound")

        # 1. 灯效映射
        if hasattr(self.dog, 'rgb_strip') and self.dog.rgb_strip:
            if emotion == "happy":
                self.dog.rgb_strip.set_mode('breath', 'green', bps=1.5)
            elif emotion == "curious":
                self.dog.rgb_strip.set_mode('boom', 'cyan', bps=2.0)
            elif emotion == "scared":
                self.dog.rgb_strip.set_mode('boom', 'red', bps=3.0)
            elif emotion == "confused":
                self.dog.rgb_strip.set_mode('breath', 'yellow', bps=1.0)
            else:
                self.dog.rgb_strip.set_mode('breath', 'white', bps=0.5)

        # 2. 动作映射
        if action:
            try:
                if action == "wag_tail":
                    if hasattr(self.dog, 'tail_move'):
                        self.dog.tail_move([[0], [45], [-45], [0]], speed=90)
                elif action in ["nod", "shake_head"]:
                    if hasattr(self.dog, 'head_move'):
                        self.dog.head_move([[0, 0, 15], [0, 0, -15], [0, 0, 0]], speed=80)
                else:
                    if hasattr(self.dog, 'do_action'):
                        self.dog.do_action(action, speed=85)
            except Exception as e:
                print(f"[EmotionExpressor] Action error: {e}")

        # 3. 音效触发
        if sound and hasattr(self.dog, 'speak'):
            try:
                self.dog.speak(sound)
            except Exception:
                pass
```

- [ ] **Step 4: 运行测试并验证通过**

Run: `python3 -m unittest test/companion/test_hardware_bridge.py`
Expected: PASS

- [ ] **Step 5: 提交代码**

```bash
git add pidog/companion/hardware/ test/companion/test_hardware_bridge.py
git commit -m "feat(companion): implement hardware bridge, camera helper and emotion expressor"
```

---

### Task 5: Orchestrator 核心调度器与意图动作解析

**Files:**
- Create: `pidog/companion/core/context.py`
- Create: `pidog/companion/core/orchestrator.py`
- Test: `test/companion/test_orchestrator.py`

**Interfaces:**
- Consumes: `EventBus`, `BaseASR`, `BaseTTS`, `BaseVLM`, `CameraHelper`
- Produces: `CompanionOrchestrator`, `ConversationContext`

- [ ] **Step 1: 编写标签解析与对话编排测试**

```python
# test/companion/test_orchestrator.py
import unittest
from unittest.mock import MagicMock
from pidog.companion.core.event_bus import EventBus
from pidog.companion.core.orchestrator import CompanionOrchestrator
from pidog.companion.config import CompanionConfig

class TestOrchestrator(unittest.TestCase):
    def test_parse_tags(self):
        bus = EventBus()
        mock_asr = MagicMock()
        mock_tts = MagicMock()
        mock_vlm = MagicMock()
        config = CompanionConfig()
        
        orch = CompanionOrchestrator(bus, mock_asr, mock_tts, mock_vlm, config)
        text = "[emotion:happy][action:wag_tail] 主人你好呀！"
        cleaned, actions, emotions = orch.extract_tags(text)
        
        self.assertEqual(cleaned, "主人你好呀！")
        self.assertIn("wag_tail", actions)
        self.assertIn("happy", emotions)

if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行测试并验证失败**

Run: `python3 -m unittest test/companion/test_orchestrator.py`
Expected: FAIL with "No module named 'pidog.companion.core.orchestrator'"

- [ ] **Step 3: 实现 Context 与 Orchestrator**

```python
# pidog/companion/core/context.py
from typing import List, Dict, Any

class ConversationContext:
    def __init__(self, max_history: int = 6):
        self.max_history = max_history
        self.history: List[Dict[str, Any]] = []

    def add_user_message(self, text: str):
        self.history.append({"role": "user", "content": text})
        self._trim()

    def add_assistant_message(self, text: str):
        self.history.append({"role": "assistant", "content": text})
        self._trim()

    def get_messages(self) -> List[Dict[str, Any]]:
        return list(self.history)

    def _trim(self):
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

# pidog/companion/core/orchestrator.py
import re
from typing import Tuple, List
from .event_bus import EventBus
from .context import ConversationContext
from ..adapters.base import BaseASR, BaseTTS, BaseVLM
from ..hardware.camera_helper import CameraHelper
from ..config import CompanionConfig

SYSTEM_PROMPT = """你是一只活泼、忠诚的机器狗 Pidog。请用可爱、简洁的小狗口吻回答。
你可以使用特殊标签表达动作与情绪：
动作标签: [action:wag_tail], [action:bark], [action:nod], [action:shake_head], [action:stretch], [action:sit], [action:lie]
情绪标签: [emotion:happy], [emotion:curious], [emotion:confused], [emotion:angry], [emotion:sleepy]
示例：[emotion:happy][action:wag_tail] 汪！主人我看到你啦！"""

class CompanionOrchestrator:
    def __init__(
        self,
        bus: EventBus,
        asr: BaseASR,
        tts: BaseTTS,
        vlm: BaseVLM,
        config: CompanionConfig
    ):
        self.bus = bus
        self.asr = asr
        self.tts = tts
        self.vlm = vlm
        self.config = config
        self.context = ConversationContext()
        self._setup_subscribers()

    def _setup_subscribers(self):
        self.bus.subscribe("voice.audio_input", self._on_voice_input)
        self.bus.subscribe("sensor.sound.direction", self._on_sound_turn)

    def _on_sound_turn(self, data):
        angle = data.get("angle", 0)
        # 发射动作转向声源
        self.bus.publish("actuator.express", {
            "emotion": "curious",
            "action": "nod"
        })

    def extract_tags(self, text: str) -> Tuple[str, List[str], List[str]]:
        actions = re.findall(r'\[action:([a-zA-Z0-9_]+)\]', text)
        emotions = re.findall(r'\[emotion:([a-zA-Z0-9_]+)\]', text)
        cleaned = re.sub(r'\[(action|emotion):[a-zA-Z0-9_]+\]', '', text).strip()
        return cleaned, actions, emotions

    def _on_voice_input(self, audio_data: bytes):
        # 1. ASR 转写
        user_text = self.asr.recognize(audio_data)
        if not user_text.strip():
            return
        
        self.context.add_user_message(user_text)

        # 2. 视觉意图识别
        image_bytes = None
        has_visual_intent = any(kw in user_text for kw in self.config.visual_intent_keywords)
        if has_visual_intent:
            image_bytes = CameraHelper.capture_frame_jpeg()

        # 3. MiniMax M3 多模态生成
        response_text = self.vlm.chat(
            messages=self.context.get_messages(),
            image_bytes=image_bytes,
            system_prompt=SYSTEM_PROMPT
        )
        self.context.add_assistant_message(response_text)

        # 4. 解析标签与具身派发
        cleaned_text, actions, emotions = self.extract_tags(response_text)
        
        # 同步派发动作与情绪
        primary_action = actions[0] if actions else "wag_tail"
        primary_emotion = emotions[0] if emotions else "happy"
        self.bus.publish("actuator.express", {
            "emotion": primary_emotion,
            "action": primary_action
        })

        # 5. TTS 合成与音频广播
        if cleaned_text:
            audio_out = self.tts.synthesize(cleaned_text)
            if audio_out:
                self.bus.publish("audio.play", {"data": audio_out})
```

- [ ] **Step 4: 运行测试并验证通过**

Run: `python3 -m unittest test/companion/test_orchestrator.py`
Expected: PASS

- [ ] **Step 5: 提交代码**

```bash
git add pidog/companion/core/ test/companion/test_orchestrator.py
git commit -m "feat(companion): implement orchestrator, tag extraction and dialogue pipeline"
```

---

### Task 6: 场景演示与全系统集成入口

**Files:**
- Create: `examples/21_embodied_pet_companion.py`
- Modify: `pidog/__init__.py:1-15`
- Test: `test/companion/test_integration_flow.py`

**Interfaces:**
- Consumes: `Pidog`, `CompanionConfig`, `EventBus`, `CompanionOrchestrator`, `BehaviorEngine`, `SensorWorker`, `EmotionExpressor`
- Produces: 完整场景启动脚本 `examples/21_embodied_pet_companion.py`

- [ ] **Step 1: 编写全链路端到端集成测试**

```python
# test/companion/test_integration_flow.py
import unittest
from unittest.mock import MagicMock
from pidog.companion.config import CompanionConfig
from pidog.companion.core.event_bus import EventBus
from pidog.companion.behavior.state import PetState
from pidog.companion.behavior.behavior_engine import BehaviorEngine
from pidog.companion.hardware.emotion_expressor import EmotionExpressor
from pidog.companion.core.orchestrator import CompanionOrchestrator

class TestIntegrationFlow(unittest.TestCase):
    def test_full_touch_to_dialogue_pipeline(self):
        bus = EventBus()
        config = CompanionConfig()
        state = PetState()
        mock_dog = MagicMock()
        
        mock_asr = MagicMock()
        mock_asr.recognize.return_value = "你看看我"
        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = b"fake_mp3"
        mock_vlm = MagicMock()
        mock_vlm.chat.return_value = "[emotion:happy][action:wag_tail] 看到你啦！"

        expressor = EmotionExpressor(mock_dog, bus)
        behavior = BehaviorEngine(state, bus)
        orchestrator = CompanionOrchestrator(bus, mock_asr, mock_tts, mock_vlm, config)

        # 模拟触摸唤醒后输入语音
        bus.publish("sensor.touch.head", {})
        bus.publish("voice.audio_input", b"mock_pcm")

        self.assertTrue(mock_vlm.chat.called)
        self.assertTrue(mock_tts.synthesize.called)

if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 运行测试并验证通过**

Run: `python3 -m unittest test/companion/test_integration_flow.py`
Expected: PASS

- [ ] **Step 3: 编写场景入口示例脚本**

```python
# examples/21_embodied_pet_companion.py
#!/usr/bin/env python3
"""
Pidog 具身多模态智能伴侣 & 自主宠物场景演示
运行环境：Raspberry Pi Zero 2W / Ubuntu / macOS (Mock)
"""
import time
import sys
from pidog import Pidog
from pidog.companion.config import CompanionConfig
from pidog.companion.core.event_bus import EventBus
from pidog.companion.behavior.state import PetState
from pidog.companion.behavior.behavior_engine import BehaviorEngine
from pidog.companion.adapters.factory import AdapterFactory
from pidog.companion.hardware.sensor_worker import SensorWorker
from pidog.companion.hardware.emotion_expressor import EmotionExpressor
from pidog.companion.core.orchestrator import CompanionOrchestrator

def main():
    print("=== 初始化 Pidog 具身智能伴侣系统 ===")
    config = CompanionConfig()
    bus = EventBus()
    state = PetState()

    try:
        dog = Pidog()
    except Exception as e:
        print(f"[Warning] 未检测到真实硬件，进入模拟模式: {e}")
        dog = None

    # 初始化各子系统
    asr = AdapterFactory.create_asr(config)
    tts = AdapterFactory.create_tts(config)
    vlm = AdapterFactory.create_vlm(config)

    expressor = EmotionExpressor(dog, bus)
    sensor_worker = SensorWorker(dog, bus, config.sensor_poll_interval_s)
    behavior_engine = BehaviorEngine(state, bus)
    orchestrator = CompanionOrchestrator(bus, asr, tts, vlm, config)

    # 启动工作线程
    sensor_worker.start()
    behavior_engine.start()

    print(">>> 伴侣系统已就绪！")
    print(">>> 交互方式：抚摸头部/背部唤醒，拍手定位，或输入文本进行多模态测试。")
    print(">>> 按 Ctrl+C 退出。")

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n正在安全退出系统...")
        sensor_worker.stop()
        behavior_engine.stop()
        if dog:
            dog.close()
        print("已完成退出。")

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行全套测试套件验证**

Run: `python3 -m unittest discover -s test/companion`
Expected: ALL PASS

- [ ] **Step 5: 提交代码**

```bash
git add examples/21_embodied_pet_companion.py test/companion/ pidog/
git commit -m "feat(companion): add full embodied pet companion example and integration pipeline"
```

---

## Self-Review Checklist
1. **Spec coverage:** 覆盖了多模态 MiniMax VLM、小米 ASR/TTS、主动视觉意图抓帧、物理/触摸多模态唤醒、PetState 行为引擎与音动协同。
2. **Placeholder scan:** 无 TBD/TODO，代码完整包含入参与出参。
3. **Type consistency:** EventBus, Adapters, Orchestrator 各任务方法名与入参统一规范。
