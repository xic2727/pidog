import random
import threading
import time
import logging
from typing import Any, Dict, Optional
from .state import PetState, MoodType
from ..core.event_bus import EventBus

logger = logging.getLogger(__name__)


# Praise keywords detected in owner speech (bonus intimacy, reflex level)
PRAISE_KEYWORDS = ["乖", "好棒", "真棒", "好孩子", "好乖", "真乖", "good boy", "good dog", "well done", "好样的"]

# Head-touch reaction pools per intimacy level. Higher levels unlock more
# coquettish/enchanted reactions (撒娇 -> 陶醉).
HEAD_TOUCH_POOLS: Dict[str, list] = {
    "stranger": [
        {"emotion": "curious", "action": "tilting_head", "sound": "happy_bark"},
    ],
    "familiar": [
        {"emotion": "happy", "action": "wag_tail", "sound": "happy_bark"},
        {"emotion": "happy", "action": "head_up_down", "sound": "happy_bark"},
    ],
    "close": [
        # 撒娇: lower head to rub against the hand + pleased groan
        {"emotion": "happy", "action": "wag_tail", "sound": "coquettish",
         "head": [[0, 10, -15], [0, -10, -15], [0, 0, -10]]},
        {"emotion": "happy", "action": "head_up_down", "sound": "coquettish"},
    ],
    "devoted": [
        # 陶醉: fully surrendered belly-rub pose + enchanted groan
        {"emotion": "excited", "action": "lie_with_hands_out", "sound": "enchanted"},
        {"emotion": "happy", "action": "wag_tail", "sound": "enchanted",
         "head": [[0, 15, -20], [0, -15, -20], [0, 0, -15]]},
    ],
}

# Stroke reactions (petting along the body)
STROKE_REACTIONS = [
    {"emotion": "happy", "action": "wag_tail", "sound": "pant"},
    {"emotion": "happy", "action": "stretch", "sound": "pant"},
]

# Sulking first-touch reaction: turn head away, soft grumble
SULK_REACTION = {
    "emotion": "sad", "sound": "whine",
    "head": [[0, -40, 0], [0, -40, 10]],
}

# Being picked up: bliss when close to the owner, struggle when not
PICKED_UP_BLISS = {
    "emotion": "excited", "action": "lie_with_hands_out", "sound": "enchanted",
    "rgb": {"style": "breath", "color": "pink", "bps": 0.8},
}
PICKED_UP_SCARED = {
    "emotion": "scared", "action": "lie", "sound": "growl",
    "rgb": {"style": "bark", "color": "red", "bps": 3.0},
}

# Clap command mapping (reflex level, no LLM):
#   1 clap -> sit, 2 claps -> spin around, 3 claps -> come to the owner
CLAP_ACTIONS = {
    1: {"emotion": "happy", "action": "sit", "step_count": 1, "sound": "happy_bark"},
    2: {"emotion": "excited", "action": "turn_right", "step_count": 8, "sound": "excited_bark"},
    3: None,  # approach: handled separately with sound direction
}


