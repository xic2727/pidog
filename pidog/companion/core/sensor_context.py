"""Rolling sensor/event context snapshot fed into LLM prompts.

Subscribes to sensor and interaction topics on the EventBus and keeps a short
recent-events window plus a few key live values (last sound direction, touch
counts, obstacle distance, battery). `summary()` renders a compact Chinese
digest appended to dialogue prompts so the mute dog's LLM "knows" what has
been happening around it and can pick the right sound and behavior.
"""
import time
import threading
import logging
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# Topic -> human label used in the prompt summary
TOPIC_LABELS = {
    "sensor.touch.head": "摸头",
    "sensor.touch.body": "摸背",
    "sensor.touch.stroke_forward": "顺毛抚摸",
    "sensor.touch.stroke_backward": "逆毛抚摸",
    "sensor.imu.suspended": "被抱起",
    "sensor.clap.detected": "拍手",
    "sensor.ultrasonic.obstacle": "障碍物靠近",
}


class SensorContext:
    """Recent sensor/interaction event snapshot for LLM prompt conditioning."""

    def __init__(
        self,
        bus: Any,
        state: Optional[Any] = None,
        max_events: int = 40,
        max_age: float = 180.0,
        sound_direction_max_age: float = 8.0,
    ):
        """
        :param bus: EventBus to subscribe on
        :param state: optional PetState for mood/energy/intimacy/owner mood
        :param max_events: max recent labeled events kept
        :param max_age: events older than this (seconds) are dropped at summary time
        :param sound_direction_max_age: sound direction considered stale after this
        """
        self.bus = bus
        self.state = state
        self.max_events = max_events
        self.max_age = max_age
        self.sound_direction_max_age = sound_direction_max_age

        self._lock = threading.RLock()
        self._events: Deque[Tuple[float, str, Any]] = deque(maxlen=max_events)
        self._last_sound_direction: Optional[float] = None
        self._last_sound_direction_time: float = 0.0
        self._last_distance: Optional[float] = None
        self._last_voltage: Optional[float] = None

        self._unsubs = [
            bus.subscribe(topic, self._make_handler(topic))
            for topic in TOPIC_LABELS
        ]
        self._unsubs.append(bus.subscribe("sensor.sound.direction", self._on_sound_direction))
        self._unsubs.append(bus.subscribe("sensor.ultrasonic.distance", self._on_distance))
        self._unsubs.append(bus.subscribe("sensor.battery.voltage", self._on_voltage))

    def close(self):
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:
                pass
        self._unsubs = []

    # ------------------------------------------------------------------ #
    # Event handlers
    # ------------------------------------------------------------------ #

    def _make_handler(self, topic: str):
        def handler(data: Any):
            with self._lock:
                self._events.append((time.time(), topic, data))
        return handler

    def _on_sound_direction(self, data: Any):
        if isinstance(data, dict) and data.get("angle") is not None:
            with self._lock:
                self._last_sound_direction = float(data["angle"])
                self._last_sound_direction_time = time.time()

    def _on_distance(self, data: Any):
        if isinstance(data, dict) and data.get("distance") is not None:
            with self._lock:
                self._last_distance = float(data["distance"])

    def _on_voltage(self, data: Any):
        if isinstance(data, dict) and data.get("voltage") is not None:
            with self._lock:
                self._last_voltage = float(data["voltage"])

    # ------------------------------------------------------------------ #
    # Snapshot accessors
    # ------------------------------------------------------------------ #

    @property
    def last_sound_direction(self) -> Optional[float]:
        """Most recent sound direction angle (0~355) if still fresh, else None."""
        with self._lock:
            if (time.time() - self._last_sound_direction_time) <= self.sound_direction_max_age:
                return self._last_sound_direction
            return None

    # ------------------------------------------------------------------ #
    # Prompt summary
    # ------------------------------------------------------------------ #

    def summary(self) -> str:
        """Compact Chinese digest of the dog's current situation."""
        now = time.time()
        with self._lock:
            events = [(t, topic, data) for (t, topic, data) in self._events if now - t <= self.max_age]
            sound_dir = self._last_sound_direction if (now - self._last_sound_direction_time) <= self.sound_direction_max_age else None
            distance = self._last_distance
            voltage = self._last_voltage

        parts = []

        # Interaction digest: aggregate labeled events per topic
        counts: Dict[str, int] = {}
        last_times: Dict[str, float] = {}
        for t, topic, _data in events:
            label = TOPIC_LABELS.get(topic)
            if not label:
                continue
            counts[label] = counts.get(label, 0) + 1
            last_times[label] = max(last_times.get(label, 0.0), t)
        if counts:
            bits = []
            for label in sorted(counts, key=counts.get, reverse=True):
                ago = int(now - last_times[label])
                bits.append(f"{label}x{counts[label]}(最近一次{ago}秒前)")
            parts.append("最近的互动: " + "、".join(bits))

        if sound_dir is not None:
            parts.append(f"刚才声音来自{sound_dir:.0f}度方向(0度=正前方,角度顺时针增大)")

        if distance is not None:
            parts.append(f"前方障碍物距离约{distance:.0f}厘米")

        if voltage is not None:
            parts.append(f"电池电压{voltage:.1f}V")

        if self.state is not None:
            try:
                state_bits = [
                    f"当前心情:{self.state.mood.value}",
                    f"亲密度:{self.state.intimacy:.0f}/100({self.state.intimacy_level})",
                    f"精力:{self.state.energy:.0f}",
                ]
                if getattr(self.state, "owner_mood", "neutral") != "neutral":
                    state_bits.append(f"主人此前情绪:{self.state.owner_mood}")
                if getattr(self.state, "is_sulking", False):
                    state_bits.append("你正在闹小脾气")
                parts.append("、".join(state_bits))
            except Exception:
                pass

        if not parts:
            return ""
        return "【小狗的近况】" + ";".join(parts)
