"""
The additions of 10.11.2026 afternoon: weekly memory review, NAS pool check, HA noise filter,
the look at tomorrow and Uptime Kuma alarms with a restart offer. Everything faked; no HA, NAS or Pi.

    .venv\\Scripts\\python.exe -m unittest tests.test_ausbau_1110 -v
"""
import isolate  # noqa: F401  (must come first)
import datetime as dt
import shutil
import time
import unittest
import zipfile
from unittest import mock

import commands
import events
import ha_events
import kuma_alerts
import memory
import memory_nas
import memory_weekly as mw
import proposals
import situation
import tomorrow

LOG = """---
type: lernen
---

# Lernprotokoll

## 2026-11-03 20:00
- Gemerkt ([[Golf]]): Spielt Sonntags in Stolpe

## 2026-11-12 21:00
- Gut: kurz geantwortet
- Gemerkt ([[Anna]]): Anna hat am 3. Mai Geburtstag
- Nicht gemerkt ([[Profil]]): wohnt in Köln — Widerspruch

## 2026-11-14 19:00
- Gemerkt ([[Vorlieben]]): Mag kein Koriander
"""


class Weekly(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(memory.VAULT, ignore_errors=True)
        memory.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        (memory.MEMORY_DIR / "Lernprotokoll.md").write_text(LOG, encoding="utf-8")
        commands.update_state(lambda d: (d.pop("memory_weekly", None), d.get("jobs", {}).pop("memory-weekly", None), True)[2])
        self.said = []
        p = mock.patch.object(events, "publish", lambda t, text, **kw: self.said.append(text))
        p.start()
        self.addCleanup(p.stop)

    def test_lists_this_weeks_saves_on_sunday(self):
        sunday = dt.datetime(2026, 11, 15, 19, 0)
        text = mw.step(sunday)
        self.assertIn("2 Dinge", text)
        self.assertIn("1. Anna hat am 3. Mai Geburtstag.", text)
        self.assertNotIn("Stolpe", text)                     # older than a week
        self.assertNotIn("Köln", text)                       # not saved
        self.assertIsNone(mw.step(sunday))                   # once per week
        self.assertIsNone(mw.step(dt.datetime(2026, 11, 14, 19, 0)))   # not on Saturday

    def test_strike_by_number_and_keep_lists_alone(self):
        mw._open([{"day": "2026-11-12", "note": "Anna", "text": "Anna hat am 3. Mai Geburtstag"},
                  {"day": "2026-11-14", "note": "Vorlieben", "text": "Mag kein Koriander"}])
        self.assertIsNone(mw.handle(" streich die milch von der einkaufsliste "))
        with mock.patch.object(mw, "_forget", return_value="Vergessen: „Mag kein Koriander“.") as fg:
            self.assertIn("Vergessen", mw.handle(" streich nummer zwei "))
        self.assertEqual(fg.call_args[0][0]["text"], "Mag kein Koriander")
        self.assertEqual(len(mw._pending()), 1)
        self.assertIn("bleibt alles", mw.handle(" passt "))
        self.assertEqual(mw._pending(), [])
        self.assertIsNone(mw.handle(" passt "))              # nothing open: not ours

    def test_forget_finds_linked_bullets(self):
        import memory_tools
        note = memory.MEMORY_DIR / "Personen" / "Anna.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# Anna\n\n- [[Anna]] hat am 3. Mai Geburtstag (abgeleitet, 2026-11-12)\n", encoding="utf-8")
        # the review passes the note too (the same words also stand in the Lernprotokoll)
        self.assertIn("Vergessen", mw._forget({"note": "Anna", "text": "Anna hat am 3. Mai Geburtstag"}))
        self.assertNotIn("Geburtstag", note.read_text(encoding="utf-8"))


class NasPool(unittest.TestCase):
    def test_check_copy(self):
        import sqlite3
        tmp = isolate.TMP / "nascheck"
        tmp.mkdir(exist_ok=True)
        z, i = tmp / "v.zip", tmp / "i.sqlite"
        with zipfile.ZipFile(z, "w") as zz:
            zz.writestr("Erinnerungen/Profil.md", "# Profil")
        con = sqlite3.connect(i)
        con.execute("CREATE VIRTUAL TABLE mem USING fts5(title, body)")
        con.execute("INSERT INTO mem VALUES ('a', 'b')")
        con.commit()
        con.close()
        self.assertEqual(memory_nas.check_copy(z, i), [])
        z.write_bytes(b"kaputt")
        self.assertTrue(memory_nas.check_copy(z, i))

    def test_finding_old_or_failed(self):
        now = time.time()
        with mock.patch.object(memory_nas, "enabled", return_value=True):
            with mock.patch.object(memory_nas, "saved_state", return_value={"last_ok": now - 3600}):
                self.assertTrue(memory_nas.finding(now)["ok"])
            with mock.patch.object(memory_nas, "saved_state", return_value={"last_ok": now - 3 * 86400}):
                self.assertFalse(memory_nas.finding(now)["ok"])
            with mock.patch.object(memory_nas, "saved_state",
                                   return_value={"last_ok": now, "verify_problems": ["Index leer"]}):
                f = memory_nas.finding(now)
                self.assertFalse(f["ok"])
                self.assertIn("Index leer", f["down"])
            with mock.patch.object(memory_nas, "saved_state", return_value={}):
                self.assertIsNone(memory_nas.finding(now))


