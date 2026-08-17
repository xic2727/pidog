import os
import unittest
import time
from unittest.mock import MagicMock, patch
from pidog.companion.core.event_bus import EventBus
from pidog.companion.hardware.camera_helper import CameraHelper
from pidog.companion.hardware.sensor_worker import SensorWorker
from pidog.companion.hardware.emotion_expressor import (
    EmotionExpressor,
    EMOTION_RGB_MAP,
    EMOTION_ACTION_MAP,
    EMOTION_HEAD_MAP,
    EMOTION_TAIL_MAP,
)


class TestCameraHelper(unittest.TestCase):
    def test_init_and_close(self):
        cam = CameraHelper(camera_backend="auto")
        self.assertIsNotNone(cam)
        cam.close()

    def test_capture_jpeg_mock_vilib(self):
        cam = CameraHelper(camera_backend="vilib")
        with patch.dict("sys.modules", {"vilib": MagicMock()}):
            import vilib
            vilib.Vilib.img_encoded = b"\xff\xd8\xff\xe0mock_jpeg_data"
            jpeg = cam.capture_jpeg()
            self.assertEqual(jpeg, b"\xff\xd8\xff\xe0mock_jpeg_data")

    def test_capture_jpeg_fallback_none_when_unavailable(self):
        cam = CameraHelper(camera_backend="nonexistent_backend")
        jpeg = cam.capture_jpeg()
        self.assertIsNone(jpeg)

    def test_capture_jpeg_mock_cv2(self):
        cam = CameraHelper(camera_backend="cv2", device_index=0)
        mock_cv2 = MagicMock()
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (True, "mock_frame")
        mock_cv2.VideoCapture.return_value = mock_cap
        mock_cv2.imencode.return_value = (True, MagicMock(tobytes=lambda: b"cv2_jpeg_bytes"))
        mock_cv2.IMWRITE_JPEG_QUALITY = 1

        with patch.dict("sys.modules", {"cv2": mock_cv2}):
            jpeg = cam.capture_jpeg()
            self.assertEqual(jpeg, b"cv2_jpeg_bytes")
            cam.close()
            mock_cap.release.assert_called_once()


