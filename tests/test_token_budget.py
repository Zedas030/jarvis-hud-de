"""
Weekly token budget (token_budget.py) — usage rows faked.

    .venv\\Scripts\\python.exe -m unittest tests.test_token_budget -v
"""
import isolate  # noqa: F401  (must come first)
import datetime as dt
import unittest
from unittest import mock

import commands
import events
import token_budget as tb

NOW = dt.datetime(2026, 11, 12, 15, 0)          # a Thursday; the week began Monday 09.11.


def rows(*items):
    return [{"at": at, "route": route, "total_in": n, "output": 0} for at, route, n in items]


class Budget(unittest.TestCase):
    def setUp(self):
        for k, v in (("BUDGET", 1000), ("WARN", [80, 100])):
            p = mock.patch.object(tb, k, v)
            p.start()
            self.addCleanup(p.stop)
        commands.update_state(lambda d: d.pop("token_budget", None) and False)
        self.said = []
        p = mock.patch.object(events, "publish", lambda t, text, **kw: self.said.append(text))
        p.start()
        self.addCleanup(p.stop)

    def usage(self, *items):
        p = mock.patch("learning.rows", return_value=rows(*items))
        p.start()
        self.addCleanup(p.stop)

    def test_counts_this_week_only_and_skips_local(self):
        self.usage(("2026-11-08 23:00:00", "modell:", 900),      # Sunday: last week
                   ("2026-11-10 08:00:00", "modell:", 300),
                   ("2026-11-11 09:00:00", "local:timer", 500),
                   ("2026-11-12 10:00:00", "background:reflection", 200))
        w = tb.week(NOW)
        self.assertEqual((w["tokens"], w["pct"], w["week"]), (500, 50, "2026-W46"))
        self.assertIsNone(tb.check(NOW))

    def test_warns_once_per_threshold(self):
        self.usage(("2026-11-10 08:00:00", "modell:", 850))
        self.assertIn("85 Prozent", tb.check(NOW))
        self.assertIsNone(tb.check(NOW))                         # same week, same threshold: quiet
        self.usage(("2026-11-10 08:00:00", "modell:", 1200))
        self.assertIn("aufgebraucht", tb.check(NOW))
        self.assertIsNone(tb.check(NOW))
        self.assertEqual(len(self.said), 2)

    def test_new_week_starts_over(self):
        self.usage(("2026-11-10 08:00:00", "modell:", 850))
        tb.check(NOW)
        self.usage(("2026-11-16 08:00:00", "modell:", 850))
        self.assertIsNotNone(tb.check(dt.datetime(2026, 11, 18, 12, 0)))

    def test_off(self):
        with mock.patch.object(tb, "BUDGET", 0):
            self.assertIsNone(tb.check(NOW))
            self.assertEqual(tb.spoken(NOW), "")


if __name__ == "__main__":
    unittest.main()
