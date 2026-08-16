"""Companion orchestrator managing speech, vision, LLM/VLM reasoning, and hardware dispatch."""
import re
import threading
import logging
import queue
from typing import Optional, Dict, Any, List, Tuple, Union

from .event_bus import EventBus
from .context import ConversationContext
from ..config import CompanionConfig
from ..adapters.base import BaseASR, BaseTTS, BaseVLM
from ..adapters.factory import AdapterFactory
from ..hardware.camera_helper import CameraHelper

logger = logging.getLogger(__name__)


# Keywords triggering visual capture/analysis
DEFAULT_VISION_KEYWORDS = [
    "看", "看见", "看到", "这是什么", "那是什么", "找", "瞧", "观察",
    "look", "see", "what is this", "what is that", "find", "watch", "camera", "photo", "pic"
]

# Action and Emotion Tag Patterns
# Supported tag formats:
# [action:wag_tail], [action: sit], <action>bark</action>, [act:xxx]
# [emotion:happy], [emotion: sad], <emotion>excited</emotion>, [emo:xxx]
ACTION_TAG_REGEX = re.compile(
    r'(?:\[(?:action|act)\s*:\s*([a-zA-Z0-9_\-]+)\s*\])|(?:<(?:action|act)>([a-zA-Z0-9_\-]+)</(?:action|act)>)',
    re.IGNORECASE
)
EMOTION_TAG_REGEX = re.compile(
    r'(?:\[(?:emotion|emo)\s*:\s*([a-zA-Z0-9_\-]+)\s*\])|(?:<(?:emotion|emo)>([a-zA-Z0-9_\-]+)</(?:emotion|emo)>)',
    re.IGNORECASE
)


