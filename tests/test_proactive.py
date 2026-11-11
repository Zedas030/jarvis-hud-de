"""
Proactive mode: sources, repeats, briefings, triggers, voice commands, feedback. No network, no house, no Claude:
Home Assistant is a dict, the speaker a list, the model a stub.

    .venv\\Scripts\\python.exe -m unittest tests.test_proactive -v
"""
import datetime as dt
import json
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402  (must come before any Jarvis module)

import clock                   # noqa: E402

import away_check              # noqa: E402
import background              # noqa: E402
import commands                # noqa: E402
import events                  # noqa: E402
import icloud_calendar         # noqa: E402
import morning_echo            # noqa: E402
import proactive               # noqa: E402
import proactive_config as pconf  # noqa: E402
import proactive_sources as srcs  # noqa: E402
import quiet_rules             # noqa: E402
import router                  # noqa: E402
import routines                # noqa: E402
import weather_alerts          # noqa: E402

TZ = dt.datetime.now().astimezone().tzinfo
DAY = dt.datetime(2026, 11, 5, 14, 0, tzinfo=TZ)
NIGHT = dt.datetime(2026, 11, 5, 23, 30, tzinfo=TZ)
EVENING = dt.datetime(2026, 11, 5, 21, 0, tzinfo=TZ)

YAML = """
enabled: true
ha: {switch_proactive: input_boolean.jarvis_proaktiv, switch_guests: input_boolean.jarvis_gaeste_modus,
     switch_quiet: input_boolean.jarvis_ruhe}
output: {formulate_with_llm: false, max_seconds: 25}
quiet: {hours: "22-7"}
calls: {windows_mic: false, entities: [binary_sensor.phone_focus]}
triggers:
  morning: {sensors: [binary_sensor.bad], from_hour: 5, until_hour: 11}
  arrival: {room_wait_minutes: 3}
sources:
  calendar: {enabled: false}
  weather: {enabled: false}
  dwd: {enabled: false}
  parcels: {enabled: false}
  energy: {enabled: false}
  noah: {enabled: true, switch: input_boolean.noah_diff_warnung, master: sensor.bat0, slave: sensor.bat1}
"""


def ago(minutes, now=DAY):
    return (now - dt.timedelta(minutes=minutes)).isoformat()


def house(**over):
    s = {"input_boolean.noah_diff_warnung": {"state": "off", "last_changed": ago(5)},
         "sensor.bat0": {"state": "45"}, "sensor.bat1": {"state": "31"},
         "binary_sensor.kuhlschrank_cloud_verbindung": {"state": "on", "last_changed": ago(600),
                                                         "attributes": {"friendly_name": "Kühlschrank Cloud-Verbindung"}},
         "input_boolean.jarvis_proaktiv": {"state": "on"}, "input_boolean.jarvis_gaeste_modus": {"state": "off"},
         "input_boolean.jarvis_ruhe": {"state": "off"}, "binary_sensor.phone_focus": {"state": "off"},
         "binary_sensor.bad": {"state": "off"}}
    s.update(over)
    return {k: dict(v, entity_id=k) for k, v in s.items()}


class Base(unittest.TestCase):
    def setUp(self):
        pconf.PATH.write_text(YAML, encoding="utf-8")
        pconf._CACHE.update(cfg=None, mtime=None)
        commands.update_state(lambda d: [d.pop(k, None) for k in ("proactive", "briefing_day", "away_since",
                                                                  "welcomed", "away_checked")])
        proactive.HISTORY.unlink(missing_ok=True)
        events.NOTICES.unlink(missing_ok=True)
        with events._COND:
            events._LOG.clear()
        proactive._SRC_AT.clear()
        proactive._SRC_CACHE.clear()
        proactive._MORNING_LAST.clear()
        self.said = []
        proactive.SPEAK_HOOK[:] = [lambda room, text: self.said.append((room, text))]
        self.switches = []
        p = mock.patch.object(proactive, "_set_switch", side_effect=lambda e, on: self.switches.append((e, on)))
        p.start()
        self.addCleanup(p.stop)
        w = mock.patch("whereabouts.room", return_value="Wohnzimmer")
        w.start()
        self.addCleanup(w.stop)
        # the afternoon of today, whatever the real clock says: outside the quiet hours "22-7"
        self.now = clock.today_at(13, 30)
        self.addCleanup(clock.frozen(self.now, proactive, quiet_rules).stop)

    @classmethod
    def tearDownClass(cls):
        pconf.PATH.unlink(missing_ok=True)          # the other test files expect the mode off
        pconf._CACHE.update(cfg=None, mtime=None)
        proactive.SPEAK_HOOK.clear()

    def cfg(self, **over):
        return pconf.load() if not over else pconf.make(dict(pconf._read(pconf.PATH), **over))

    def rows(self):
        return [json.loads(ln) for ln in proactive.HISTORY.read_text(encoding="utf-8").splitlines()] \
            if proactive.HISTORY.exists() else []


