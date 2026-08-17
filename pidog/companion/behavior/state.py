from dataclasses import dataclass, field
from enum import Enum
import time
import threading


class MoodType(Enum):
    HAPPY = "happy"
    CURIOUS = "curious"
    NEUTRAL = "neutral"
    SCARED = "scared"
    SLEEPY = "sleepy"
    CONFUSED = "confused"
    SAD = "sad"
    EXCITED = "excited"


# Intimacy level thresholds (affects reaction pools and unlocked behaviors)
INTIMACY_LEVELS = {
    "stranger": (0.0, 30.0),    # 陌生: polite but reserved
    "familiar": (30.0, 60.0),   # 熟悉: friendly tail wags
    "close": (60.0, 85.0),      # 亲密: coquettish bliss, comes when called
    "devoted": (85.0, 100.0),   # 黏人: full cuddle mode, sulks when neglected
}


def intimacy_level(intimacy: float) -> str:
    """Map a raw intimacy value (0-100) to its level name."""
    for level, (low, high) in INTIMACY_LEVELS.items():
        if low <= intimacy < high:
            return level
    return "devoted" if intimacy >= 100 else "stranger"


@dataclass
class PetState:
    energy: float = 100.0     # 0 ~ 100
    boredom: float = 0.0      # 0 ~ 100
    intimacy: float = 50.0    # 0 ~ 100
    mood: MoodType = MoodType.NEUTRAL
    # Perceived owner emotion from ASR/LLM (happy/sad/angry/tired/neutral)
    owner_mood: str = "neutral"
    # Sulking flag: set after long neglect, first touch gets the cold shoulder
    is_sulking: bool = False
    last_interaction_time: float = None

    def __post_init__(self):
        if self.last_interaction_time is None:
            self.last_interaction_time = time.time()
        self._lock = threading.RLock()

    def tick(self, delta_seconds: float = 1.0):
        """Periodic decay energy and increase boredom."""
        with self._lock:
            # Energy decays slowly
            self.energy = max(0.0, min(100.0, self.energy - (0.05 * delta_seconds)))
            # Boredom increases over time
            self.boredom = max(0.0, min(100.0, self.boredom + (0.1 * delta_seconds)))

            if self.energy < 20.0:
                self.mood = MoodType.SLEEPY
            elif self.boredom > 70.0:
                self.mood = MoodType.NEUTRAL

    def on_interact(self, intimacy_bonus: float = 2.0, mood: MoodType = MoodType.HAPPY):
        """Handle interaction event to reduce boredom and increase intimacy."""
        with self._lock:
            self.last_interaction_time = time.time()
            self.boredom = max(0.0, self.boredom - 20.0)
            self.intimacy = max(0.0, min(100.0, self.intimacy + intimacy_bonus))
            self.mood = mood
            was_sulking = self.is_sulking
            self.is_sulking = False
            return was_sulking

    @property
    def intimacy_level(self) -> str:
        """Current intimacy level name (stranger/familiar/close/devoted)."""
        with self._lock:
            return intimacy_level(self.intimacy)

    @property
    def neglected_seconds(self) -> float:
        """Seconds elapsed since the last interaction."""
        with self._lock:
            return max(0.0, time.time() - self.last_interaction_time)
