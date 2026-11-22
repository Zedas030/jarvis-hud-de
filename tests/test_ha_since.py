"""ha_since: when a device's current state began, across HA restarts and short drop-outs."""
import isolate  # noqa: F401 — first: state and logs into a temp folder

import datetime as dt
import unittest

import ha_since

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 17, 16, 44, tzinfo=UTC)


def at(h, m, day=17):
    return dt.datetime(2026, 11, day, h, m, tzinfo=UTC).isoformat()


class Began(unittest.TestCase):
    def setUp(self):
        self.old = ha_since._history

    def tearDown(self):
        ha_since._history = self.old

    def rows(self, *rows):
        ha_since._history = lambda eid, start, end=None: [{"state": s, "last_changed": t} for s, t in rows]

    def test_last_real_change_counts(self):
        self.rows(("off", at(6, 0)), ("on", at(15, 52)))
        t, bound = ha_since.began("light.licht", "on", at(15, 52), NOW)
        self.assertEqual(t.isoformat(), at(15, 52))
        self.assertFalse(bound)

    def test_restart_and_dropout_are_skipped(self):
        # on since 15:52, HA restarted at 16:10 (same state again), the lamp dropped off at 16:30
        self.rows(("off", at(6, 0)), ("on", at(15, 52)), ("on", at(16, 10)), ("unavailable", at(16, 30)),
                  ("on", at(16, 31)))
        t, _ = ha_since.began("light.licht", "on", at(16, 31), NOW)
        self.assertEqual(t.isoformat(), at(15, 52))

    def test_unchanged_in_the_whole_window_is_a_lower_bound(self):
        self.rows(("on", at(16, 44, day=10)))
        _, bound = ha_since.began("light.licht", "on", None, NOW)
        self.assertTrue(bound)
        self.assertIn("mehr als", ha_since.phrase("light.licht", "on", None, NOW))

    def test_no_history_falls_back_to_last_changed(self):
        def boom(eid, start, end=None):
            raise OSError("HA down")
        ha_since._history = boom
        t, bound = ha_since.began("light.licht", "on", at(16, 0), NOW)
        self.assertEqual(t.isoformat(), at(16, 0))
        self.assertFalse(bound)

    def test_history_that_disagrees_with_the_state_falls_back(self):
        self.rows(("off", at(16, 0)))                   # the live state says on: history not caught up yet
        t, _ = ha_since.began("light.licht", "on", at(16, 40), NOW)
        self.assertEqual(t.isoformat(), at(16, 40))


class Query(unittest.TestCase):
    def test_history_asks_up_to_now(self):
        """Without end_time HA returns one day from the start: a 7-day window ended on day one."""
        import ha_why
        old, seen = ha_why._get, []
        ha_why._get = lambda path: seen.append(path) or [[]]
        try:
            ha_since._history("light.licht", NOW - dt.timedelta(days=7), NOW)
        finally:
            ha_why._get = old
        self.assertIn("end_time=" + NOW.isoformat().replace(":", "%3A").replace("+", "%2B"), seen[0])


class Phrase(unittest.TestCase):
    def setUp(self):
        self.old = ha_since._history
        ha_since._history = lambda eid, start, end=None: []

    def tearDown(self):
        ha_since._history = self.old

    def local(self, iso):
        t = dt.datetime.fromisoformat(iso).astimezone()
        return f"{t.hour}:{t.minute:02d}"

    def test_minutes_today(self):
        text = ha_since.phrase("light.licht", "on", at(15, 53), NOW)
        self.assertEqual(text, f"seit {self.local(at(15, 53))} an, also seit 51 Minuten")

    def test_hours_and_days(self):
        self.assertIn("2 Stunden und 14 Minuten", ha_since.phrase("light.licht", "off", at(14, 30), NOW))
        self.assertIn("einer Stunde", ha_since.phrase("light.licht", "on", at(15, 42), NOW))
        self.assertIn("3 Tagen", ha_since.phrase("light.licht", "on", at(16, 0, day=14), NOW))

    def test_just_now(self):
        self.assertEqual(ha_since.phrase("light.licht", "on", at(16, 44), NOW + dt.timedelta(seconds=30)),
                         "gerade eben angegangen")

    def test_stamp_for_the_tool(self):
        self.assertTrue(ha_since.stamp("light.licht", "on", at(15, 53), NOW).endswith("(51 Minuten)"))


if __name__ == "__main__":
    unittest.main()