# ── sources ──────────────────────────────────────────────────
class Sources(Base):
    def ctx(self, mode="instant", states=None, now=DAY, since=None):
        return {"now": now, "mode": mode, "states": house() if states is None else states, "since": since}

    def test_noah_only_while_the_switch_is_on(self):
        self.assertEqual(srcs.noah(self.cfg(), self.ctx()), [])
        on = house(**{"input_boolean.noah_diff_warnung": {"state": "on", "last_changed": ago(2)}})
        n = srcs.noah(self.cfg(), self.ctx(states=on))[0]
        self.assertEqual(n["text"], "Die NOAH-Akkus laufen auseinander: 14 Prozent Unterschied.")
        self.assertIn("Master 45 %, Slave 31 %", n["details"])
        self.assertEqual(n["fp"], ago(2))                     # a new episode = a new fingerprint

    def test_offline_after_minutes_and_ignore(self):
        st = house(**{"binary_sensor.tv_cloud_verbindung": {"state": "off", "last_changed": ago(30),
                                                            "attributes": {"friendly_name": "TV Cloud-Verbindung"}},
                      "binary_sensor.pc_cloud_verbindung": {"state": "off", "last_changed": ago(3)},
                      "binary_sensor.luft_cloud_verbindung": {"state": "unavailable", "last_changed": ago(900)}})
        found = srcs.devices_offline(self.cfg(), self.ctx(states=st))
        self.assertEqual([n["id"] for n in found], ["offline:binary_sensor.luft_cloud_verbindung",
                                                    "offline:binary_sensor.tv_cloud_verbindung"])
        self.assertEqual(found[1]["text"], "TV ist offline.")
        cfg = pconf.make({"sources": {"devices_offline": {"ignore": ["binary_sensor.luft_cloud_verbindung"]}}})
        self.assertEqual(len(srcs.devices_offline(cfg, self.ctx(states=st))), 1)

    def test_safety(self):
        st = house(**{"binary_sensor.wasser_bad": {"state": "on", "last_changed": ago(1),
                                                   "attributes": {"device_class": "moisture",
                                                                  "friendly_name": "Wassermelder Bad"}}})
        n = srcs.safety(self.cfg(), self.ctx(states=st))[0]
        self.assertEqual(n["text"], "Achtung: Wassermelder Bad meldet Wasser!")
        self.assertEqual(n["kind"], "safety")

    def test_ha_sources_fail_without_ha(self):
        for fn in (srcs.noah, srcs.devices_offline, srcs.safety):
            with self.assertRaises(RuntimeError):
                fn(self.cfg(), {"now": DAY, "mode": "instant", "states": None})

    def test_evening_devices_and_windows(self):
        st = house(**{"light.wohnzimmer": {"state": "on", "attributes": {"friendly_name": "Licht Wohnzimmer"}},
                      "binary_sensor.fenster": {"state": "on", "attributes": {"device_class": "window",
                                                                             "friendly_name": "Fenster Küche"}}})
        with mock.patch("jarvis_profile.ha", return_value={}):
            self.assertEqual(srcs.devices_on(self.cfg(), self.ctx("evening", st))[0]["text"],
                             "Noch an: Licht Wohnzimmer.")
        self.assertEqual(srcs.windows(self.cfg(), self.ctx("evening", st))[0]["text"], "Noch offen: Fenster Küche.")
        self.assertEqual(srcs.windows(self.cfg(), self.ctx("morning", st)), [])

    def test_missed_skips_own_and_alarms(self):
        t = time.time()
        events._note({"at": t, "kind": "appliance", "t": "say", "text": "Sir, die Spülmaschine ist fertig."})
        events._note({"at": t, "kind": "proactive", "t": "say", "text": "Schon gesagt."})
        events._note({"at": t, "kind": "alarm", "t": "alarm", "text": "Timer."})
        n = srcs.missed(self.cfg(), self.ctx("arrival", since=t - 10))[0]
        self.assertEqual(n["text"], "Während du weg warst: Die Spülmaschine ist fertig.")

    def test_calendar_and_parcels(self):
        base = DAY.replace(tzinfo=None)
        evts = [{"start": base + dt.timedelta(minutes=50), "end": base + dt.timedelta(minutes=110),
                 "title": "Zahnarzt", "allday": False},
                {"start": base.replace(hour=19), "title": "Kino", "allday": False},
                {"start": base.replace(hour=0), "title": "📦 Amazon: Kabel · DHL", "allday": True},
                {"start": base.replace(hour=9), "end": base.replace(hour=10), "title": "Vorbei", "allday": False}]
        with mock.patch.object(icloud_calendar, "configured", return_value=True), \
                mock.patch.object(icloud_calendar, "events", return_value=evts):
            cal = srcs.calendar(self.cfg(), self.ctx("arrival"))
            par = srcs.parcels(self.cfg(), self.ctx("arrival"))
        self.assertEqual([(n["kind"], n["text"]) for n in cal],
                         [("calendar_soon", "Um 14:50 steht Zahnarzt an."), ("calendar", "Heute noch: um 19:00 Kino.")])
        self.assertEqual(par[0]["text"], "Heute kommt ein Paket von Amazon.")

    def test_tomorrow_early(self):
        tomorrow = (EVENING + dt.timedelta(days=1)).replace(tzinfo=None)
        evts = [{"start": tomorrow.replace(hour=7, minute=30), "title": "Flug", "allday": False}]
        with mock.patch.object(icloud_calendar, "configured", return_value=True), \
                mock.patch.object(icloud_calendar, "events", return_value=evts):
            n = srcs.calendar(self.cfg(), self.ctx("evening", now=EVENING))
        self.assertEqual(n[0]["text"], "Morgen früh um 7:30: Flug.")


