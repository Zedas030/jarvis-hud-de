"""
Predictive monitoring: storage forecasts with data basis, recurring faults,
duplicate notices merged, hourly limit for spoken notices.

    .venv\\Scripts\\python.exe -m unittest tests.test_monitoring -v
"""
import datetime as dt
import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import commands             # noqa: E402
import events               # noqa: E402
import homelab_watch as hw  # noqa: E402
import monitoring as mon    # noqa: E402

TODAY = dt.date(2026, 10, 23)


def series(start, per_day, days=21, noise=0.0, seed=1):
    rnd = random.Random(seed)
    return {(TODAY - dt.timedelta(days=days - 1 - i)).isoformat(): round(start + per_day * i + rnd.uniform(-noise, noise), 2)
            for i in range(days)}


class Forecast(unittest.TestCase):
    def test_steady_growth(self):
        f = mon.forecast("nas_used", TODAY, {"nas_used": series(80, 0.25)})
        self.assertAlmostEqual(f["days_left"], (100 - 85) / 0.25, delta=1)
        self.assertEqual(f["confidence"], "hoch")
        self.assertEqual((f["n"], f["span"]), (21, 20))

    def test_warning_with_basis(self):
        found = mon.forecast_findings(TODAY, {"pc_disk": series(85, 0.4)})
        bad = [x for x in found if not x["ok"]]
        self.assertEqual(len(bad), 1)
        self.assertIn("in etwa", bad[0]["down"])
        self.assertIn("Sicherheit hoch, 21 Messwerte über 20 Tage", bad[0]["down"])

    def test_no_warning_far_away_flat_or_noisy(self):
        self.assertTrue(all(x["ok"] for x in mon.forecast_findings(TODAY, {"nas_used": series(40, 0.05)})))
        self.assertIsNone(mon.forecast("nas_used", TODAY, {"nas_used": series(60, 0.0)})["days_left"])
        noisy = mon.forecast("pi_disk", TODAY, {"pi_disk": series(70, 0.3, noise=8)})
        self.assertNotEqual(noisy["confidence"], "hoch")

    def test_too_little_data(self):
        self.assertIsNone(mon.forecast("nas_used", TODAY, {"nas_used": series(80, 1, days=5)}))
        self.assertEqual(mon.forecast_findings(TODAY, {}), [])

    def test_record_daily(self):
        if mon.PATH.exists():
            mon.PATH.unlink()
        mon.record({"pc_disk": 41.0, "pi_temp": 50}, TODAY)
        mon.record({"pc_disk": 41.5}, TODAY)
        mon.record({"pc_disk": 42.0}, TODAY + dt.timedelta(days=1))
        self.assertEqual(mon.load(), {"pc_disk": {"2026-10-23": 41.5, "2026-10-24": 42.0}})


class Recurring(unittest.TestCase):
    def setUp(self):
        commands.update_state(lambda d: d.pop("homelab", None))
        if hw.HISTORY.exists():
            hw.HISTORY.unlink()

    def test_third_outage_in_a_week_is_named(self):
        f = dict(key="device:Drucker", ok=False, severity="warn", down="Der Drucker ist seit {min} Minuten weg.",
                 up="Der Drucker ist wieder da.")
        now = time.time() - 3 * 86400
        out = []
        for i in range(3):
            t = now + i * 86400
            hw.process([f], now=t, quiet=False)
            out += hw.process([f], now=t + 300, quiet=False)
            hw.process([dict(f, ok=True)], now=t + 600, quiet=False)
        self.assertNotIn("wiederkehrend", out[0])
        self.assertIn("Das ist der 3. Ausfall in 7 Tagen — wiederkehrend.", out[2])

    def test_value_peak_is_no_outage(self):
        f = dict(key="value:pi_load", ok=False, severity="warn", down="Die Last auf dem Pi liegt bei 6,3.",
                 up="Die Last ist wieder normal.")
        now = time.time() - 3 * 86400
        out = []
        for i in range(3):
            t = now + i * 86400
            hw.process([f], now=t, quiet=False)
            out += hw.process([f], now=t + 300, quiet=False)
            hw.process([dict(f, ok=True)], now=t + 600, quiet=False)
        self.assertNotIn("wiederkehrend", out[2])
        self.assertIn("Das ist das 3. Mal in 7 Tagen, aber kein Ausfall.", out[2])

    def test_pi_load_uses_five_minute_average(self):
        self.assertEqual(hw.parse_pi("load: 6.30 2.10 1.20")["load"], 2.1)
        self.assertEqual(hw.parse_pi("load: 0.5")["load"], 0.5)


class Notices(unittest.TestCase):
    def setUp(self):
        with events._COND:
            events._LOG.clear()
        events._SPOKEN.clear()
        self._quiet = events.quiet_now
        events.quiet_now = lambda now=None: False

    def tearDown(self):
        events.quiet_now = self._quiet

    def test_duplicates_merged(self):
        self.assertIsNotNone(events.publish("say", "Sir, das NAS ist seit 5 Minuten nicht erreichbar."))
        self.assertIsNone(events.publish("say", "Sir, das NAS ist seit 10 Minuten nicht erreichbar."))
        self.assertIsNotNone(events.publish("say", "Sir, die Waschmaschine ist fertig."))
        self.assertIsNotNone(events.publish("say", "anders formuliert", key="k1"))
        self.assertIsNone(events.publish("say", "nochmal anders", key="k1"))

    def test_hourly_limit_but_critical_and_alarms_pass(self):
        spoken = [events.publish("say", f"Meldung {chr(65 + i)}") for i in range(events.SAY_PER_HOUR + 2)]
        self.assertTrue(all(not e.get("silent") for e in spoken[:events.SAY_PER_HOUR]))
        self.assertTrue(all(e.get("throttled") for e in spoken[events.SAY_PER_HOUR:]))
        self.assertFalse(events.publish("say", "Der Pi ist weg.", priority="critical").get("silent"))
        self.assertFalse(events.publish("alarm", "Wecker", priority="alarm").get("silent"))


if __name__ == "__main__":
    unittest.main()
