"""
Regression tests written before the proactive mode: they pin how notices behave today, so the new
module can be switched off and Jarvis is exactly the old one. No network, no house.

    .venv\\Scripts\\python.exe -m unittest tests.test_regression_notices -v
"""
import datetime as dt
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import commands          # noqa: E402
import emergency         # noqa: E402
import events            # noqa: E402
import focus             # noqa: E402
import voice_config      # noqa: E402
import watchers          # noqa: E402


def reset_bus():
    with events._COND:
        events._LOG.clear()
        events._SPOKEN.clear()
    events.NOTICES.unlink(missing_ok=True)
    commands.update_state(lambda d: [d.pop(k, None) for k in ("paused_until", "focus", "notified")])


class QuietHours(unittest.TestCase):
    def setUp(self):
        reset_bus()

    def test_wraps_over_midnight(self):
        self.assertTrue(events.quiet_now(dt.datetime(2026, 11, 5, 23, 0)))
        self.assertTrue(events.quiet_now(dt.datetime(2026, 11, 5, 6, 59)))
        self.assertFalse(events.quiet_now(dt.datetime(2026, 11, 5, 7, 0)))
        self.assertFalse(events.quiet_now(dt.datetime(2026, 11, 5, 21, 59)))

    def test_say_shown_alarm_rings_critical_shown(self):
        with mock.patch.object(events, "quiet_now", return_value=True):
            say = events.publish("say", "Die Waschmaschine ist fertig.")
            alarm = events.publish("alarm", "Timer abgelaufen.", priority="alarm")
            crit = events.publish("say", "Unwetterwarnung für Berlin.", priority="critical")
        self.assertTrue(say["silent"])
        self.assertNotIn("silent", alarm)
        self.assertTrue(crit["silent"])           # today only "alarm" speaks in the quiet hours

    def test_day_speaks(self):
        with mock.patch.object(events, "quiet_now", return_value=False):
            self.assertNotIn("silent", events.publish("say", "Ein Paket wurde zugestellt."))


class DedupeAndThrottle(unittest.TestCase):
    def setUp(self):
        reset_bus()
        self.q = mock.patch.object(events, "quiet_now", return_value=False)
        self.q.start()

    def tearDown(self):
        self.q.stop()

    def test_same_text_or_key_dropped(self):
        self.assertIsNotNone(events.publish("say", "Seit 5 Minuten offline: Router."))
        self.assertIsNone(events.publish("say", "Seit 10 Minuten offline: Router."))     # digits ignored
        self.assertIsNotNone(events.publish("say", "Erste Fassung", key="k1"))
        self.assertIsNone(events.publish("say", "Ganz anderer Text", key="k1"))

    def test_hourly_limit_then_silent_but_critical_speaks(self):
        for i in range(events.SAY_PER_HOUR):
            self.assertNotIn("silent", events.publish("say", f"Meldung {'x' * (i + 1)}"))
        over = events.publish("say", "Noch eine Meldung")
        self.assertTrue(over["silent"] and over["throttled"])
        self.assertNotIn("silent", events.publish("say", "Rauch im Flur", priority="critical"))

    def test_notice_log_keeps_at_kind_text(self):
        events.publish("say", "Die Spülmaschine ist fertig.", kind="appliance")
        rows = [json.loads(ln) for ln in events.NOTICES.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(set(rows[-1]), {"at", "kind", "text"})
        self.assertEqual(rows[-1]["kind"], "appliance")
        self.assertEqual(events.notices_since(rows[-1]["at"])[0]["text"], "Die Spülmaschine ist fertig.")


class Holds(unittest.TestCase):
    def setUp(self):
        reset_bus()

    def test_emergency_pause_drops_say_but_not_alarm(self):
        commands.update_state(lambda d: d.update(paused_until=9e12))
        try:
            self.assertIsNone(events.publish("say", "Etwas Neues."))
            self.assertIsNotNone(events.publish("alarm", "Wecker.", priority="alarm"))
        finally:
            commands.update_state(lambda d: d.pop("paused_until", None))

    def test_focus_holds_normal_not_critical(self):
        with mock.patch.object(events, "quiet_now", return_value=False), \
                mock.patch.object(focus, "active", return_value=True):
            held = events.publish("say", "Post ist da.")
            crit = events.publish("say", "Wasser im Bad.", priority="critical")
        self.assertTrue(held["silent"] and held["held"])
        self.assertNotIn("silent", crit)


class OncePerDay(unittest.TestCase):
    def setUp(self):
        reset_bus()

    def test_once_per_key_and_day(self):
        with mock.patch.object(events, "publish") as pub:
            self.assertTrue(watchers.once("storage", "Der Speicher ist voll."))
            self.assertFalse(watchers.once("storage", "Der Speicher ist voll."))
        pub.assert_called_once()


class Commands(unittest.TestCase):
    def test_emergency_words_unchanged(self):
        self.assertTrue(emergency.STOP.match("not-aus"))
        self.assertTrue(emergency.RESUME.match("pause aufheben"))
        self.assertFalse(emergency.RESUME.match("ruhe für eine stunde"))

    def test_barge_in_stop_words_are_whole_utterances(self):
        words = voice_config.make().barge.stop_words
        self.assertIn("ruhe", words)
        self.assertNotIn("ruhe für eine stunde", words)


if __name__ == "__main__":
    unittest.main()