# ── collecting: a dead source is skipped and logged once ─────
class Collect(Base):
    def test_failing_source_skipped_and_noted_once(self):
        boom = mock.Mock(side_effect=RuntimeError("Wetterdienst weg"))
        cfg = self.cfg()
        with mock.patch.dict(srcs.SOURCES, noah=boom), self.assertLogs("proactive", "WARNING") as logs:
            self.assertEqual(proactive.collect(cfg, "instant", house(), DAY), [])
            proactive.collect(cfg, "instant", house(), DAY)
        self.assertEqual(len([m for m in logs.output if "noah" in m]), 1)
        self.assertIn("noah", proactive._st()["down"])
        proactive.collect(cfg, "instant", house(), DAY)          # back: the outage ends
        self.assertNotIn("noah", proactive._st().get("down", {}))

    def test_switched_off_source_is_not_asked(self):
        spy = mock.Mock(return_value=[])
        cfg = pconf.make({"sources": {"noah": {"enabled": False}}})
        with mock.patch.dict(srcs.SOURCES, noah=spy):
            proactive.collect(cfg, "instant", house(), DAY)
        spy.assert_not_called()

    def test_slow_source_times_out(self):
        def slow(cfg, ctx):
            time.sleep(1.0)
            return []
        with mock.patch.dict(srcs.SOURCES, noah=slow), mock.patch.object(proactive, "SOURCE_TIMEOUT_S", 0.1):
            self.assertEqual(proactive.collect(self.cfg(), "instant", house(), DAY), [])
        self.assertIn("keine Antwort", proactive._st()["down"]["noah"]["error"])


# ── instant notices ──────────────────────────────────────────
NOAH_ON = {"input_boolean.noah_diff_warnung": {"state": "on", "last_changed": ago(2)}}


