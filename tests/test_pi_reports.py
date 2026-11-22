"""
Pi reports (Restore Probe, Update-Schleuse, Stromfresser-Detektiv, Zigbee-Platzberater, Akku-Doktor,
Automations-Prüfer, Homelab-Drift) — fetch is faked; no network.

    .venv\\Scripts\\python.exe -m unittest tests.test_pi_reports -v
"""
import isolate  # noqa: F401  (must come first)
import time
import unittest

import pi_reports
import policy
import router

RESTORE = {"finished": 0, "status": "ok", "error": "",
           "services": [{"name": "homeassistant", "title": "Home Assistant", "status": "ok"},
                        {"name": "grafana", "title": "Grafana", "status": "ok"}]}
AIRLOCK = {"overall": "attention",
           "global": {"last_postponed": {"reason": "postponed: at home and active (binary_sensor.x was on)"}},
           "containers": {"homeassistant": {"status": "held", "available_version": "2026.11.0",
                                            "held_reason": "breaking_change"},
                          "uptime-kuma": {"status": "ready", "available_version": "2@e884"},
                          "fritz_exporter": {"status": "candidate"},
                          "noah-mqtt": {"status": "up_to_date"}}}
STROM = {"status": "auffällig", "lernfortschritt": {"tv": "14/14", "ipad_station": "11/14"},
         "offen": [{"id": 1, "geraet": "tv", "meldung": "TV: Standby-Verbrauch zu hoch.", "zustand": "offen"},
                   {"id": 2, "geraet": "pc", "meldung": "PC: alt.", "zustand": "erledigt"}]}
ZIGBEE = {"recommendation": {"status": "insufficient_data", "candidates": [], "weak": []},
          "text": ["Noch zu wenig Daten – keine Empfehlung.", "Nur 4 Zigbee-Geräte."],
          "basis": "Datengrundlage: 0,1 Tage.",
          "channel": {"tone": "ok", "headline": "Keine Überlappung."}, "health": {"ok": True}}
AKKU = {"ha_connected": True, "alerts": [],
        "storages": {"system": {"capacity": {"status": "ok", "soh": 92.7, "cap_kwh": 3.7976, "lo": 3.677, "hi": 3.918},
                                "warranty": {"status": "ok", "end": "2036-04-17", "notes": []}}},
        "cycles": {"system": 74.09}}
PRUEFER = {"status": "ok", "trusted": True, "detail": "", "last_check": 0,
           "findings": [{"severity": "info", "title": "wait_template wartet 30 min"}]}
LOOP = {"severity": "critical", "title": "Echte Schleife: Jarvis: Nachtruhe Schlafzimmer"}
CONFLICT = {"severity": "warning", "title": "Konflikt bei Hue Schlafzimmer"}
DRIFT_TEXT = """# HELP homelab_drift_scan_ok last scan ok
homelab_drift_last_success_timestamp_seconds {now}
homelab_drift_scan_ok 1
homelab_drift_host_reachable{{host="fritzbox"}} 1
homelab_drift_host_reachable{{host="pi"}} 1
homelab_drift_findings{{severity="instant",status="open"}} 0
homelab_drift_findings{{severity="instant",status="acknowledged"}} 10
homelab_drift_findings{{severity="digest",status="open"}} 7
"""


