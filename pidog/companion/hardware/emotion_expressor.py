import logging
import os
import threading
from typing import Optional, Any, Dict
from ..core.event_bus import EventBus
from ..behavior.sound_library import SoundLibrary
from .audio_worker import AudioPlayer

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


# Action alias mapping to Pidog native actions and head gestures
ACTION_ALIAS_MAP = {
    "tilt_head": ("tilting_head", None),
    "tilting_head": ("tilting_head", None),
    "tilting_head_left": ("tilting_head_left", None),
    "tilting_head_right": ("tilting_head_right", None),
    "head_nod": ("head_up_down", None),
    "nod": ("head_up_down", None),
    "shake_head": ("shake_head", None),
    "nod_lethargy": ("nod_lethargy", None),
    "bark": ("head_bark", "happy_bark"),
    "howling": ("head_bark", "howling"),
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

    def __init__(self, dog: Any, bus: EventBus, sound_library: Optional[SoundLibrary] = None):
        """
        :param dog: Instance of Pidog or mock Pidog.
        :param bus: EventBus instance.
        :param sound_library: SoundLibrary resolving semantic sound tags
            (e.g. 'coquettish') to built-in audio files. A default shared
            library is created when omitted.
        """
        self.dog = dog
        self.bus = bus
        self.sound_library = sound_library or SoundLibrary()
        self._unsub = None
        self._subscribe()

    def _subscribe(self):
        self._unsub_express = self.bus.subscribe("actuator.express", self.express)
        self._unsub_tts = self.bus.subscribe("tts.audio.ready", self._on_tts_audio)

    def close(self):
        """Unsubscribe and cleanup."""
        if hasattr(self, "_unsub_express") and self._unsub_express:
            self._unsub_express()
            self._unsub_express = None
        if hasattr(self, "_unsub_tts") and self._unsub_tts:
            self._unsub_tts()
            self._unsub_tts = None

    def _on_tts_audio(self, data: Dict[str, Any]):
        """Play synthesized TTS audio from Xiaomi MiMo TTS."""
        if not isinstance(data, dict):
            return
        audio_bytes = data.get("audio")
        if audio_bytes:
            AudioPlayer.play_wav_bytes(audio_bytes)

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
        step_count = command.get("step_count", 1)

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
            self._do_action(action, speed=speed, step_count=step_count)

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

    def _do_action(self, action_name: str, speed: int = 50, step_count: int = 1):
        """Perform preset dog action with alias and fallback support."""
        try:
            if not hasattr(self.dog, "do_action"):
                return

            # Resolve aliases (e.g. tilt_head -> tilting_head)
            target_action = action_name
            sound_effect = None
            if action_name in ACTION_ALIAS_MAP:
                target_action, sound_effect = ACTION_ALIAS_MAP[action_name]

            # Play associated sound if any
            if sound_effect:
                self._play_sound(sound_effect)

            self.dog.do_action(target_action, step_count=step_count, speed=speed)
        except Exception as e:
            logger.debug(f"Do action error for '{action_name}': {e}")

    def _play_sound(self, sound_name: str):
        """Play a sound effect by semantic tag or file path.

        Semantic tags (e.g. 'coquettish', 'happy_bark') are resolved through
        the SoundLibrary with variant selection and cooldown; direct file
        names/paths still work as fallback.
        """
        try:
            path = None
            # 1. Semantic tag via SoundLibrary (respects cooldown)
            if self.sound_library and self.sound_library.is_known_tag(sound_name):
                path = self.sound_library.resolve(sound_name)
                if path is None:  # cooling down, skip silently
                    return
            # 2. Direct file path / name fallback
            elif os.path.isfile(sound_name):
                path = sound_name

            if not path:
                logger.debug(f"Unknown sound '{sound_name}', skipping.")
                return

            # Prefer Pidog.speak (handles absolute paths + threaded playback)
            if hasattr(self.dog, "speak"):
                self.dog.speak(path)
                return
            if hasattr(self.dog, "music") and self.dog.music:
                if hasattr(self.dog.music, "sound_play_threading"):
                    self.dog.music.sound_play_threading(path)
                    return
                if hasattr(self.dog.music, "sound_play"):
                    self.dog.music.sound_play(path)
                    return
            # Dev machine fallback
            AudioPlayer.play_file(path)
        except Exception as e:
            logger.debug(f"Play sound error: {e}")

    def _speak(self, text: str):
        """Publish or trigger TTS speak if requested."""
        try:
            # Publish event for TTS adapter and AudioPlayer to synthesize and output
            self.bus.publish("actuator.speak", {"text": text})
        except Exception as e:
            logger.debug(f"Speak error: {e}")
