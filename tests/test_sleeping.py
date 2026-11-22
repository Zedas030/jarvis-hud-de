"""
Soft voice after "Gute Nacht", not by the clock (events.sleeping, 17.11.2026).

    .venv\\Scripts\\python.exe -m unittest tests.test_sleeping -v
"""
import datetime as dt
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import daytime   # noqa: E402
import events    # noqa: E402

NIGHT = dt.datetime(2026, 11, 17, 0, 30)


class Sleeping(unittest.TestCase):
    def setUp(self):
        events._SLEEP.update(at=0.0, state=None)
        self.p = [mock.patch.object(events, "SLEEP_ENTITY", "input_boolean.jarvis_gute_nacht"),
                  mock.patch.object(events, "quiet_now", return_value=True)]     # it is night by the clock
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in reversed(self.p):
            p.stop()
        events._SLEEP.update(at=0.0, state=None)

    def test_awake_at_night_is_not_quiet(self):
        with mock.patch.object(events, "_sleep_state", return_value="off"):
            self.assertFalse(events.sleeping())

    def test_after_good_night_quiet(self):
        with mock.patch.object(events, "_sleep_state", return_value="on"):
            self.assertTrue(events.sleeping())

    def test_unknown_falls_back_to_the_clock(self):
        with mock.patch.object(events, "_sleep_state", return_value=None):
            self.assertTrue(events.sleeping())

    def test_without_entity_the_clock(self):
        with mock.patch.object(events, "SLEEP_ENTITY", ""), \
                mock.patch.object(events, "_sleep_state", side_effect=AssertionError("no HA call")):
            self.assertTrue(events.sleeping())

    def test_whisper_mode_follows_good_night(self):
        with mock.patch.object(daytime, "_WHISPER_SET", False), mock.patch.object(daytime, "override", return_value=None), \
                mock.patch.object(daytime, "ENABLED", True), mock.patch.object(events, "_sleep_state", return_value="off"), \
                mock.patch.object(daytime.dt, "datetime", mock.Mock(now=mock.Mock(return_value=NIGHT))):
            self.assertNotEqual(daytime.mode(), "nacht")      # 0:30, awake: no whispering
        with mock.patch.object(daytime, "_WHISPER_SET", False), mock.patch.object(daytime, "override", return_value=None), \
                mock.patch.object(daytime, "ENABLED", True), mock.patch.object(events, "_sleep_state", return_value="on"):
            self.assertEqual(daytime.mode(), "nacht")


if __name__ == "__main__":
    unittest.main()
