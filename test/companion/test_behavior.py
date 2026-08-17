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
        # Default intimacy 50 -> 'familiar' pool: happy wag or nod + happy bark
        self.assertEqual(events[0]["emotion"], "happy")
        self.assertIn(events[0]["action"], ("wag_tail", "head_up_down"))
        self.assertEqual(events[0]["sound"], "happy_bark")

    def test_head_touch_coquettish_at_high_intimacy(self):
        self.state.intimacy = 90.0  # devoted level
        events = []
        self.bus.subscribe("actuator.express", lambda data: events.append(data))

        self.bus.publish("sensor.touch.head", {"type": "touch"})

        self.assertEqual(len(events), 1)
        self.assertIn(events[0]["sound"], ("enchanted",))
        self.assertIn(events[0]["action"], ("lie_with_hands_out", "wag_tail"))

    def test_head_touch_cooldown(self):
        events = []
        self.bus.subscribe("actuator.express", lambda data: events.append(data))

        self.bus.publish("sensor.touch.head", {"type": "touch"})
        self.bus.publish("sensor.touch.head", {"type": "touch"})

        # Second touch within cooldown: no second full reaction
        self.assertEqual(len(events), 1)

    def test_sulking_after_neglect(self):
        self.state.intimacy = 90.0  # devoted: sulks when neglected
        self.state.last_interaction_time = time.time() - 400.0
        events = []
        self.bus.subscribe("actuator.express", lambda data: events.append(data))

        self.engine.start()
        time.sleep(0.3)

        # Sulking expressed
        self.assertTrue(self.state.is_sulking)
        self.assertEqual(events[0]["emotion"], "sad")

        # First touch while sulking: cold shoulder (whine), no coquettish pool.
        # Must happen before stop(): stop() unsubscribes reflex handlers.
        self.bus.publish("sensor.touch.head", {"type": "touch"})
        self.engine.stop()

        self.assertEqual(events[-1]["sound"], "whine")
        self.assertIn("head", events[-1])
        self.assertFalse(self.state.is_sulking)

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

    def test_suspended_bliss_at_high_intimacy(self):
        self.state.intimacy = 90.0  # devoted: loves being held
        events = []
        self.bus.subscribe("actuator.express", lambda data: events.append(data))

        self.bus.publish("sensor.imu.suspended", {"status": "suspended"})

        self.assertEqual(self.state.mood, MoodType.EXCITED)
        self.assertEqual(events[0]["sound"], "enchanted")
        self.assertEqual(events[0]["action"], "lie_with_hands_out")

    def test_clap_commands(self):
        events = []
        approach_events = []
        self.bus.subscribe("actuator.express", lambda data: events.append(data))
        self.bus.subscribe("behavior.approach", lambda data: approach_events.append(data))

        # 1 clap -> sit
        self.bus.publish("sensor.clap.detected", {"count": 1})
        self.assertEqual(events[0]["action"], "sit")

        # 2 claps -> spin around
        self.bus.publish("sensor.clap.detected", {"count": 2})
        self.assertEqual(events[1]["action"], "turn_right")
        self.assertEqual(events[1]["step_count"], 8)

        # 3 claps with a fresh sound direction -> approach event
        self.bus.publish("sensor.sound.direction", {"angle": 120})
        self.bus.publish("sensor.clap.detected", {"count": 3})
        self.assertEqual(len(approach_events), 1)
        self.assertEqual(approach_events[0]["angle"], 120)

        # 3 claps without direction info -> confused look instead
        self.engine._last_sound_direction = None
        self.bus.publish("sensor.clap.detected", {"count": 3})
        self.assertEqual(len(approach_events), 1)
        self.assertEqual(events[-1]["sound"], "confused")

    def test_battery_low_and_critical_reactions(self):
        events = []
        self.bus.subscribe("actuator.express", lambda data: events.append(data))

        # 1. Low battery: sleepy pant, no human speech
        self.bus.publish("sensor.battery.low", {"voltage": 6.9})
        self.assertLessEqual(self.state.energy, 15.0)
        self.assertEqual(self.state.mood, MoodType.SLEEPY)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["emotion"], "sleepy")
        self.assertEqual(events[0]["sound"], "pant")
        self.assertNotIn("speak_text", events[0])

        # 2. Critical battery: sad whine
        self.bus.publish("sensor.battery.critical", {"voltage": 6.4})
        self.assertEqual(self.state.energy, 0.0)
        self.assertEqual(self.state.mood, MoodType.SAD)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1]["emotion"], "sad")
        self.assertEqual(events[1]["action"], "lie")
        self.assertEqual(events[1]["sound"], "whine")

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
