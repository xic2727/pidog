import threading
import time
import logging
from typing import Optional
from .state import PetState, MoodType
from ..core.event_bus import EventBus

logger = logging.getLogger(__name__)


class BehaviorEngine:
    """
    Autonomous behavior engine for Pidog.
    Subscribes to touch and sensor events, manages state updates,
    and runs periodic autonomous heartbeats to emit expressive behaviors.
    """

    def __init__(self, state: PetState, bus: EventBus, interval: float = 0.1):
        self.state = state
        self.bus = bus
        self.interval = interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._unsubscribers = []
        self._setup_subscribers()

    def _setup_subscribers(self):
        self._unsubscribers.append(self.bus.subscribe("sensor.touch.head", self._on_head_touch))
        self._unsubscribers.append(self.bus.subscribe("sensor.touch.body", self._on_body_touch))
        self._unsubscribers.append(self.bus.subscribe("sensor.imu.suspended", self._on_suspended))
        self._unsubscribers.append(self.bus.subscribe("sensor.battery.low", self._on_battery_low))
        self._unsubscribers.append(self.bus.subscribe("sensor.battery.critical", self._on_battery_critical))

    def _on_battery_low(self, data):
        """Handle low battery event: reduce energy, set sleepy mood, express tired behavior."""
        self.state.energy = min(self.state.energy, 15.0)
        self.state.mood = MoodType.SLEEPY
        self.bus.publish("actuator.express", {
            "emotion": "sleepy",
            "action": "pant",
            "rgb": {"style": "breath", "color": "orange", "bps": 0.8},
            "sound": "pant.mp3",
            "speak_text": "主人，我的电量有点低了，记得给我充电哦！"
        })

    def _on_battery_critical(self, data):
        """Handle critical battery event: drain energy to 0, lie down, alert loudly."""
        self.state.energy = 0.0
        self.state.mood = MoodType.SAD
        self.bus.publish("actuator.express", {
            "emotion": "sad",
            "action": "lie",
            "rgb": {"style": "boom", "color": "red", "bps": 2.0},
            "sound": "growl_1.mp3",
            "speak_text": "主人，我快要没电啦，马上就要休息了，快帮我充电吧！"
        })

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
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            self._thread = None

    def _loop(self):
        last_t = time.time()
        while self._running:
            now = time.time()
            dt = now - last_t
            last_t = now
            self.state.tick(dt)

            # Autonomous heartbeat: when bored and no recent interaction
            if self.state.boredom > 60.0 and (now - self.state.last_interaction_time > 15.0):
                self.bus.publish("actuator.express", {
                    "emotion": "neutral",
                    "action": "stretch",
                    "sound": "pant.mp3"
                })
                self.state.last_interaction_time = now

            time.sleep(self.interval)
