import unittest
import time
from unittest.mock import MagicMock
from pidog.companion.core.event_bus import EventBus
from pidog.companion.behavior.state import PetState, MoodType
from pidog.companion.behavior.behavior_engine import BehaviorEngine


class TestPetState(unittest.TestCase):
    def test_default_state(self):
        state = PetState()
        self.assertEqual(state.energy, 100.0)
        self.assertEqual(state.boredom, 0.0)
        self.assertEqual(state.intimacy, 50.0)
        self.assertEqual(state.mood, MoodType.NEUTRAL)

    def test_tick_decay(self):
        state = PetState(energy=100.0, boredom=0.0, intimacy=50.0)
        state.tick(delta_seconds=10.0)
        self.assertLess(state.energy, 100.0)
        self.assertGreater(state.boredom, 0.0)

    def test_tick_sleepy_transition(self):
        state = PetState(energy=20.5)
        state.tick(delta_seconds=20.0)
        self.assertLess(state.energy, 20.0)
        self.assertEqual(state.mood, MoodType.SLEEPY)

    def test_tick_boredom_neutral_transition(self):
        state = PetState(energy=80.0, boredom=69.0, mood=MoodType.HAPPY)
        state.tick(delta_seconds=20.0)
        self.assertGreater(state.boredom, 70.0)
        self.assertEqual(state.mood, MoodType.NEUTRAL)

    def test_on_interact(self):
        state = PetState(energy=50.0, boredom=60.0, intimacy=50.0, mood=MoodType.NEUTRAL)
        old_time = state.last_interaction_time - 100
        state.last_interaction_time = old_time
        state.on_interact(intimacy_bonus=5.0, mood=MoodType.HAPPY)
        self.assertEqual(state.boredom, 40.0)
        self.assertEqual(state.intimacy, 55.0)
        self.assertEqual(state.mood, MoodType.HAPPY)
        self.assertGreater(state.last_interaction_time, old_time)

    def test_clamping_limits(self):
        state = PetState(energy=5.0, boredom=95.0, intimacy=98.0)
        state.tick(delta_seconds=200.0)
        self.assertEqual(state.energy, 0.0)
        self.assertEqual(state.boredom, 100.0)

        state.on_interact(intimacy_bonus=10.0)
        self.assertEqual(state.intimacy, 100.0)


class TestBehaviorEngine(unittest.TestCase):
    def setUp(self):
        self.state = PetState()
        self.bus = EventBus()
        self.engine = BehaviorEngine(self.state, self.bus)

    def tearDown(self):
        if self.engine._running:
            self.engine.stop()

    def test_head_touch_reaction(self):
        events = []
        self.bus.subscribe("actuator.express", lambda data: events.append(data))

        self.bus.publish("sensor.touch.head", {"type": "touch"})

        self.assertGreater(self.state.intimacy, 50.0)
        self.assertEqual(self.state.mood, MoodType.HAPPY)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["emotion"], "happy")
        self.assertEqual(events[0]["action"], "wag_tail")

    def test_body_touch_reaction(self):
        events = []
        self.bus.subscribe("actuator.express", lambda data: events.append(data))

        self.bus.publish("sensor.touch.body", {"type": "slide"})

        self.assertGreater(self.state.intimacy, 50.0)
        self.assertEqual(self.state.mood, MoodType.HAPPY)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["emotion"], "happy")
        self.assertEqual(events[0]["action"], "stretch")

    def test_suspended_imu_reaction(self):
        events = []
        self.bus.subscribe("actuator.express", lambda data: events.append(data))

        self.bus.publish("sensor.imu.suspended", {"status": "suspended"})

        self.assertEqual(self.state.mood, MoodType.SCARED)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["emotion"], "scared")
        self.assertEqual(events[0]["action"], "lie")

    def test_battery_low_and_critical_reactions(self):
        events = []
        self.bus.subscribe("actuator.express", lambda data: events.append(data))

        # 1. Low battery
        self.bus.publish("sensor.battery.low", {"voltage": 6.9})
        self.assertLessEqual(self.state.energy, 15.0)
        self.assertEqual(self.state.mood, MoodType.SLEEPY)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["emotion"], "sleepy")
        self.assertIn("主人", events[0]["speak_text"])

        # 2. Critical battery
        self.bus.publish("sensor.battery.critical", {"voltage": 6.4})
        self.assertEqual(self.state.energy, 0.0)
        self.assertEqual(self.state.mood, MoodType.SAD)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1]["emotion"], "sad")
        self.assertEqual(events[1]["action"], "lie")

    def test_autonomous_heartbeat_tick(self):
        self.state.boredom = 65.0
        self.state.last_interaction_time = time.time() - 20.0
        events = []
        self.bus.subscribe("actuator.express", lambda data: events.append(data))

        self.engine.start()
        time.sleep(0.3)
        self.engine.stop()

        self.assertTrue(len(events) >= 1)
        self.assertEqual(events[0]["emotion"], "neutral")
        self.assertEqual(events[0]["action"], "stretch")


if __name__ == '__main__':
    unittest.main()