class Noise(unittest.TestCase):
    def setUp(self):
        ha_events._FLIPS.clear()
        ha_events._MUTED.clear()

    def test_flapping_sensor_stops_waking(self):
        eid = "binary_sensor.praesenz_wohnzimmer"
        t0 = 1_000_000.0
        with mock.patch.object(ha_events, "NOISY_PER_H", 5):
            woke = [ha_events.worth_waking(eid, {"state": "off" if i % 2 else "on"},
                                           {"state": "on" if i % 2 else "off"}, now=t0 + i * 60) for i in range(8)]
            self.assertEqual(woke, [True] * 5 + [False] * 3)
            self.assertIn(eid, ha_events._MUTED)
            # an hour later it may wake again
            self.assertTrue(ha_events.worth_waking(eid, {"state": "on"}, {"state": "off"}, now=t0 + 3 * 3600))
            # a quiet door is not affected
            self.assertTrue(ha_events.worth_waking("binary_sensor.tuer", {"state": "off"}, {"state": "on"}, now=t0))


class Tomorrow(unittest.TestCase):
    def test_sentence_from_all_sources(self):
        day = dt.date(2026, 11, 11)
        evts = [{"start": dt.datetime(2026, 11, 11, 9, 30), "title": "Zahnarzt", "allday": False},
                {"start": dt.datetime(2026, 11, 11, 15, 0), "title": "Golf", "allday": False},
                {"start": dt.datetime(2026, 11, 11), "title": "📦 Amazon · DHL", "allday": True}]
        now = dt.datetime(2026, 11, 10, 22, 30)
        with mock.patch("scheduler.items", return_value=[{"kind": "alarm", "at": dt.datetime(2026, 11, 11, 6, 45).timestamp()}]), \
                mock.patch("icloud_calendar.configured", return_value=True), \
                mock.patch("icloud_calendar.events", return_value=evts), \
                mock.patch.object(tomorrow, "_weather", return_value="Wetter: klar, 2 bis 9 Grad, Regenrisiko 0 Prozent."):
            text = tomorrow.spoken(now)
        self.assertEqual(text, "Ihr Wecker klingelt um 6:45. Morgen um 9:30 Zahnarzt, danach noch ein Termin. "
                               "Ein Paket von Amazon soll kommen. Wetter: klar, 2 bis 9 Grad, Regenrisiko 0 Prozent.")
        self.assertIsNotNone(day)

    def test_router_phrase(self):
        with mock.patch.object(tomorrow, "spoken", return_value="x"):
            self.assertEqual(tomorrow.handle(" was steht morgen an "), "x")
            self.assertIsNone(tomorrow.handle(" was steht heute an "))


class Kuma(unittest.TestCase):
    CFG = {"kuma_restart": {"Blast-Radius": "blast-radius"}}

    def setUp(self):
        proposals.PATH.unlink(missing_ok=True)
        kuma_alerts._PUSHED.clear()
        self.said = []
        p = mock.patch.object(events, "publish", lambda t, text, **kw: self.said.append(text))
        p.start()
        self.addCleanup(p.stop)

    def test_pushed_outage_skips_the_second_round(self):
        mons = [{"name": "Web · Blast-Radius :8098", "up": False, "minutes": 1}]
        self.assertEqual(len(situation.kuma_findings(mons, {})), 1)
        with mock.patch("threading.Thread"):
            kuma_alerts.on_event({"source": "kuma", "name": "Web · Blast-Radius :8098", "status": 0})
        self.assertEqual(len(situation.kuma_findings(mons, {})), 2)

    def test_restart_offer_and_withdrawal(self):
        down = [{"name": "Web · Blast-Radius :8098", "up": False}, {"name": "DNS · Pi-hole", "up": False}]
        self.assertEqual(kuma_alerts.sync(down, self.CFG), ["blast-radius"])
        self.assertEqual(kuma_alerts.sync(down, self.CFG), [])          # asked once
        self.assertIn("Soll ich den Container blast-radius", self.said[0])
        p = proposals.pending()[0]
        self.assertEqual((p["kind"], p["payload"]["container"]), ("restart", "blast-radius"))
        kuma_alerts.sync([{"name": "Web · Blast-Radius :8098", "up": True}], self.CFG)
        self.assertEqual(proposals.pending(), [])

    def test_restart_only_listed_containers(self):
        with mock.patch("homelab_watch.config", return_value=self.CFG), \
                mock.patch("jarvis_mcp._pi_run", return_value="exit 0\nblast-radius") as run:
            self.assertIn("neu gestartet", kuma_alerts.restart("blast-radius"))
            run.assert_called_once_with("docker restart blast-radius")
            self.assertIn("nicht neu", kuma_alerts.restart("homeassistant; rm -rf /"))
            self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