class PiReports(unittest.TestCase):
    def setUp(self):
        self._fetch = pi_reports.fetch
        self.data = {"backups": dict(RESTORE, finished=time.time() - 3600), "updates": AIRLOCK,
                     "strom": STROM, "zigbee": ZIGBEE, "akku": AKKU,
                     "automationen": dict(PRUEFER, last_check=time.time() - 60),
                     "drift": pi_reports._prom(DRIFT_TEXT.format(now=time.time() - 600))}
        pi_reports.fetch = lambda topic: self.data[topic]
        router.forget_topic()

    def tearDown(self):
        pi_reports.fetch = self._fetch

    def test_tool_only_reads(self):
        self.assertEqual(policy.action_class("pi_reports"), policy.READ)

    def test_backups(self):
        self.assertEqual(pi_reports.report("backups")[0], "ok")
        self.data["backups"] = dict(self.data["backups"], services=[
            {"title": "Home Assistant", "status": "fail"}, {"title": "Grafana", "status": "ok"}])
        sev, text = pi_reports.report("backups")
        self.assertEqual(sev, "attention")
        self.assertIn("Home Assistant nicht sauber wiederherstellbar", text)
        self.assertIn("1 von 2", text)
        self.data["backups"] = dict(RESTORE, finished=time.time() - 3 * 86400)
        self.assertIn("3 Tage alt", pi_reports.report("backups")[1])

    def test_updates(self):
        sev, text = pi_reports.report("updates")
        self.assertEqual(sev, "attention")
        self.assertIn("Home Assistant 2026.11.0 wartet auf dich, weil es Breaking Changes gibt.", text)
        self.assertIn("2 Container-Updates stehen an", text)
        self.assertIn("weil du zu Hause warst", text)
        # routine updates are left out of the briefing, the held one stays
        self.assertNotIn("Container-Updates", pi_reports.report("updates", brief=True)[1])
        self.data["updates"] = {"overall": "ok", "containers": {"a": {"status": "up_to_date"}}}
        self.assertEqual(pi_reports.report("updates"), ("ok", "Alle Container sind aktuell."))

    def test_strom_reads_only_open_findings(self):
        sev, text = pi_reports.report("strom")
        self.assertEqual(sev, "attention")
        self.assertIn("Eine offene Meldung. TV: Standby-Verbrauch zu hoch.", text)
        self.assertNotIn("PC: alt", text)
        self.data["strom"] = dict(STROM, offen=[])
        self.assertEqual(pi_reports.report("strom"), ("ok", "Beim Strom ist nichts auffällig. Noch am Lernen: 1 Gerät."))

    def test_zigbee(self):
        sev, text = pi_reports.report("zigbee")
        self.assertEqual(sev, "ok")
        self.assertTrue(text.startswith("Noch zu wenig Daten"))
        self.data["zigbee"] = dict(ZIGBEE, channel={"tone": "warn", "headline": "WLAN 7 überlappt Zigbee 20."})
        self.assertEqual(pi_reports.report("zigbee")[0], "attention")

    def test_akku(self):
        self.assertEqual(pi_reports.report("akku"), ("ok", "Der Speicher hat noch etwa 93 Prozent seiner Kapazität, rund "
                                                     "3,8 Kilowattstunden nutzbar (3,7 bis 3,9). Bisher 74 Vollzyklen, Garantie bis 2036."))
        self.assertEqual(pi_reports.report("akku", brief=True), ("ok", ""))
        self.data["akku"] = dict(AKKU, ha_connected=False, alerts=[{"text": "Temperatur zu hoch"}],
                                 storages={"system": {"capacity": {"status": "uncertain"}}})
        sev, text = pi_reports.report("akku")
        self.assertEqual(sev, "attention")
        self.assertIn("reichen die Daten noch nicht", text)
        self.assertIn("keine Daten von Home Assistant", text)
        self.assertIn("Temperatur zu hoch.", text)

    def test_double_encoded_text_is_repaired(self):
        broken = "verlässlich – gut".encode("utf-8").decode("cp1252")
        self.assertEqual(pi_reports._fix(broken), "verlässlich – gut")
        self.assertEqual(pi_reports._fix("schon gut"), "schon gut")

    def test_automationen(self):
        self.assertEqual(pi_reports.report("automationen"), ("ok", "Die Automationen sind in Ordnung. Dazu 1 Hinweise zur Ordnung."))
        self.data["automationen"]["findings"] = [LOOP, CONFLICT]
        sev, text = pi_reports.report("automationen")
        self.assertEqual(sev, "attention")
        self.assertIn("Ein kritischer Befund bei den Automationen: Echte Schleife: Jarvis: Nachtruhe Schlafzimmer.", text)
        self.assertIn("Eine Warnung: Konflikt bei Hue Schlafzimmer.", text)
        self.assertNotIn("Konflikt", pi_reports.report("automationen", brief=True)[1], "warnings stay out of the briefing")
        self.data["automationen"] = dict(PRUEFER, findings=[CONFLICT], last_check=time.time() - 60)
        self.assertEqual(pi_reports.report("automationen", brief=True)[0], "ok")
        self.data["automationen"] = dict(PRUEFER, last_check=time.time() - 5 * 3600)
        self.assertIn("seit 5 Stunden nicht mehr geprüft", pi_reports.report("automationen")[1])
        self.data["automationen"] = dict(PRUEFER, status="error", trusted=False, detail="HA antwortet nicht")
        self.assertIn("nicht sauber lesen: HA antwortet nicht.", pi_reports.report("automationen")[1])

    def test_drift_reads_metrics(self):
        m = self.data["drift"]
        self.assertEqual(m[("homelab_drift_findings", (("severity", "digest"), ("status", "open")))], 7.0)
        self.assertEqual(pi_reports.report("drift"),
                         ("ok", "Im Homelab ist nichts Dringendes. 7 kleinere Änderungen warten auf den Wochenbericht."))
        self.assertEqual(pi_reports.report("drift", brief=True), ("ok", "Im Homelab hat sich nichts Unerwartetes verändert."))
        text = (DRIFT_TEXT.format(now=time.time() - 5 * 3600).replace('host="pi"} 1', 'host="pi"} 0')
                .replace('"instant",status="open"} 0', '"instant",status="open"} 2'))
        self.data["drift"] = pi_reports._prom(text)
        sev, text = pi_reports.report("drift", brief=True)
        self.assertEqual(sev, "attention")
        self.assertIn("seit 5 Stunden nicht mehr gescannt", text)
        self.assertIn("erreicht pi nicht", text)
        self.assertIn("2 wichtige Änderungen", text)
        self.data["drift"] = {}
        self.assertEqual(pi_reports.report("drift")[0], "unknown")

    def test_unreachable_or_changed_format_never_raises(self):
        def boom(topic):
            raise OSError("down")
        pi_reports.fetch = boom
        self.assertEqual(pi_reports.report("strom"), ("unknown", "Stromfresser-Detektiv ist gerade nicht erreichbar."))
        self.assertEqual(pi_reports.briefing_line(), "")
        pi_reports.fetch = lambda topic: {"containers": "kaputt"}
        self.assertEqual(pi_reports.report("updates")[0], "unknown")

    def test_briefing_only_findings(self):
        line = pi_reports.briefing_line()
        self.assertIn("Breaking Changes", line)
        self.assertIn("Standby-Verbrauch", line)
        self.assertNotIn("Zigbee", line)
        self.data.update(updates={"containers": {}}, strom=dict(STROM, offen=[]))
        self.assertEqual(pi_reports.briefing_line(), "")

    def test_router_answers_locally(self):
        cases = {"Sind die Backups in Ordnung?": "Die Backups sind in Ordnung",
                 "Was sagt die Update-Schleuse?": "Home Assistant 2026.11.0",
                 "Ist beim Strom was auffällig?": "Stromfresser-Detektiv",
                 "Was sagt der Stromfresser-Detektiv?": "Stromfresser-Detektiv",
                 "Wo soll ich einen Zigbee-Router hinstellen?": "Noch zu wenig Daten",
                 "Was sagen die Werkzeuge auf dem Pi?": "Breaking Changes",
                 "Wie gesund ist der Akku?": "93 Prozent",
                 "Was sagt der Akku-Doktor?": "93 Prozent",
                 "Gibt es kaputte Automationen?": "Die Automationen sind in Ordnung",
                 "Was sagt der Automations-Prüfer?": "Die Automationen sind in Ordnung",
                 "Hat sich im Netz was verändert?": "7 kleinere Änderungen",
                 "Was sagt Homelab-Drift?": "7 kleinere Änderungen"}
        for text, start in cases.items():
            r = router.route(text)
            self.assertEqual(r.get("handler"), "pi_reports", text)
            self.assertIn(start, r["reply"], text)

    def test_other_questions_are_not_taken(self):
        for text in ("Was sind meine Stromfresser?", "Gibt es Updates?", "Wie geht es dem Pi?",
                     "Wie voll ist der Speicher?", "Erstelle eine Automation fürs Licht",
                     "Warum wurde Home Assistant nicht aktualisiert?", "Mach das Licht aus"):
            r = router.route(text)
            self.assertNotEqual(r.get("handler"), "pi_reports", text)

    def test_why_questions_go_to_claude_with_the_tool(self):
        r = router.route("Warum ist das Backup-Test-Ergebnis auffällig?")
        self.assertEqual(r["kind"], "llm")
        self.assertIn("homelab", r["groups"])

    def test_mcp_tool_text(self):
        text = pi_reports.describe("alles")
        self.assertIn("[Restore Probe · ok]", text)
        self.assertIn("[Update-Schleuse · attention]", text)


if __name__ == "__main__":
    unittest.main()