class Instant(Base):
    def test_said_once_then_again_only_when_changed(self):
        cfg = self.cfg()
        proactive.instant(cfg, house(**NOAH_ON), DAY)
        proactive.instant(cfg, house(**NOAH_ON), DAY)
        self.assertEqual(self.said, [("Wohnzimmer", "Die NOAH-Akkus laufen auseinander: 14 Prozent Unterschied.")])
        again = {"input_boolean.noah_diff_warnung": {"state": "on", "last_changed": ago(1)}}
        proactive.instant(cfg, house(**again), DAY)
        self.assertEqual(len(self.said), 2)
        self.assertEqual(self.rows()[-1]["action"], "gesprochen")

    def test_quiet_hours_hold_until_the_morning_briefing(self):
        cfg = self.cfg()
        proactive.instant(cfg, house(**NOAH_ON), NIGHT)
        proactive.instant(cfg, house(**NOAH_ON), NIGHT)
        self.assertEqual(self.said, [])
        rows = self.rows()
        self.assertEqual(len(rows), 1)                           # shown once, not every 30 s
        self.assertEqual((rows[0]["action"], rows[0]["reason"]), ("zurückgehalten", "Ruhezeit"))
        proactive.instant(cfg, house(**NOAH_ON), DAY.replace(hour=7, minute=5))
        self.assertEqual(self.said, [])                          # not at 7:00 sharp — the morning brings it

    def test_critical_speaks_at_night(self):
        water = {"binary_sensor.wasser": {"state": "on", "last_changed": ago(1, NIGHT),
                                          "attributes": {"device_class": "moisture", "friendly_name": "Wassermelder"}}}
        proactive.instant(self.cfg(), house(**water), NIGHT)
        self.assertEqual(self.said, [("Wohnzimmer", "Achtung: Wassermelder meldet Wasser!")])

    def test_guests_then_said_afterwards(self):
        cfg = self.cfg()
        guests = {"input_boolean.jarvis_gaeste_modus": {"state": "on"}}
        proactive.instant(cfg, house(**NOAH_ON, **guests), DAY)
        self.assertEqual(self.said, [])
        self.assertEqual(self.rows()[-1]["reason"], "Gäste-Modus")
        proactive.instant(cfg, house(**NOAH_ON), DAY)            # guests gone: the important news follows
        self.assertEqual(len(self.said), 1)

    def test_call_holds(self):
        focus = {"binary_sensor.phone_focus": {"state": "on"}}
        proactive.instant(self.cfg(), house(**NOAH_ON, **focus), DAY)
        self.assertEqual(self.said, [])
        self.assertEqual(self.rows()[-1]["reason"], "Telefonat")

    def test_pc_microphone_counts_unless_ignored(self):
        cfg = pconf.make({"calls": {"windows_mic": True, "mic_ignore": ["opera.exe"]}})
        with mock.patch("mic_use.in_use", return_value=["opera.exe"]):
            self.assertFalse(proactive._call_active(cfg, house()))
        with mock.patch("mic_use.in_use", return_value=["opera.exe", "teams.exe"]):
            self.assertTrue(proactive._call_active(cfg, house()))

    def test_proactive_switch_off(self):
        off = {"input_boolean.jarvis_proaktiv": {"state": "off"}}
        proactive.instant(self.cfg(), house(**NOAH_ON, **off), DAY)
        self.assertEqual(self.said, [])
        self.assertEqual(self.rows()[-1]["reason"], "Proaktiv-Modus aus")


# ── briefings ────────────────────────────────────────────────
def notice(i, level, words=5):
    return {"id": f"n{i}", "kind": "weather", "text": " ".join(["wort"] * words) + ".", "details": "",
            "fp": "x", "valid_until": None, "level": level, "source": "test"}