class TestSensorWorker(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.mock_dog = MagicMock()

    def test_poll_touch_head(self):
        self.mock_dog.dual_touch.read.return_value = 'R'
        worker = SensorWorker(self.mock_dog, self.bus, poll_interval=0.01)

        events = []
        self.bus.subscribe("sensor.touch.head", lambda data: events.append(data))

        worker._poll_touch()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["value"], 'R')

    def test_poll_touch_body(self):
        self.mock_dog.dual_touch.read.return_value = 'L'
        worker = SensorWorker(self.mock_dog, self.bus, poll_interval=0.01)

        events = []
        self.bus.subscribe("sensor.touch.body", lambda data: events.append(data))

        worker._poll_touch()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["value"], 'L')

    def test_poll_touch_strokes(self):
        self.mock_dog.dual_touch.read.return_value = 'LS'
        worker = SensorWorker(self.mock_dog, self.bus, poll_interval=0.01)

        forward_events = []
        body_events = []
        self.bus.subscribe("sensor.touch.stroke_forward", lambda data: forward_events.append(data))
        self.bus.subscribe("sensor.touch.body", lambda data: body_events.append(data))

        worker._poll_touch()
        self.assertEqual(len(forward_events), 1)
        self.assertEqual(len(body_events), 1)

    def test_poll_ultrasonic_and_obstacle(self):
        self.mock_dog.read_distance.return_value = 10.5
        worker = SensorWorker(self.mock_dog, self.bus, poll_interval=0.01, publish_distance_interval=0.0)

        dist_events = []
        obstacle_events = []
        self.bus.subscribe("sensor.ultrasonic.distance", lambda data: dist_events.append(data))
        self.bus.subscribe("sensor.ultrasonic.obstacle", lambda data: obstacle_events.append(data))

        worker._poll_ultrasonic()
        self.assertEqual(len(dist_events), 1)
        self.assertEqual(dist_events[0]["distance"], 10.5)
        self.assertEqual(len(obstacle_events), 1)
        self.assertEqual(obstacle_events[0]["distance"], 10.5)

    def test_poll_sound_direction(self):
        self.mock_dog.ears.isdetected.return_value = True
        self.mock_dog.ears.read.return_value = 180
        worker = SensorWorker(self.mock_dog, self.bus, poll_interval=0.01)

        sound_events = []
        self.bus.subscribe("sensor.sound.direction", lambda data: sound_events.append(data))

        worker._poll_sound_direction()
        self.assertEqual(len(sound_events), 1)
        self.assertEqual(sound_events[0]["angle"], 180)

    def test_poll_imu_suspended(self):
        self.mock_dog.imu = MagicMock()
        self.mock_dog.accData = [0.0, 0.0, 0.0]
        self.mock_dog.is_suspended = True
        worker = SensorWorker(self.mock_dog, self.bus, poll_interval=0.01)

        imu_events = []
        self.bus.subscribe("sensor.imu.suspended", lambda data: imu_events.append(data))

        worker._poll_imu()
        self.assertEqual(len(imu_events), 1)
        self.assertEqual(imu_events[0]["status"], "suspended")

    def test_poll_battery_levels_and_warnings(self):
        self.mock_dog.get_battery_voltage = MagicMock(return_value=7.4)
        worker = SensorWorker(
            self.mock_dog,
            self.bus,
            poll_interval=0.01,
            battery_check_interval=0.0,
            low_voltage_threshold=7.0,
            critical_voltage_threshold=6.6,
        )

        voltage_events = []
        low_events = []
        critical_events = []
        normal_events = []

        self.bus.subscribe("sensor.battery.voltage", lambda d: voltage_events.append(d))
        self.bus.subscribe("sensor.battery.low", lambda d: low_events.append(d))
        self.bus.subscribe("sensor.battery.critical", lambda d: critical_events.append(d))
        self.bus.subscribe("sensor.battery.normal", lambda d: normal_events.append(d))

        # 1. Normal voltage
        worker._poll_battery()
        self.assertEqual(len(voltage_events), 1)
        self.assertEqual(voltage_events[0]["voltage"], 7.4)
        self.assertEqual(len(low_events), 0)

        # 2. Low voltage trigger (< 7.0V)
        self.mock_dog.get_battery_voltage.return_value = 6.9
        worker._poll_battery()
        self.assertEqual(len(low_events), 1)
        self.assertEqual(low_events[0]["voltage"], 6.9)

        # 3. Critical voltage trigger (< 6.6V)
        self.mock_dog.get_battery_voltage.return_value = 6.4
        worker._poll_battery()
        self.assertEqual(len(critical_events), 1)
        self.assertEqual(critical_events[0]["voltage"], 6.4)

        # 4. Recovered to normal
        self.mock_dog.get_battery_voltage.return_value = 8.0
        worker._poll_battery()
        self.assertEqual(len(normal_events), 1)
        self.assertEqual(normal_events[0]["voltage"], 8.0)

    def test_worker_lifecycle(self):
        self.mock_dog.dual_touch.read.return_value = 'N'
        self.mock_dog.read_distance.return_value = 50.0
        self.mock_dog.ears.isdetected.return_value = False
        self.mock_dog.accData = [0, 0, 1]
        self.mock_dog.pitch = 0
        self.mock_dog.roll = 0

        worker = SensorWorker(self.mock_dog, self.bus, poll_interval=0.01)
        worker.start()
        self.assertTrue(worker._running)
        time.sleep(0.05)
        worker.stop()
        self.assertFalse(worker._running)


class TestEmotionExpressor(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.mock_dog = MagicMock()
        self.expressor = EmotionExpressor(self.mock_dog, self.bus)

    def tearDown(self):
        self.expressor.close()

    def test_express_happy_emotion(self):
        self.bus.publish("actuator.express", {
            "emotion": "happy",
            "action": "wag_tail",
            "sound": "happy_bark"
        })

        # Check rgb
        self.mock_dog.rgb_strip.set_mode.assert_called_with("breath", "yellow", bps=1.5)
        # Check action
        self.mock_dog.do_action.assert_called_with("wag_tail", step_count=1, speed=50)
        # Check sound: semantic tag resolved to a real built-in file, played via dog.speak
        speak_args = self.mock_dog.speak.call_args
        self.assertIsNotNone(speak_args)
        self.assertTrue(os.path.isfile(speak_args[0][0]))

    def test_express_custom_rgb_and_head_tail(self):
        self.bus.publish("actuator.express", {
            "emotion": "neutral",
            "rgb": {"style": "boom", "color": "red", "bps": 2.0},
            "head": [[10, 0, 0]],
            "tail": [[20]],
            "speed": 60
        })

        self.mock_dog.rgb_strip.set_mode.assert_called_with("boom", "red", bps=2.0)
        self.mock_dog.head_move.assert_called_with([[10, 0, 0]], immediately=False, speed=60)
        self.mock_dog.tail_move.assert_called_with([[20]], immediately=False, speed=60)

    def test_express_speak(self):
        speak_events = []
        self.bus.subscribe("actuator.speak", lambda data: speak_events.append(data))

        self.bus.publish("actuator.express", {
            "emotion": "speak",
            "speak_text": "Hello world"
        })

        self.assertEqual(len(speak_events), 1)
        self.assertEqual(speak_events[0]["text"], "Hello world")


if __name__ == '__main__':
    unittest.main()
