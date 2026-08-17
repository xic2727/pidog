"""Approach behavior: turn toward a sound source and walk up to it.

Triggered by 'behavior.approach' events carrying the sound direction angle
(0~355 degrees, clockwise, 0 = straight ahead). Coordinates:

    0           -> front
    1 ~ 160     -> right side (turn_right)
    ~180        -> behind
    200 ~ 355   -> left side (turn_left)

Runs its motion sequence on a dedicated thread so it never blocks the
EventBus, and aborts when an ultrasonic obstacle event arrives mid-walk.
"""
import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def normalize_angle(angle: float) -> float:
    """Convert a 0..355 clockwise compass angle to a signed [-180, 180) angle.

    Positive means right of the dog, negative means left.
    """
    angle = float(angle) % 360.0
    if angle > 180.0:
        angle -= 360.0
    return angle


class ApproachBehavior:
    """Turn toward a sound direction and walk a few steps forward."""

    def __init__(
        self,
        dog: Any,
        bus: Any,
        turn_degrees_per_step: float = 22.5,
        turn_deadzone_deg: float = 20.0,
        max_turn_steps: int = 10,
        forward_steps: int = 4,
        speed: int = 60,
    ):
        """
        :param turn_degrees_per_step: yaw degrees rotated per 'turn_left/right'
            action step (calibrate for your robot; walk gait dependent)
        :param turn_deadzone_deg: angles below this are treated as "already facing"
        :param max_turn_steps: upper bound of turn steps per approach
        :param forward_steps: how many forward walk steps after turning
        """
        self.dog = dog
        self.bus = bus
        self.turn_degrees_per_step = turn_degrees_per_step
        self.turn_deadzone_deg = turn_deadzone_deg
        self.max_turn_steps = max_turn_steps
        self.forward_steps = forward_steps
        self.speed = speed

        self._running = False
        self._abort = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._unsub_approach = bus.subscribe("behavior.approach", self._on_approach)
        self._unsub_obstacle = bus.subscribe("sensor.ultrasonic.obstacle", self._on_obstacle)

    # ------------------------------------------------------------------ #

    def _on_approach(self, data: Any):
        """EventBus handler: 'behavior.approach' {angle: 0~355, ...}"""
        if isinstance(data, dict):
            angle = data.get("angle")
        else:
            angle = data
        if angle is None:
            return
        try:
            angle = float(angle)
        except (TypeError, ValueError):
            return

        # One approach at a time; a new request replaces the previous one
        self._abort.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._abort.clear()
        self._thread = threading.Thread(
            target=self._approach_sequence, args=(angle,), name="ApproachBehavior", daemon=True
        )
        self._thread.start()

    def _on_obstacle(self, data: Any):
        """Abort an in-flight approach when an obstacle appears."""
        if self._running:
            logger.info(f"Approach aborted by obstacle: {data}")
            self._abort.set()

    # ------------------------------------------------------------------ #

    def _wait_legs(self, timeout: float = 10.0):
        """Block until leg queue drains or abort fires."""
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._abort.is_set():
                return False
            buf = getattr(self.dog, "legs_action_buffer", None)
            if buf is not None:
                try:
                    if len(buf) <= 0:
                        return True
                except Exception:
                    return True
            else:
                return True
            time.sleep(0.05)
        return not self._abort.is_set()

    def _approach_sequence(self, angle: float):
        self._running = True
        try:
            signed = normalize_angle(angle)
            logger.info(f"Approach: sound at {angle:.0f} deg (signed {signed:.0f}), moving toward it.")

            # 1. Turn toward the sound source
            if abs(signed) >= self.turn_deadzone_deg:
                action = "turn_right" if signed > 0 else "turn_left"
                steps = min(self.max_turn_steps, max(1, round(abs(signed) / self.turn_degrees_per_step)))
                self._do_action(action, step_count=steps)
                if not self._wait_legs(timeout=steps * 1.5 + 3.0):
                    logger.info("Approach: turn interrupted, stopping.")
                    self._stop_body()
                    return

            if self._abort.is_set():
                self._stop_body()
                return

            # 2. Walk a few steps toward the owner
            self._do_action("forward", step_count=self.forward_steps)
            self._wait_legs(timeout=self.forward_steps * 1.5 + 3.0)

            # 3. Arrived: happy greeting
            self.bus.publish("actuator.express", {
                "emotion": "excited",
                "action": "wag_tail",
                "sound": "happy_bark",
            })
        except Exception as e:
            logger.debug(f"Approach sequence error: {e}")
        finally:
            self._running = False

    def _do_action(self, action: str, step_count: int = 1):
        try:
            if hasattr(self.dog, "do_action"):
                self.dog.do_action(action, step_count=step_count, speed=self.speed)
        except Exception as e:
            logger.debug(f"Do action '{action}' error: {e}")

    def _stop_body(self):
        try:
            if hasattr(self.dog, "body_stop"):
                self.dog.body_stop()
        except Exception as e:
            logger.debug(f"body_stop error: {e}")

    def close(self):
        """Abort in-flight motion and unsubscribe."""
        self._abort.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._unsub_approach()
        self._unsub_obstacle()
