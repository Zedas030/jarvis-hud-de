"""
Home Assistant in real time (ha_events.py) — events faked; no HA, no network.

    .venv\\Scripts\\python.exe -m unittest tests.test_ha_events -v
"""
import isolate  # noqa: F401  (must come first)
import time
import unittest
from unittest import mock

import briefing
import ha_events


def st(eid, state):
    return {"entity_id": eid, "state": state, "attributes": {}}


class HaEvents(unittest.TestCase):
    def setUp(self):
        ha_events._CACHE.clear()
        ha_events._ST.update(live=False, last_msg=0.0, events=0)
        ha_events.WAKE.clear()

    def go_live(self, *items):
        ha_events._CACHE.update({s["entity_id"]: s for s in items})
        ha_events._ST.update(live=True, last_msg=time.time())

    def test_down_means_none(self):
        self.assertIsNone(ha_events.states())
        self.go_live(st("light.flur", "on"))
        self.assertEqual(ha_events.states(), [st("light.flur", "on")])
        ha_events._ST["last_msg"] = time.time() - ha_events.STALE_S - 1     # silent connection
        self.assertIsNone(ha_events.states())

    def test_event_updates_and_removes(self):
        self.go_live(st("sensor.temp", "20"))
        ha_events.apply_event({"entity_id": "sensor.temp", "old_state": st("sensor.temp", "20"),
                               "new_state": st("sensor.temp", "21")})
        self.assertEqual(ha_events.states()[0]["state"], "21")
        ha_events.apply_event({"entity_id": "sensor.temp", "old_state": st("sensor.temp", "21"), "new_state": None})
        self.assertIsNone(ha_events.states())         # empty cache counts as not trustworthy

    def test_what_wakes(self):
        door = "binary_sensor.wohnungstur"
        self.assertTrue(ha_events.worth_waking(door, st(door, "off"), st(door, "on")))
        self.assertFalse(ha_events.worth_waking(door, st(door, "on"), st(door, "on")))      # attributes only
        self.assertTrue(ha_events.worth_waking("sensor.x1c_druckstatus", st("a", "running"), st("a", "finish")))
        self.assertFalse(ha_events.worth_waking("sensor.pc_derzeitiger_verbrauch", st("a", "300"), st("a", "310")))

    def test_briefing_prefers_live_copy(self):
        # a fresh copy of briefing.py: test_router replaces briefing._ha_states for good when it loads
        import importlib.util
        spec = importlib.util.spec_from_file_location("briefing_fresh", briefing.__file__)
        fresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fresh)
        self.go_live(st("light.flur", "off"))
        with mock.patch.object(fresh, "HA_TOKEN", "t"), mock.patch.object(fresh, "_get") as rest:
            self.assertEqual(fresh._ha_states(), [st("light.flur", "off")])
            rest.assert_not_called()
            ha_events._ST["live"] = False
            rest.return_value = [st("light.flur", "on")]
            self.assertEqual(fresh._ha_states(), [st("light.flur", "on")])


if __name__ == "__main__":
    unittest.main()
