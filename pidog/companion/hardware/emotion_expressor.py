import logging
import threading
from typing import Optional, Any, Dict
from ..core.event_bus import EventBus

logger = logging.getLogger(__name__)


# Emotion to RGB LED mapping: (style, color, bps)
EMOTION_RGB_MAP = {
    "happy": ("breath", "yellow", 1.5),
    "excited": ("boom", "pink", 2.0),
    "sad": ("breath", "blue", 0.5),
    "scared": ("bark", "red", 3.0),
    "angry": ("boom", "red", 2.5),
    "sleepy": ("breath", "cyan", 0.3),
    "neutral": ("breath", "green", 1.0),
    "listen": ("listen", "yellow", 1.0),
    "speak": ("speak", "blue", 1.5),
}

# Emotion to preset physical movements / gestures
EMOTION_ACTION_MAP = {
    "happy": "wag_tail",
    "excited": "pant",
    "sad": "lie",
    "scared": "lie",
    "sleepy": "doze_off",
    "curious": "head_nod",
    "neutral": "stretch",
}

# Head preset poses: target_yrp = [yaw, roll, pitch]
EMOTION_HEAD_MAP = {
    "curious": [[20, 15, 10], [-20, -15, 10], [0, 0, 0]],
    "happy": [[0, 0, 20], [0, 0, -10], [0, 0, 0]],
    "confused": [[0, 25, 0], [0, -25, 0], [0, 0, 0]],
    "scared": [[0, 0, -30]],
    "neutral": [[0, 0, 0]],
}

# Tail preset angles
EMOTION_TAIL_MAP = {
    "happy": [[40], [-40], [40], [-40], [0]],
    "excited": [[60], [-60], [60], [-60], [60], [-60], [0]],
    "sad": [[0]],
    "scared": [[-30]],
}


class EmotionExpressor:
    """
    Subscribes to 'actuator.express' events on the EventBus and drives
    physical actuators on the Pidog instance:
    - RGB LED strip (modes/colors)
    - Head & Tail servo movements
    - Body preset actions (do_action)
    - Sound effects & TTS speech output
    """

    def __init__(self, dog: Any, bus: EventBus):
        """
        :param dog: Instance of Pidog or mock Pidog.
        :param bus: EventBus instance.
        """
        self.dog = dog
        self.bus = bus
        self._unsub = None
        self._subscribe()

    def _subscribe(self):
        self._unsub = self.bus.subscribe("actuator.express", self.express)

    def close(self):
        """Unsubscribe and cleanup."""
        if self._unsub:
            self._unsub()
            self._unsub = None

    def express(self, command: Dict[str, Any]):
        """
        Handle 'actuator.express' command.
        Expected command dict format:
        {
            "emotion": "happy" | "scared" | "sad" | "sleepy" | "neutral" | "excited" | ...,
            "action": "wag_tail" | "bark" | "stretch" | ... (optional preset action name),
            "rgb": {"style": "breath", "color": "yellow", "bps": 1.0} (optional explicit RGB),
            "head": [[0, 0, 10], ...] (optional explicit head motion list),
            "tail": [[30], [-30], ...] (optional explicit tail motion list),
            "sound": "single_bark_1.mp3" | ... (optional sound effect filename),
            "speak_text": "Hello master" (optional TTS text),
            "speed": 50 (optional movement speed)
        }
        """
        if not isinstance(command, dict):
            return

        emotion = command.get("emotion", "neutral")
        action = command.get("action")
        rgb = command.get("rgb")
        head = command.get("head")
        tail = command.get("tail")
        sound = command.get("sound")
        speak_text = command.get("speak_text")
        speed = command.get("speed", 50)

        # 1. Update RGB LED Strip
        self._set_rgb(emotion, rgb)

        # 2. Drive Head Movements
        if head is not None:
            self._move_head(head, speed=speed)
        elif emotion in EMOTION_HEAD_MAP and action is None:
            self._move_head(EMOTION_HEAD_MAP[emotion], speed=speed)

        # 3. Drive Tail Movements
        if tail is not None:
            self._move_tail(tail, speed=speed)
        elif emotion in EMOTION_TAIL_MAP and action is None:
            self._move_tail(EMOTION_TAIL_MAP[emotion], speed=speed)

        # 4. Drive Body Preset Action
        if action is not None:
            self._do_action(action, speed=speed)

        # 5. Play Sound Effect
        if sound:
            self._play_sound(sound)

        # 6. Speak Text if specified
        if speak_text:
            self._speak(speak_text)

    def _set_rgb(self, emotion: str, custom_rgb: Optional[Dict[str, Any]] = None):
        """Set RGB strip style and color."""
        try:
            rgb_strip = getattr(self.dog, "rgb_strip", None)
            if rgb_strip is None:
                return

            if custom_rgb and isinstance(custom_rgb, dict):
                style = custom_rgb.get("style", "breath")
                color = custom_rgb.get("color", "white")
                bps = custom_rgb.get("bps", 1.0)
            elif emotion in EMOTION_RGB_MAP:
                style, color, bps = EMOTION_RGB_MAP[emotion]
            else:
                style, color, bps = ("breath", "white", 1.0)

            if hasattr(rgb_strip, "set_mode"):
                rgb_strip.set_mode(style, color, bps=bps)
        except Exception as e:
            logger.debug(f"RGB strip express error: {e}")

    def _move_head(self, target_yrps, speed: int = 50):
        """Move head servos safely."""
        try:
            if hasattr(self.dog, "head_move"):
                self.dog.head_move(target_yrps, immediately=False, speed=speed)
        except Exception as e:
            logger.debug(f"Head move error: {e}")

    def _move_tail(self, target_angles, speed: int = 50):
        """Move tail servo safely."""
        try:
            if hasattr(self.dog, "tail_move"):
                self.dog.tail_move(target_angles, immediately=False, speed=speed)
        except Exception as e:
            logger.debug(f"Tail move error: {e}")

    def _do_action(self, action_name: str, speed: int = 50):
        """Perform preset dog action."""
        try:
            if hasattr(self.dog, "do_action"):
                self.dog.do_action(action_name, speed=speed)
        except Exception as e:
            logger.debug(f"Do action error: {e}")

    def _play_sound(self, sound_name: str):
        """Play sound effect file using Pidog music/speaker."""
        try:
            if hasattr(self.dog, "music") and self.dog.music:
                if hasattr(self.dog.music, "sound_play"):
                    self.dog.music.sound_play(sound_name)
                elif hasattr(self.dog.music, "play"):
                    self.dog.music.play(sound_name)
        except Exception as e:
            logger.debug(f"Play sound error: {e}")

    def _speak(self, text: str):
        """Publish or trigger TTS speak if requested."""
        try:
            if hasattr(self.dog, "speak"):
                self.dog.speak(text)
            else:
                # Publish event for TTS adapter worker to consume
                self.bus.publish("actuator.speak", {"text": text})
        except Exception as e:
            logger.debug(f"Speak error: {e}")
