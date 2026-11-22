"""
Homelab observer: when it speaks up, and when it stays quiet. No network needed.

    .venv\\Scripts\\python.exe -m unittest tests.test_homelab_watch -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import commands          # noqa: E402
import homelab_watch as hw   # noqa: E402

PI_OUT = """host: raspi
uptime: up 6 weeks
temp: 56.3 C
load: 1.42 1.26 1.26
ram: 2500 von 7800 MB belegt
disk: 29G von 117G belegt (26%)
dienst pihole-FTL: active
dienst docker: failed
container homeassistant: Up 3 days
container zigbee2mqtt: Exited (1) 2 hours ago
container grafana: Up 2 hours (unhealthy)"""


def finding(key="pi", ok=False, severity="critical"):
    return dict(key=key, ok=ok, severity=severity, down="Der Pi ist seit {min} Minuten weg.", up="Der Pi ist wieder da.")


class Watch(unittest.TestCase):
    def setUp(self):
        commands.update_state(lambda d: d.pop("homelab", None))
        for p in (hw.BASELINE, hw.HISTORY):
            if p.exists():
                p.unlink()

    def test_outage_needs_two_checks_and_recovery_once(self):
        t = 1_000_000
        self.assertEqual(hw.process([finding()], now=t, quiet=False), [])
        self.assertEqual(hw.process([finding()], now=t + 300, quiet=False), ["Der Pi ist seit 5 Minuten weg."])
        self.assertEqual(hw.process([finding()], now=t + 600, quiet=False), [])          # once per incident
        self.assertEqual(hw.process([finding(ok=True)], now=t + 900, quiet=False), ["Der Pi ist wieder da."])
        self.assertEqual(hw.process([finding(ok=True)], now=t + 1200, quiet=False), [])

    def test_blip_stays_silent(self):
        hw.process([finding()], now=1, quiet=False)
        self.assertEqual(hw.process([finding(ok=True)], now=300, quiet=False), [])     # never announced: no "back"

    def test_quiet_hours_only_critical(self):
        warn = finding("pi_container:x", severity="warn")
        hw.process([warn, finding()], now=1, quiet=True)
        out = hw.process([warn, finding()], now=301, quiet=True)
        self.assertEqual(out, ["Der Pi ist seit 5 Minuten weg."])
        out = hw.process([warn], now=8 * 3600, quiet=False)          # morning, still broken: now it is said
        self.assertEqual(len(out), 1)

    def test_daily_cap(self):
        t, said = 1_000_000, 0
        for i in range(10):                      # flapping all day
            said += len(hw.process([finding()], now=t + i * 600, quiet=False))
            said += len(hw.process([finding()], now=t + i * 600 + 300, quiet=False))
            said += len(hw.process([finding(ok=True)], now=t + i * 600 + 400, quiet=False))
        self.assertLessEqual(said, 2 * hw.MAX_PER_DAY + 1)

    def test_parse_pi(self):
        pi = hw.parse_pi(PI_OUT)
        self.assertEqual((pi["temp"], pi["disk"], pi["load"]), (56.3, 26.0, 1.26))   # 5-minute average
        self.assertIn(("docker", "failed"), pi["services"])
        self.assertEqual(len(pi["containers"]), 3)

    def test_pi_findings(self):
        import jarvis_mcp
        old = (jarvis_mcp.PI_HOST, jarvis_mcp._pi_status)
        jarvis_mcp.PI_HOST, jarvis_mcp._pi_status = "raspi", lambda: PI_OUT
        try:
            metrics = {}
            found = {f["key"]: f["ok"] for f in hw.check_pi(metrics)}
        finally:
            jarvis_mcp.PI_HOST, jarvis_mcp._pi_status = old
        self.assertTrue(found["pi"])
        self.assertFalse(found["pi_service:docker"])
        self.assertTrue(found["pi_container:homeassistant"])
        self.assertFalse(found["pi_container:zigbee2mqtt"])
        self.assertFalse(found["pi_container:grafana"])
        self.assertEqual(metrics["pi_temp"], 56.3)

    def test_limits_and_learned_outliers(self):
        limits = dict(hw.LIMITS)
        bad = [f for f in hw.value_findings({"pi_temp": 80.0}, limits) if not f["ok"]]
        self.assertEqual(len(bad), 1)
        self.assertIn("80 Grad", bad[0]["down"])
        # one learning week at ~0.8 load: 3.5 is an outlier, 1.9 is not (needs 2.5 above normal)
        hw.BASELINE.write_text(json.dumps({"pi_load": {"samples": [0.7, 0.8, 0.9] * 56, "last": 0}}), encoding="utf-8")
        self.assertTrue(all(f["ok"] for f in hw.value_findings({"pi_load": 1.9}, limits)))
        odd = [f for f in hw.value_findings({"pi_load": 3.5}, limits) if not f["ok"]]
        self.assertEqual(len(odd), 1)
        self.assertIn("sonst um 0,8", odd[0]["down"])

    def test_no_outliers_while_learning(self):
        hw.BASELINE.write_text(json.dumps({"pi_load": {"samples": [0.8] * 20, "last": 0}}), encoding="utf-8")
        self.assertTrue(all(f["ok"] for f in hw.value_findings({"pi_load": 3.0}, dict(hw.LIMITS))))

    def test_devices(self):
        hw_ping = hw._ping
        hw._ping = lambda host: host == "192.0.2.1"
        try:
            found = hw.check_devices({"devices": [{"name": "Der Router", "host": "192.0.2.1", "critical": True},
                                                  {"name": "Der Drucker", "host": "192.0.2.9"}]})
        finally:
            hw._ping = hw_ping
        self.assertEqual([(f["ok"], f["severity"]) for f in found], [(True, "critical"), (False, "warn")])

    def test_wording(self):
        self.assertEqual(hw.sir("Der Pi ist weg."), "Sir, der Pi ist weg.")
        self.assertEqual(hw.sir("Home Assistant antwortet nicht."), "Sir, Home Assistant antwortet nicht.")


if __name__ == "__main__":
    unittest.main()
