import threading
import time
import unittest
from pidog.companion.core.event_bus import EventBus


class TestEventBus(unittest.TestCase):

    def setUp(self):
        self.bus = EventBus()

    def test_subscribe_and_publish(self):
        received = []

        def handler(data):
            received.append(data)

        self.bus.subscribe("sensor.touch", handler)
        self.bus.publish("sensor.touch", {"touch": "front"})

        self.assertEqual(received, [{"touch": "front"}])

    def test_unsubscribe(self):
        received = []

        def handler(data):
            received.append(data)

        unsub = self.bus.subscribe("sensor.touch", handler)
        self.bus.publish("sensor.touch", "event1")
        self.assertEqual(received, ["event1"])

        unsub()
        self.bus.publish("sensor.touch", "event2")
        self.assertEqual(received, ["event1"])

    def test_multiple_subscribers_same_topic(self):
        res1 = []
        res2 = []

        self.bus.subscribe("cmd", lambda d: res1.append(d))
        self.bus.subscribe("cmd", lambda d: res2.append(d))

        self.bus.publish("cmd", 42)
        self.assertEqual(res1, [42])
        self.assertEqual(res2, [42])

    def test_subscribe_all(self):
        events = []

        def all_handler(topic, data):
            events.append((topic, data))

        unsub = self.bus.subscribe_all(all_handler)
        self.bus.publish("topic.a", 1)
        self.bus.publish("topic.b", 2)

        self.assertEqual(events, [("topic.a", 1), ("topic.b", 2)])

        unsub()
        self.bus.publish("topic.c", 3)
        self.assertEqual(events, [("topic.a", 1), ("topic.b", 2)])

    def test_handler_exception_does_not_break_bus(self):
        res = []

        def bad_handler(data):
            raise RuntimeError("Boom!")

        def good_handler(data):
            res.append(data)

        self.bus.subscribe("topic", bad_handler)
        self.bus.subscribe("topic", good_handler)

        self.bus.publish("topic", "ok")
        self.assertEqual(res, ["ok"])

    def test_thread_safety(self):
        num_threads = 10
        events_per_thread = 100
        collected = []
        lock = threading.Lock()

        def handler(data):
            with lock:
                collected.append(data)

        self.bus.subscribe("thread.test", handler)

        def worker(tid):
            for i in range(events_per_thread):
                self.bus.publish("thread.test", f"{tid}-{i}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(collected), num_threads * events_per_thread)

    def test_clear(self):
        received = []
        self.bus.subscribe("topic", lambda d: received.append(d))
        self.bus.clear()
        self.bus.publish("topic", "data")
        self.assertEqual(received, [])


if __name__ == "__main__":
    unittest.main()
