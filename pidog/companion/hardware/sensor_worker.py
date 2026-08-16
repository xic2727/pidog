import threading
import time
import logging
from typing import Optional, Any
from ..core.event_bus import EventBus

logger = logging.getLogger(__name__)


class SensorWorker:
    """
    Background worker thread polling sensors on Pidog:
    - Dual touch sensor (head/body touch/strokes)
    - Ultrasonic distance
    - Sound direction module
    - IMU (accData, gyroData, pitch/roll or suspended status)

    Publishes detected sensor events to the EventBus.
    Safe against missing hardware or mock Pidog instances.
    """

    def __init__(
        self,
        dog: Any,
        bus: EventBus,
        poll_interval: float = 0.05,
        publish_distance_interval: float = 0.5,
        battery_check_interval: float = 15.0,
        low_voltage_threshold: float = 7.0,
        critical_voltage_threshold: float = 6.6,
    ):
        """
        :param dog: Instance of Pidog or a mock object.
        :param bus: EventBus instance.
        :param poll_interval: Sleep duration between sensor polling cycles (seconds).
        :param publish_distance_interval: Minimum interval between periodic ultrasonic broadcasts (seconds).
        :param battery_check_interval: Minimum interval between battery voltage checks (seconds).
        :param low_voltage_threshold: Voltage threshold for low battery warning (volts).
        :param critical_voltage_threshold: Voltage threshold for critical battery alert (volts).
        """
        self.dog = dog
        self.bus = bus
        self.poll_interval = poll_interval
        self.publish_distance_interval = publish_distance_interval
        self.battery_check_interval = battery_check_interval
        self.low_voltage_threshold = low_voltage_threshold
        self.critical_voltage_threshold = critical_voltage_threshold

        self._running = False
        self._thread: Optional[threading.Thread] = None

        self._last_touch_val = 'N'
        self._last_distance_publish = 0.0
        self._last_sound_dir = -1
        self._last_battery_check = 0.0
        self._last_battery_voltage = None
        self._low_battery_warned = False
        self._critical_battery_warned = False

    def start(self):
        """Start the sensor polling background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, name="SensorWorker", daemon=True)
        self._thread.start()
        logger.info("SensorWorker started.")

    def stop(self):
        """Stop the sensor polling background thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.info("SensorWorker stopped.")

    def _run_loop(self):
        while self._running:
            try:
                self._poll_touch()
                self._poll_ultrasonic()
                self._poll_sound_direction()
                self._poll_imu()
                self._poll_battery()
            except Exception as e:
                logger.debug(f"Error during sensor polling cycle: {e}")

            time.sleep(self.poll_interval)

    def _poll_touch(self):
        """
        Poll dual touch sensor:
        - TouchStyle / values:
          'N': None
          'L': Rear touch -> publish 'sensor.touch.body'
          'R': Front touch -> publish 'sensor.touch.head'
          'LS': Rear to front slide -> publish 'sensor.touch.stroke_forward' / 'sensor.touch.body'
          'RS': Front to rear slide -> publish 'sensor.touch.stroke_backward' / 'sensor.touch.head'
        """
        try:
            touch_obj = getattr(self.dog, "dual_touch", None)
            if touch_obj is None:
                return

            val = 'N'
            if hasattr(touch_obj, "read"):
                val = touch_obj.read()
            elif hasattr(self.dog, "touch"):
                val = getattr(self.dog, "touch", 'N')

            if val != 'N' and val != self._last_touch_val:
                if val == 'R':
                    self.bus.publish("sensor.touch.head", {"type": "touch_front", "value": val})
                elif val == 'L':
                    self.bus.publish("sensor.touch.body", {"type": "touch_rear", "value": val})
                elif val == 'LS':
                    self.bus.publish("sensor.touch.stroke_forward", {"type": "stroke_rear_to_front", "value": val})
                    self.bus.publish("sensor.touch.body", {"type": "stroke_forward", "value": val})
                elif val == 'RS':
                    self.bus.publish("sensor.touch.stroke_backward", {"type": "stroke_front_to_rear", "value": val})
                    self.bus.publish("sensor.touch.head", {"type": "stroke_backward", "value": val})
            self._last_touch_val = val
        except Exception as e:
            logger.debug(f"Touch polling error: {e}")

    def _poll_ultrasonic(self):
        """Poll ultrasonic distance sensor."""
        try:
            dist = -1.0
            if hasattr(self.dog, "read_distance"):
                dist = self.dog.read_distance()
            elif hasattr(self.dog, "distance"):
                dist_val = self.dog.distance
                dist = dist_val.value if hasattr(dist_val, "value") else float(dist_val)

            now = time.time()
            if dist >= 0:
                # If obstacle is very close (< 15cm), publish obstacle event
                if dist < 15.0:
                    self.bus.publish("sensor.ultrasonic.obstacle", {"distance": dist})

                if now - self._last_distance_publish >= self.publish_distance_interval:
                    self.bus.publish("sensor.ultrasonic.distance", {"distance": dist})
                    self._last_distance_publish = now
        except Exception as e:
            logger.debug(f"Ultrasonic polling error: {e}")

    def _poll_sound_direction(self):
        """Poll sound direction module (ears)."""
        try:
            ears = getattr(self.dog, "ears", None)
            if ears is None:
                return

            detected = False
            if hasattr(ears, "isdetected"):
                detected = ears.isdetected()

            if detected and hasattr(ears, "read"):
                direction = ears.read()
                if direction >= 0:
                    self.bus.publish("sensor.sound.direction", {"angle": direction})
                    self._last_sound_dir = direction
        except Exception as e:
            logger.debug(f"Sound direction polling error: {e}")

    def _poll_imu(self):
        """
        Poll IMU data:
        - accData [ax, ay, az]
        - gyroData [gx, gy, gz]
        - Check if dog is suspended in the air or picked up (e.g. low/abnormal gravity or high tilt)
        """
        try:
            imu = getattr(self.dog, "imu", None)
            acc = getattr(self.dog, "accData", None)
            pitch = getattr(self.dog, "pitch", None)
            roll = getattr(self.dog, "roll", None)

            if imu is None and acc is None:
                return

            if acc is not None and len(acc) >= 3:
                ax, ay, az = acc[0], acc[1], acc[2]
                # Total acceleration magnitude
                mag = (ax**2 + ay**2 + az**2) ** 0.5
                # Free-fall or lift-up / suspended detection
                # When resting on ground normally, gravity mag is ~1.0g or ~9.8m/s^2 depending on scaling
                # If mag is close to 0 (free-fall) or excessive tilt / lifted
                if hasattr(self.dog, "is_suspended") and self.dog.is_suspended:
                    self.bus.publish("sensor.imu.suspended", {"status": "suspended", "acc": acc})
                elif pitch is not None and (abs(pitch) > 60 or (roll is not None and abs(roll) > 60)):
                    self.bus.publish("sensor.imu.tilted", {"pitch": pitch, "roll": roll})
        except Exception as e:
            logger.debug(f"IMU polling error: {e}")

    def _poll_battery(self):
        """
        Poll battery voltage:
        - Checks get_battery_voltage() or battery property on dog.
        - Emits 'sensor.battery.voltage' periodically.
        - Emits 'sensor.battery.low' when voltage falls below low_voltage_threshold (e.g. 7.0V).
        - Emits 'sensor.battery.critical' when voltage falls below critical_voltage_threshold (e.g. 6.6V).
        - Resets warning flags if voltage recovers (e.g., when plugged into charger).
        """
        now = time.time()
        if now - self._last_battery_check < self.battery_check_interval:
            return

        self._last_battery_check = now

        try:
            voltage = None
            if hasattr(self.dog, "get_battery_voltage"):
                voltage = self.dog.get_battery_voltage()
            elif hasattr(self.dog, "battery_voltage"):
                val = self.dog.battery_voltage
                voltage = val() if callable(val) else val
            elif hasattr(self.dog, "battery"):
                val = self.dog.battery
                voltage = val.read() if hasattr(val, "read") else (val() if callable(val) else val)

            if voltage is None or not isinstance(voltage, (int, float)) or voltage <= 0:
                return

            voltage = round(float(voltage), 2)
            self._last_battery_voltage = voltage

            # Periodic status broadcast
            self.bus.publish("sensor.battery.voltage", {"voltage": voltage})

            # Check critical battery (< 6.6V)
            if voltage < self.critical_voltage_threshold:
                if not self._critical_battery_warned:
                    self._critical_battery_warned = True
                    self._low_battery_warned = True
                    self.bus.publish("sensor.battery.critical", {
                        "voltage": voltage,
                        "threshold": self.critical_voltage_threshold,
                    })
            # Check low battery (< 7.0V)
            elif voltage < self.low_voltage_threshold:
                if not self._low_battery_warned:
                    self._low_battery_warned = True
                    self.bus.publish("sensor.battery.low", {
                        "voltage": voltage,
                        "threshold": self.low_voltage_threshold,
                    })
            else:
                # Voltage recovered (charging / normal)
                if self._low_battery_warned or self._critical_battery_warned:
                    self._low_battery_warned = False
                    self._critical_battery_warned = False
                    self.bus.publish("sensor.battery.normal", {"voltage": voltage})

        except Exception as e:
            logger.debug(f"Battery polling error: {e}")