class Compose(Base):
    def test_budget_keeps_critical_and_orders(self):
        items = [notice(1, "nett", 20), notice(2, "wichtig", 20), notice(3, "nett", 20), notice(4, "kritisch", 30)]
        text, spoken, rest = proactive.compose(self.cfg(), "arrival", items)
        self.assertEqual([n["id"] for n in spoken], ["n4", "n2"])      # 62 words fit 25 s × 2.5
        self.assertEqual([n["id"] for n in rest], ["n1", "n3"])
        self.assertTrue(text.startswith("Willkommen zurück, Sir."))
        self.assertLessEqual(proactive.words(text), 62 + 3)

    def test_model_words_it_or_the_fixed_text_stands_in(self):
        cfg = pconf.make({"output": {"formulate_with_llm": True, "llm_timeout_s": 2}})
        items = [notice(1, "wichtig")]
        with mock.patch.object(background, "ask", return_value="Guten Morgen, Sir. Heute regnet es."):
            self.assertEqual(proactive.compose(cfg, "morning", items)[0], "Guten Morgen, Sir. Heute regnet es.")
        with mock.patch.object(background, "ask", return_value=None):
            self.assertEqual(proactive.compose(cfg, "morning", items)[0], "Guten Morgen, Sir. wort wort wort wort wort.")
        with mock.patch.object(background, "ask", return_value="* Liste " + "lang " * 200):
            self.assertTrue(proactive.compose(cfg, "morning", items)[0].startswith("Guten Morgen, Sir. wort"))

    def test_slow_model_does_not_block(self):
        cfg = pconf.make({"output": {"formulate_with_llm": True, "llm_timeout_s": 0.2}})
        with mock.patch.object(background, "ask", side_effect=lambda *a: time.sleep(2) or "zu spät"):
            t0 = time.time()
            text = proactive.compose(cfg, "arrival", [notice(1, "wichtig")])[0]
        self.assertLess(time.time() - t0, 1.5)
        self.assertTrue(text.startswith("Willkommen zurück"))


class Briefings(Base):
    def test_arrival_flow_from_away_check(self):
        commands.update_state(lambda d: d.update(away_since=time.time() - 7200))
        with mock.patch("jarvis_profile.ha", side_effect=lambda k, d=None: {"person": "person.me"}.get(k, d)):
            r = away_check.check([{"entity_id": "person.me", "state": "home",
                                   "last_changed": dt.datetime.now(dt.timezone.utc).isoformat()}])
        self.assertEqual(r, "proaktiv")
        self.assertIn("arrival", proactive._st())
        proactive.tick(list(house(**NOAH_ON).values()))
        self.assertEqual(self.said[0][0], "Wohnzimmer")
        self.assertTrue(self.said[0][1].startswith("Willkommen zurück, Sir. Die NOAH-Akkus laufen auseinander"))
        self.assertEqual(len(self.said), 1)                      # the instant round knows it was said
        self.assertNotIn("arrival", proactive._st())

    def test_arrival_waits_for_the_room(self):
        proactive.on_arrival(time.time() - 7200)
        with mock.patch("whereabouts.room", return_value=""):
            proactive.tick(list(house().values()))
            self.assertEqual(self.said, [])
            proactive._update(lambda p: p["arrival"].update(at=time.time() - 200))
            proactive.tick(list(house().values()))
        self.assertEqual(len(self.said), 1)                      # after the wait: the default speaker

    def test_morning_on_first_bathroom_entry_once(self):
        now = DAY.replace(hour=6, minute=40, tzinfo=None)
        off, on = house(), house(**{"binary_sensor.bad": {"state": "on"}})
        for st in (off, on, off, on):
            proactive.tick(list(st.values()), now=now)
        mornings = [t for _, t in self.said if t.startswith("Guten Morgen")]
        self.assertEqual(len(mornings), 1)                       # in the quiet hours: the morning ends the night
        self.assertEqual(commands.load()["briefing_day"], now.strftime("%Y-%m-%d"))

    def test_morning_not_in_the_afternoon_and_old_module_steps_back(self):
        off, on = house(), house(**{"binary_sensor.bad": {"state": "on"}})
        for st in (off, on):
            proactive.tick(list(st.values()), now=DAY.replace(tzinfo=None))
        self.assertFalse([t for _, t in self.said if t.startswith("Guten Morgen")])
        self.assertTrue(proactive.owns("morning"))
        with mock.patch.object(morning_echo, "_speak") as speak, \
                mock.patch.object(morning_echo, "config", return_value={"room": "X", "triggers": ["light.l"]}):
            morning_echo.check([{"entity_id": "light.l", "state": "off"}], DAY.replace(hour=7, tzinfo=None))
            morning_echo.check([{"entity_id": "light.l", "state": "on"}], DAY.replace(hour=7, tzinfo=None))
        speak.assert_not_called()

    def test_briefing_held_by_guests_only_critical(self):
        st = house(**NOAH_ON, **{"input_boolean.jarvis_gaeste_modus": {"state": "on"},
                                 "binary_sensor.rauch": {"state": "on", "last_changed": ago(1),
                                                         "attributes": {"device_class": "smoke", "friendly_name": "Rauchmelder"}}})
        proactive.briefing(self.cfg(), "arrival", st, since=time.time() - 3600, now=DAY)
        self.assertEqual(self.said, [("Wohnzimmer", "Achtung: Rauchmelder meldet Rauch!")])
        held = [r for r in self.rows() if r.get("id") == "noah"]
        self.assertEqual((held[0]["action"], held[0]["reason"]), ("zurückgehalten", "Gäste-Modus"))

    def test_disabled_source_is_skipped_in_the_briefing(self):
        data = pconf._read(pconf.PATH)
        data["sources"]["noah"]["enabled"] = False             # the others stay off too: no network in tests
        cfg = pconf.make(data)
        text = proactive.briefing(cfg, "arrival", house(**NOAH_ON), since=time.time() - 60, now=DAY)
        self.assertEqual(text, "Willkommen zurück, Sir. Während du weg warst, war alles ruhig.")