class BehaviorEngine:
    """
    Autonomous reflex behavior engine for the mute companion Pidog.
    Subscribes to touch / IMU / clap / battery / sound-direction events,
    maintains pet state (intimacy levels, sulking, owner mood), and emits
    immediate expressive behaviors (no LLM roundtrip, no TTS speech).

    All vocalization goes through semantic sound tags resolved by the
    SoundLibrary in EmotionExpressor.
    """

    def __init__(
        self,
        state: PetState,
        bus: EventBus,
        interval: float = 0.1,
        touch_cooldown: float = 1.5,
        neglect_timeout: float = 300.0,
    ):
        """
        :param touch_cooldown: minimum seconds between two full touch reactions
        :param neglect_timeout: seconds without interaction before sulking
                (only for close/devoted intimacy levels)
        """
        self.state = state
        self.bus = bus
        self.interval = interval
        self.touch_cooldown = touch_cooldown
        self.neglect_timeout = neglect_timeout

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._unsubscribers = []
        self._last_touch_reaction = 0.0
        self._last_sound_direction: Optional[float] = None
        self._last_sound_direction_time = 0.0

        self._setup_subscribers()

    def _setup_subscribers(self):
        self._unsubscribers.append(self.bus.subscribe("sensor.touch.head", self._on_head_touch))
        self._unsubscribers.append(self.bus.subscribe("sensor.touch.body", self._on_body_touch))
        self._unsubscribers.append(self.bus.subscribe("sensor.touch.stroke_forward", self._on_stroke))
        self._unsubscribers.append(self.bus.subscribe("sensor.touch.stroke_backward", self._on_stroke))
        self._unsubscribers.append(self.bus.subscribe("sensor.imu.suspended", self._on_suspended))
        self._unsubscribers.append(self.bus.subscribe("sensor.clap.detected", self._on_clap))
        self._unsubscribers.append(self.bus.subscribe("sensor.sound.direction", self._on_sound_direction))
        self._unsubscribers.append(self.bus.subscribe("sensor.battery.low", self._on_battery_low))
        self._unsubscribers.append(self.bus.subscribe("sensor.battery.critical", self._on_battery_critical))
        self._unsubscribers.append(self.bus.subscribe("dialogue.response", self._on_dialogue_response))

    # ------------------------------------------------------------------ #
    # Sensor reflexes
    # ------------------------------------------------------------------ #

    def _on_battery_low(self, data):
        """Low battery: reduce energy, sleepy mood, tired panting."""
        self.state.energy = min(self.state.energy, 15.0)
        self.state.mood = MoodType.SLEEPY
        self.bus.publish("actuator.express", {
            "emotion": "sleepy",
            "action": "pant",
            "rgb": {"style": "breath", "color": "orange", "bps": 0.8},
            "sound": "pant",
        })

    def _on_battery_critical(self, data):
        """Critical battery: drain energy, lie down, sad whine."""
        self.state.energy = 0.0
        self.state.mood = MoodType.SAD
        self.bus.publish("actuator.express", {
            "emotion": "sad",
            "action": "lie",
            "rgb": {"style": "boom", "color": "red", "bps": 2.0},
            "sound": "whine",
        })

    def _on_head_touch(self, data):
        """Head touch: intimacy-gated coquettish reactions (撒娇 -> 陶醉)."""
        was_sulking = self.state.on_interact(intimacy_bonus=3.0, mood=MoodType.HAPPY)

        # First touch after a long neglect: turn head away and sulk
        if was_sulking:
            self.bus.publish("actuator.express", dict(SULK_REACTION))
            return

        now = time.time()
        if now - self._last_touch_reaction < self.touch_cooldown:
            return
        self._last_touch_reaction = now

        level = self.state.intimacy_level
        pool = HEAD_TOUCH_POOLS.get(level) or HEAD_TOUCH_POOLS["familiar"]
        self.bus.publish("actuator.express", dict(random.choice(pool)))

    def _on_body_touch(self, data):
        """Body touch: friendly stretch."""
        self.state.on_interact(intimacy_bonus=1.5, mood=MoodType.HAPPY)
        now = time.time()
        if now - self._last_touch_reaction < self.touch_cooldown:
            return
        self._last_touch_reaction = now
        self.bus.publish("actuator.express", {
            "emotion": "happy",
            "action": "stretch",
            "sound": "pant",
        })

    def _on_stroke(self, data):
        """Stroking along the body: happy panting."""
        self.state.on_interact(intimacy_bonus=1.0, mood=MoodType.HAPPY)
        now = time.time()
        if now - self._last_touch_reaction < self.touch_cooldown:
            return
        self._last_touch_reaction = now
        self.bus.publish("actuator.express", dict(random.choice(STROKE_REACTIONS)))

    def _on_suspended(self, data):
        """Picked up: blissful cuddle when close, scared struggle otherwise."""
        if self.state.intimacy_level in ("close", "devoted"):
            self.state.on_interact(intimacy_bonus=2.0, mood=MoodType.EXCITED)
            self.bus.publish("actuator.express", dict(PICKED_UP_BLISS))
        else:
            self.state.mood = MoodType.SCARED
            self.bus.publish("actuator.express", dict(PICKED_UP_SCARED))

    def _on_clap(self, data):
        """Clap-count commands: 1 sit / 2 spin / 3 come here."""
        if not isinstance(data, dict):
            return
        count = data.get("count")
        if count not in CLAP_ACTIONS and count != 3:
            return

        self.state.on_interact(intimacy_bonus=1.0, mood=MoodType.HAPPY)
        if count == 3:
            # Come here: use the freshest sound direction if available
            fresh = (time.time() - self._last_sound_direction_time) < 5.0
            angle = self._last_sound_direction if fresh else None
            if angle is None:
                # No direction known: curious look around instead
                self.bus.publish("actuator.express", {
                    "emotion": "confused", "action": "shake_head", "sound": "confused",
                })
                return
            self.bus.publish("actuator.express", {
                "emotion": "excited", "sound": "excited_bark",
            })
            self.bus.publish("behavior.approach", {"angle": angle})
        else:
            self.bus.publish("actuator.express", dict(CLAP_ACTIONS[count]))

    def _on_sound_direction(self, data):
        """Track the freshest sound direction for approach commands."""
        if isinstance(data, dict) and data.get("angle") is not None:
            self._last_sound_direction = float(data["angle"])
            self._last_sound_direction_time = time.time()

    # ------------------------------------------------------------------ #
    # Dialogue feedback (owner emotion & praise from the LLM layer)
    # ------------------------------------------------------------------ #

    def _on_dialogue_response(self, data: Any):
        """Update owner mood / intimacy from completed dialogue turns."""
        if not isinstance(data, dict):
            return
        owner_emotion = data.get("owner_emotion")
        if owner_emotion:
            self.state.owner_mood = str(owner_emotion)

        prompt = str(data.get("prompt", ""))
        prompt_lower = prompt.lower()
        if any(kw in prompt_lower for kw in PRAISE_KEYWORDS):
            self.state.on_interact(intimacy_bonus=5.0, mood=MoodType.HAPPY)

    # ------------------------------------------------------------------ #
    # Heartbeat loop
    # ------------------------------------------------------------------ #

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
        for unsub in self._unsubscribers:
            try:
                unsub()
            except Exception:
                pass
        self._unsubscribers = []

    def _loop(self):
        last_t = time.time()
        while self._running:
            now = time.time()
            dt = now - last_t
            last_t = now
            self.state.tick(dt)

            # Sulking: neglected for too long while closely bonded
            if (
                not self.state.is_sulking
                and self.state.intimacy_level in ("close", "devoted")
                and self.state.neglected_seconds > self.neglect_timeout
            ):
                self.state.is_sulking = True
                self.state.mood = MoodType.SAD
                self.bus.publish("actuator.express", {
                    "emotion": "sad",
                    "action": "sit",
                    "head": [[0, -40, 0]],
                    "sound": "whine",
                })
                self.state.last_interaction_time = now

            # Autonomous heartbeat: bored and idle -> amuse itself
            elif self.state.boredom > 60.0 and (now - self.state.last_interaction_time > 15.0):
                self.bus.publish("actuator.express", {
                    "emotion": "neutral",
                    "action": "stretch",
                    "sound": "pant",
                })
                self.state.last_interaction_time = now

            time.sleep(self.interval)