class CompanionOrchestrator:
    """
    Central coordinator for Pidog Embodied Companion.
    - Subscribes to audio/voice input and multimodal trigger events.
    - Detects visual intent in user utterances.
    - Interacts with VLM / LLM (e.g. MiniMax) with contextual history.
    - Parses [action:xxx] and [emotion:xxx] semantic tags from model responses.
    - Dispatches cleaned speech text to TTS and physical expressions to 'actuator.express'.
    """

    def __init__(
        self,
        config: Optional[CompanionConfig] = None,
        bus: Optional[EventBus] = None,
        vlm: Optional[BaseVLM] = None,
        asr: Optional[BaseASR] = None,
        tts: Optional[BaseTTS] = None,
        camera: Optional[CameraHelper] = None,
        context: Optional[ConversationContext] = None,
        vision_keywords: Optional[List[str]] = None,
    ):
        self.config = config or CompanionConfig()
        self.bus = bus or EventBus()
        self.vlm = vlm or AdapterFactory.create_vlm(self.config.vlm)
        self.asr = asr or AdapterFactory.create_asr(self.config.asr)
        self.tts = tts or AdapterFactory.create_tts(self.config.tts)
        self.camera = camera or (CameraHelper() if self.config.enable_vision else None)
        self.context = context or ConversationContext(
            max_history=10,
            system_prompt=self.config.vlm.system_prompt if hasattr(self.config, "vlm") else None
        )
        self.vision_keywords = vision_keywords or list(DEFAULT_VISION_KEYWORDS)

        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._task_queue: queue.Queue = queue.Queue()
        self._unsub_list: List[Any] = []
        self._lock = threading.RLock()

        self._subscribe_events()

    def _subscribe_events(self):
        """Subscribe to voice and interaction events on EventBus."""
        unsub_voice = self.bus.subscribe("voice.input.text", self._on_voice_text)
        unsub_audio = self.bus.subscribe("voice.input.audio", self._on_voice_audio)
        unsub_dialogue = self.bus.subscribe("dialogue.request", self._on_dialogue_request)
        unsub_speak = self.bus.subscribe("actuator.speak", self._on_actuator_speak)
        self._unsub_list.extend([unsub_voice, unsub_audio, unsub_dialogue, unsub_speak])

    def _on_actuator_speak(self, data: Any):
        """Synthesize and play direct speech requests."""
        if not data:
            return
        text = data.get("text") if isinstance(data, dict) else str(data)
        if text and self.tts and self.tts.is_available():
            try:
                audio_bytes = self.tts.synthesize(text)
                if audio_bytes:
                    self.bus.publish("tts.audio.ready", {"text": text, "audio": audio_bytes})
            except Exception as e:
                logger.error(f"TTS synthesis for speak failed: {e}")

    def start(self):
        """Start async processing worker thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._worker_thread = threading.Thread(target=self._process_loop, daemon=True)
            self._worker_thread.start()

    def stop(self):
        """Stop worker and unsubscribe from events."""
        with self._lock:
            self._running = False
            for unsub in self._unsub_list:
                try:
                    unsub()
                except Exception as e:
                    logger.debug(f"Error during unsubscribe: {e}")
            self._unsub_list.clear()

        # Signal queue
        self._task_queue.put(None)
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)
            self._worker_thread = None

    def _on_voice_text(self, data: Any):
        """Handler for 'voice.input.text' event."""
        if not data:
            return
        if isinstance(data, str):
            text = data
            image_data = None
        elif isinstance(data, dict):
            text = data.get("text", "")
            image_data = data.get("image_data")
        else:
            return

        if text.strip():
            self.enqueue_dialogue(text.strip(), image_data=image_data)

    def _on_voice_audio(self, data: Any):
        """Handler for raw 'voice.input.audio' event requiring ASR."""
        if not data:
            return
        audio_bytes = None
        if isinstance(data, bytes):
            audio_bytes = data
        elif isinstance(data, dict):
            audio_bytes = data.get("audio") or data.get("audio_data")

        if audio_bytes and self.asr and self.asr.is_available():
            try:
                text = self.asr.transcribe(audio_bytes)
                if text and text.strip():
                    logger.info(f"ASR transcription result: '{text.strip()}'")
                    self.enqueue_dialogue(text.strip())
                else:
                    logger.debug("ASR returned empty transcription.")
            except Exception as e:
                logger.error(f"ASR transcription error: {e}")

    def _on_dialogue_request(self, data: Any):
        """Handler for explicit 'dialogue.request' event."""
        if isinstance(data, dict):
            prompt = data.get("prompt") or data.get("text", "")
            image_data = data.get("image_data")
            if prompt.strip():
                self.enqueue_dialogue(prompt.strip(), image_data=image_data)
        elif isinstance(data, str) and data.strip():
            self.enqueue_dialogue(data.strip())

    def enqueue_dialogue(self, prompt: str, image_data: Optional[Union[bytes, str]] = None):
        """Enqueue dialogue task for asynchronous processing."""
        self._task_queue.put({"prompt": prompt, "image_data": image_data})

    def _process_loop(self):
        """Background worker consuming dialogue queries from queue."""
        while self._running:
            try:
                task = self._task_queue.get(timeout=0.2)
                if task is None:
                    break
                self.process_dialogue(
                    prompt=task.get("prompt", ""),
                    image_data=task.get("image_data")
                )
                self._task_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.exception(f"Unexpected error in Orchestrator process loop: {e}")

    def detect_visual_intent(self, text: str) -> bool:
        """
        Check if user utterance contains visual query intent (e.g. 'what is this', '看前面').
        """
        if not text:
            return False
        text_lower = text.lower()
        for kw in self.vision_keywords:
            if kw.lower() in text_lower:
                return True
        return False

    @staticmethod
    def extract_tags(text: str) -> Tuple[Optional[str], Optional[str], str]:
        """
        Extract [action:xxx] and [emotion:xxx] tags from model output.
        Returns: (action, emotion, clean_text)
        """
        if not text:
            return None, None, ""

        action = None
        emotion = None

        # Search action tags
        action_match = ACTION_TAG_REGEX.search(text)
        if action_match:
            action = action_match.group(1) or action_match.group(2)
            if action:
                action = action.strip()

        # Search emotion tags
        emotion_match = EMOTION_TAG_REGEX.search(text)
        if emotion_match:
            emotion = emotion_match.group(1) or emotion_match.group(2)
            if emotion:
                emotion = emotion.strip()

        # Clean text by removing all action and emotion tags
        clean_text = ACTION_TAG_REGEX.sub("", text)
        clean_text = EMOTION_TAG_REGEX.sub("", clean_text)
        clean_text = clean_text.strip()

        return action, emotion, clean_text

    def process_dialogue(
        self,
        prompt: str,
        image_data: Optional[Union[bytes, str]] = None
    ) -> Dict[str, Any]:
        """
        Synchronously process a dialogue turn:
        1. Check visual intent and optionally capture frame via camera.
        2. Call VLM / LLM with conversation history.
        3. Parse action and emotion tags.
        4. Update conversation context.
        5. Dispatch TTS and physical expression via EventBus.

        Returns result dict with raw_response, clean_text, action, emotion, etc.
        """
        if not prompt or not prompt.strip():
            return {"error": "Empty prompt", "success": False}

        prompt = prompt.strip()
        logger.info(f"Processing dialogue prompt: {prompt}")

        # 1. Image capture if visual intent detected and no explicit image provided
        final_image = image_data
        if final_image is None and self.detect_visual_intent(prompt) and self.camera:
            try:
                final_image = self.camera.capture_jpeg()
                if final_image:
                    logger.info("Captured camera frame for visual query.")
            except Exception as e:
                logger.warning(f"Failed to capture frame from camera: {e}")

        # 2. Add user prompt to context
        self.context.add_user_message(prompt)

        # 3. Call VLM / LLM
        raw_response = ""
        if self.vlm and self.vlm.is_available():
            try:
                # Provide history excluding the prompt we just added to avoid duplicate prompt
                history = self.context.get_history()[:-1]
                raw_response = self.vlm.generate(
                    prompt=prompt,
                    image_data=final_image,
                    history=history,
                    system_prompt=self.context.system_prompt
                )
            except Exception as e:
                logger.error(f"VLM generation failed: {e}")
                raw_response = "汪汪！"
        else:
            logger.warning("VLM not available, using default fallback response.")
            raw_response = "[emotion:happy][action:wag_tail] 汪汪！"

        # 4. Extract action and emotion tags
        action, emotion, clean_text = self.extract_tags(raw_response)

        # 5. Record assistant response in context
        self.context.add_assistant_message(raw_response)

        # 6. Dispatch TTS and Physical actuation
        # Publish dialogue response event
        result = {
            "prompt": prompt,
            "raw_response": raw_response,
            "clean_text": clean_text,
            "action": action,
            "emotion": emotion or "neutral",
            "has_image": final_image is not None,
            "success": True,
        }
        self.bus.publish("dialogue.response", result)

        # Dispatch physical express
        express_payload = {
            "emotion": emotion or "neutral",
            "action": action,
        }
        self.bus.publish("actuator.express", express_payload)

        # Synthesize and speak via TTS if available
        if clean_text and self.tts and self.tts.is_available():
            try:
                audio_bytes = self.tts.synthesize(clean_text)
                if audio_bytes:
                    self.bus.publish("tts.audio.ready", {
                        "text": clean_text,
                        "audio": audio_bytes,
                    })
            except Exception as e:
                logger.error(f"TTS synthesis error: {e}")

        return result