# ── voice commands ───────────────────────────────────────────
class Voice(Base):
    def local(self, text):
        r = router.route(text)
        self.assertEqual(r["kind"], "local", r)
        return r["reply"], r["handler"]

    def test_quiet_for_an_hour_and_end(self):
        reply, handler = self.local("Jarvis, Ruhe für eine Stunde")
        self.assertEqual(handler, "proactive")
        self.assertTrue(reply.startswith("Verstanden, bis "))
        self.assertEqual(proactive._st()["quiet_until"], (self.now + dt.timedelta(hours=1)).timestamp())
        self.assertEqual(self.switches[-1], ("input_boolean.jarvis_ruhe", True))
        proactive.instant(self.cfg(), house(**NOAH_ON), self.now.replace(hour=14, minute=0))
        self.assertEqual(self.said, [])
        self.assertEqual(self.rows()[-1]["reason"], "Ruhe")
        self.assertEqual(self.local("Ruhe aufheben")[0], "Gut, ich melde mich wieder.")
        self.assertEqual(self.switches[-1], ("input_boolean.jarvis_ruhe", False))

    def test_today_no_announcements(self):
        reply, _ = self.local("Heute keine Ansagen mehr")
        self.assertIn("bis morgen 05:00 Uhr", reply)

    def test_quiet_switch_off_in_ha_ends_it(self):
        self.local("Ruhe für zwei Stunden")
        on = house(**{"input_boolean.jarvis_ruhe": {"state": "on"}})
        proactive._sync_quiet(self.cfg(), on)
        proactive._sync_quiet(self.cfg(), house())
        self.assertFalse(proactive._st()["quiet_until"])

    def test_quiet_expires_and_switch_goes_off(self):
        proactive._update(lambda p: p.update(quiet_until=time.time() - 1, quiet_by_jarvis=True))
        proactive._sync_quiet(self.cfg(), house(**{"input_boolean.jarvis_ruhe": {"state": "on"}}))
        self.assertEqual(self.switches, [("input_boolean.jarvis_ruhe", False)])

    def test_more_refers_to_the_last_notice(self):
        self.assertEqual(self.local("mehr dazu")[0], "Ich habe zuletzt nichts von selbst gemeldet.")
        proactive.instant(self.cfg(), house(**NOAH_ON), DAY)
        reply, _ = self.local("Jarvis, mehr dazu")
        self.assertIn("Master 45 %, Slave 31 %", reply)

    def test_unimportant_three_times_lowers_and_reset(self):
        proactive.instant(self.cfg(), house(**NOAH_ON), DAY)
        self.assertIn("1 von 3", self.local("unwichtig")[0])
        self.assertIn("2 von 3", self.local("Das ist unwichtig")[0])
        self.assertIn("herunter auf „nett“", self.local("unwichtig")[0])
        self.assertEqual(proactive.level_of("noah", self.cfg()), "nett")
        self.assertIn("NOAH-Akkus 3× unwichtig, Stufe nett statt wichtig",
                      self.local("Was hast du über Ansagen gelernt?")[0])
        proactive._update(lambda p: p.update(said={}))
        proactive.instant(self.cfg(), house(**{"input_boolean.noah_diff_warnung":
                                               {"state": "on", "last_changed": ago(1)}}), DAY)
        self.assertEqual(len(self.said), 1)                      # nett: no longer interrupts
        self.assertIn("normale Stufe", self.local("Ansage-Feedback zurücksetzen")[0])
        self.assertEqual(proactive.level_of("noah", self.cfg()), "wichtig")

    def test_named_kind_and_critical_stays(self):
        self.assertIn("Wetter", self.local("Wetter unwichtig")[0])
        self.assertEqual(proactive._st()["feedback"].keys(), {"weather"})
        proactive._remember_last("instant", [{"kind": "safety", "text": "x", "details": ""}], "x")
        self.assertIn("bleibt kritisch", self.local("unwichtig")[0])

    def test_question_is_not_feedback(self):
        proactive.instant(self.cfg(), house(**NOAH_ON), DAY)
        r = router.route("Ist das nicht wichtig?")
        self.assertNotEqual(r.get("handler"), "proactive")

    def test_guest_mode_by_voice(self):
        self.assertEqual(self.local("Gäste-Modus an")[0], "Gäste-Modus an, ich melde nur noch Kritisches.")
        self.assertEqual(self.switches[-1], ("input_boolean.jarvis_gaeste_modus", True))

    def test_good_night_routine_gets_the_evening_check(self):
        routines.PATH.write_text(json.dumps([{"name": "Gute Nacht", "triggers": ["gute nacht"],
                                              "steps": [{"say": "Schlafen Sie gut."}]}]), encoding="utf-8")
        routines._CACHE["mtime"] = None
        self.addCleanup(lambda: routines.PATH.unlink(missing_ok=True))
        st = house(**{"light.flur": {"state": "on", "attributes": {"friendly_name": "Flurlicht"}}})
        with mock.patch.object(proactive, "_states", return_value=st), \
                mock.patch("jarvis_profile.ha", return_value={}):
            reply, handler = self.local("Gute Nacht, Jarvis")
        self.assertEqual(handler, "routine")
        self.assertEqual(reply, "Schlafen Sie gut. Gute Nacht, Sir. Noch an: Flurlicht.")

    def test_good_night_without_routine(self):
        routines.PATH.unlink(missing_ok=True)
        routines._CACHE["mtime"] = None
        with mock.patch.object(proactive, "_states", return_value=house()):
            reply, handler = self.local("Jarvis, gute Nacht")
        self.assertEqual((handler, reply), ("proactive", "Gute Nacht, Sir. Alles erledigt, nichts mehr offen."))

    def test_switched_off_everything_as_before(self):
        pconf.PATH.write_text("enabled: false\n", encoding="utf-8")
        pconf._CACHE.update(cfg=None, mtime=None)
        r = router.route("Ruhe für eine Stunde")
        self.assertNotEqual(r.get("handler"), "proactive")
        self.assertFalse(proactive.owns("arrival"))
        self.assertFalse(proactive.owns("dwd"))
        proactive.tick(list(house(**NOAH_ON).values()))
        self.assertEqual(self.said, [])


class Handover(Base):
    def test_weather_alerts_step_back_when_owned(self):
        cfg_yaml = YAML.replace("dwd: {enabled: false}", "dwd: {enabled: true}")
        pconf.PATH.write_text(cfg_yaml, encoding="utf-8")
        pconf._CACHE.update(cfg=None, mtime=None)
        with mock.patch.object(weather_alerts, "fetch") as fetch:
            weather_alerts.step()
        fetch.assert_not_called()

    def test_history_keeps_no_work(self):
        proactive._record("arrival", [{"id": "c", "kind": "calendar", "level": "nett",
                                       "text": "Um 9:00 Meeting mit Contoso.", "details": "Contoso"}], "angezeigt", "")
        self.assertEqual(self.rows()[-1]["text"], "(Arbeitstermin – nicht gespeichert)")

    def test_status_for_the_dashboard(self):
        proactive.instant(self.cfg(), house(**NOAH_ON), DAY)
        s = proactive.status()
        self.assertTrue(s["enabled"])
        self.assertEqual(s["history"][0]["kind"], "noah")
        self.assertTrue(s["sources"]["noah"])
        self.assertFalse(s["sources"]["weather"])


if __name__ == "__main__":
    unittest.main()
