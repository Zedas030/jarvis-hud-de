"""
Router, timers, lists and routines — without Home Assistant, Claude or a mic.

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

Home Assistant is faked (a few devices, every switch call is recorded), state
and vault live in a temp folder, so the tests never touch the real house or
the real state.json.
"""
import datetime as dt
import json
import os
import pathlib
import sys
import time
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402  (must come before any Jarvis module)

import commands       # noqa: E402
import events         # noqa: E402
import fallback_llm   # noqa: E402
import jarvis_mcp     # noqa: E402
import lists          # noqa: E402
import router         # noqa: E402
import routines       # noqa: E402
import scheduler      # noqa: E402
import briefing       # noqa: E402

CALLS = []
DEVICES = [("light.wohnzimmer", "Wohnzimmer"), ("light.kueche", "Küche"), ("switch.router", "Router"),
           ("switch.ventilator", "Ventilator"), ("switch.kuehlschrank", "Kühlschrank"),
           ("light.led_wohnzimmer", "LED Wohnzimmer"),                       # a real lamp (e.g. Tuya)
           ("switch.tv_led", "TV LED"),                                      # status LED of a socket
           ("switch.ledwohnzimmer_internet_access", "ledwohnzimmer Internetzugang"),   # FRITZ!Box
           ("switch.fritz_box_wifi_main", "FRITZ!Box 6660 Cable Wi-Fi Main 5Ghz"),
           ("light.led_pc", "LED PC"), ("switch.pc", "PC")]                  # a lamp at the PC vs. the PC socket
STATES = {"light.wohnzimmer": "on", "light.kueche": "off", "switch.router": "on", "switch.ventilator": "off",
          "switch.kuehlschrank": "on", "light.led_wohnzimmer": "on", "switch.tv_led": "on",
          "switch.ledwohnzimmer_internet_access": "on", "switch.fritz_box_wifi_main": "on",
          "light.led_pc": "on", "switch.pc": "on"}


def fake_home(name, args):
    CALLS.append((args["entity_id"], args["service"], args.get("data") or {}))
    return f"Erledigt: {args['service']} für {args['entity_id']}."


def fake_ha(path, body=None):
    CALLS.append((body.get("entity_id"), path.rsplit("/", 1)[-1], {}))
    return []


jarvis_mcp._home = fake_home
jarvis_mcp._ha = fake_ha
briefing._ha_states = lambda: [{"entity_id": e, "state": STATES[e], "attributes": {"friendly_name": n}}
                               for e, n in DEVICES]
router._DEV["at"] = 0
router._devices()                                  # the real filter runs over the fake house once
router._DEV["at"] = time.time() + 10 ** 9

# The PC is never really touched: volume, apps and the lock screen are recorded instead.
import answer_cache   # noqa: E402
import commute        # noqa: E402
import learning       # noqa: E402
import parcels        # noqa: E402
import pc             # noqa: E402
import pronounce      # noqa: E402

PC = []
pc.set_volume = lambda p: PC.append(("volume", p)) or True
pc.step = lambda d: PC.append(("step", d)) or 50 + d
pc.mute = lambda on=True: PC.append(("mute", on)) or True
pc.lock = lambda: PC.append(("lock",)) or True
pc.open_app = lambda name: PC.append(("open", name.lower())) or name
pc.volume = lambda: 42
learning.USAGE = TMP / "usage.jsonl"


def route(text):
    return router.route(text)


class Base(unittest.TestCase):
    def setUp(self):
        router.forget_topic()
        CALLS.clear()
        PC.clear()
        learning.PATH.unlink(missing_ok=True)
        learning._CACHE["mtime"] = None
        commands.update_state(lambda d: d.update(timers=[], notified={}))
        for p in (TMP / "vault").rglob("*.md"):
            p.unlink()
        routines.PATH.unlink(missing_ok=True)
        routines._CACHE["mtime"] = None

    def local(self, text, handler=None):
        r = route(text)
        self.assertEqual(r["kind"], "local", f"{text!r} ging ans LLM: {r}")
        if handler:
            self.assertEqual(r["handler"], handler, text)
        return r["reply"]

    def llm(self, text, *groups):
        r = route(text)
        self.assertEqual(r["kind"], "llm", f"{text!r} wurde lokal erledigt: {r.get('reply')}")
        for g in groups:
            self.assertIn(g, r["groups"], text)
        return r


class HomeRules(Base):
    def test_switch_on(self):
        self.local("Mach das Licht im Wohnzimmer an", "home")
        self.assertEqual(CALLS, [("light.wohnzimmer", "turn_on", {})])

    def test_compound_room_device(self):
        # "Schlafzimmerlicht aus" went to Claude every time: the compound matched no device name
        self.local("Wohnzimmerlicht aus.", "home")
        self.local("Küchenlicht an", "home")
        self.assertEqual(CALLS, [("light.wohnzimmer", "turn_off", {}), ("light.kueche", "turn_on", {})])

    def test_compound_split_is_narrow(self):
        self.assertEqual(router._norm("Schlafzimmerlicht aus"), " schlafzimmer licht aus ")
        for word in ("tageslicht", "leselampe", "zimmerlicht"):
            self.assertEqual(router._norm(word).strip(), word)

    def test_dim(self):
        self.local("Licht Wohnzimmer auf 40 Prozent", "home")
        self.assertEqual(CALLS, [("light.wohnzimmer", "turn_on", {"brightness_pct": 40})])

    def test_question_never_switches(self):
        for text in ("Ist das Licht im Wohnzimmer an?", "Wohnzimmer an?", "Ist der Ventilator aus?"):
            route(text)
        self.assertEqual(CALLS, [], "eine Frage hat geschaltet")

    def test_state_answer(self):
        self.assertIn("an", self.local("Ist das Licht im Wohnzimmer an?", "home_state"))

    def test_led_lamp_is_a_lamp(self):
        self.local("LED Wohnzimmer aus", "home")
        self.assertEqual(CALLS, [("light.led_wohnzimmer", "turn_off", {})])

    def test_indicator_leds_and_internet_switches_are_never_targets(self):
        eids = {d["eid"] for d in router._devices()}
        self.assertNotIn("switch.tv_led", eids)
        self.assertNotIn("switch.ledwohnzimmer_internet_access", eids)
        route("Schalte TV LED aus")
        route("ledwohnzimmer Internetzugang aus")
        self.assertFalse([c for c in CALLS if c[0].startswith("switch.")])

    def test_all_lights(self):
        self.local("Mach alle Lichter aus", "home")
        self.assertEqual(sorted(c[0] for c in CALLS),
                         ["light.kueche", "light.led_pc", "light.led_wohnzimmer", "light.wohnzimmer"])
        self.assertTrue(all(c[1] == "turn_off" for c in CALLS))

    def test_lamp_named_pc_is_not_the_pc(self):
        self.local("LED PC aus", "home")
        self.assertEqual(CALLS, [("light.led_pc", "turn_off", {})])
        CALLS.clear()
        self.llm("PC aus")                               # the PC socket stays protected
        self.assertEqual(CALLS, [])

    def test_protected_devices_go_to_llm(self):
        self.llm("Schalte den Router aus")
        self.llm("Mach den Kühlschrank aus")
        self.llm("Fritz Box aus")
        self.llm("Schalte das WiFi Main aus")
        self.assertEqual(CALLS, [])

    def test_device_aliases(self):
        # The living-room lamp HA calls just "LED", next to a dead lamp; the user says Licht, Lampe, Steckdose.
        house = [("light.licht", "LED", "on"), ("light.lampe_wohnzimmer", "Tot", "unavailable"),
                 ("light.kueche", "Küche", "off"), ("switch.pc", "PC", "on")]
        states, aliases = briefing._ha_states, router._aliases
        briefing._ha_states = lambda: [{"entity_id": e, "state": s, "attributes": {"friendly_name": n}}
                                       for e, n, s in house]
        router._aliases = lambda: {"light.licht": ["Wohnzimmer", "Licht", "Lampe", "Steckdose"]}
        router._DEV["at"] = 0
        try:
            self.assertNotIn("light.lampe_wohnzimmer", {d["eid"] for d in router._devices()})
            for text in ("Wohnzimmer, Licht aus", "Wohnzimmer aus", "Licht aus", "Lampe an",
                         "Mach die Steckdose im Wohnzimmer aus", "LED an"):
                CALLS.clear()
                self.local(text, "home")
                self.assertEqual([c[0] for c in CALLS], ["light.licht"], text)
            CALLS.clear()
            self.local("Licht Küche an", "home")             # a real name beats the generic alias word
            self.assertEqual(CALLS, [("light.kueche", "turn_on", {})])
            CALLS.clear()
            self.local("Mach alle Lichter aus", "home")      # the dead lamp is left out
            self.assertEqual(sorted(c[0] for c in CALLS), ["light.kueche", "light.licht"])
            self.assertIn("an", self.local("Ist das Licht im Wohnzimmer an?", "home_state"))
        finally:
            briefing._ha_states, router._aliases = states, aliases
            router._DEV["at"] = 0
            router._devices()
            router._DEV["at"] = time.time() + 10 ** 9

    def test_conditions_are_not_switched_now(self):
        self.llm("Wenn ich Gute Nacht sage, mach alle Lichter aus", "routines", "home")
        self.assertEqual(CALLS, [])

    def test_delayed_switch_is_scheduled_not_done(self):
        reply = self.local("Mach das Licht im Wohnzimmer in 10 Minuten aus", "timers")
        self.assertEqual(CALLS, [])
        (item,) = scheduler.items()
        self.assertEqual(item["kind"], "action")
        self.assertAlmostEqual(item["at"] - time.time(), 600, delta=5)
        self.assertIn("aus", reply)
        self.assertEqual(router.run_local(item["text"]) is not None, True)
        self.assertEqual(CALLS, [("light.wohnzimmer", "turn_off", {})])


class Timers(Base):
    def test_timer(self):
        self.assertIn("10 Minuten", self.local("Timer 10 Minuten", "timers"))
        (item,) = scheduler.items()
        self.assertEqual(item["kind"], "timer")
        self.assertAlmostEqual(item["at"] - time.time(), 600, delta=3)

    def test_timer_words_and_label(self):
        reply = self.local("Stell einen Timer auf eine halbe Stunde für die Nudeln")
        (item,) = scheduler.items()
        self.assertAlmostEqual(item["at"] - time.time(), 1800, delta=3)
        self.assertEqual(item["text"], "Nudeln")
        self.assertIn("Nudeln", reply)

    def test_timer_compound_number(self):
        self.local("Timer auf fünfundzwanzig Minuten")
        self.assertAlmostEqual(scheduler.items()[0]["at"] - time.time(), 1500, delta=3)

    def test_alarm(self):
        self.local("Weck mich morgen um halb sieben", "timers")
        (item,) = scheduler.items()
        when = dt.datetime.fromtimestamp(item["at"])
        self.assertEqual((when.date() - dt.date.today()).days, 1)
        self.assertEqual((when.hour, when.minute), (6, 30))

    def test_reminder_relative(self):
        reply = self.local("Erinnere mich in 20 Minuten an die Wäsche", "timers")
        (item,) = scheduler.items()
        self.assertEqual(item["text"], "die Wäsche")
        self.assertAlmostEqual(item["at"] - time.time(), 1200, delta=3)
        self.assertIn("20 Minuten", reply)

    def test_reminder_absolute(self):
        self.local("Erinnere mich morgen um 9 daran, Anna anzurufen")
        (item,) = scheduler.items()
        when = dt.datetime.fromtimestamp(item["at"])
        self.assertEqual((when.hour, when.minute), (9, 0))
        self.assertEqual(item["text"], "Anna anzurufen")

    def test_reminder_without_time_goes_to_llm(self):
        self.llm("Erinnere mich an die Steuererklärung", "timers")

    def test_list_and_cancel(self):
        self.local("Timer 5 Minuten")
        self.assertIn("noch", self.local("Welche Timer laufen?"))
        self.assertIn("gelöscht", self.local("Timer stoppen"))
        self.assertEqual(scheduler.items(), [])
        self.assertIn("kein", self.local("Lösch den Wecker"))

    def test_snooze(self):
        self.assertIn("9 Minuten", self.local("Schlummern"))
        self.assertEqual(scheduler.items()[0]["kind"], "alarm")

    def test_ringing(self):
        scheduler.add("timer", time.time() - 1, "Pizza", 300)
        before = events._LOG[-1]["id"] if events._LOG else 0
        scheduler._due(time.time())
        rung = [e for e in events._LOG if e["id"] > before and e["t"] == "alarm"]
        self.assertEqual(len(rung), 1)
        self.assertIn("Pizza", rung[0]["text"])
        self.assertEqual(scheduler.items(), [])


class TimeParsing(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 14, 14, 0)        # a Wednesday afternoon

    def at(self, text, kind="alarm"):
        return scheduler.resolve(scheduler.numify(router._norm(text)), kind, self.NOW)

    def test_clock_words(self):
        self.assertEqual(self.at("um viertel vor acht"), dt.datetime(2026, 10, 15, 7, 45))
        self.assertEqual(self.at("um halb acht abends"), dt.datetime(2026, 10, 14, 19, 30))
        self.assertEqual(self.at("um 7:15"), dt.datetime(2026, 10, 15, 7, 15))
        self.assertEqual(self.at("um 16 Uhr 30"), dt.datetime(2026, 10, 14, 16, 30))

    def test_reminder_means_the_coming_six(self):
        self.assertEqual(self.at("um 6", "reminder"), dt.datetime(2026, 10, 14, 18, 0))
        self.assertEqual(self.at("um 6", "alarm"), dt.datetime(2026, 10, 15, 6, 0))

    def test_weekday(self):
        self.assertEqual(self.at("am Freitag um 9"), dt.datetime(2026, 10, 16, 9, 0))
        self.assertEqual(self.at("am Mittwoch um 9"), dt.datetime(2026, 10, 21, 9, 0))

    def test_minutes_are_not_a_clock(self):
        self.assertIsNone(scheduler.clock(scheduler.numify(router._norm("Timer auf 10 Minuten"))))

    def test_durations(self):
        d = lambda s: scheduler.duration(scheduler.numify(router._norm(s)))  # noqa: E731
        self.assertEqual(d("eine Stunde und 30 Minuten"), 5400)
        self.assertEqual(d("anderthalb Stunden"), 5400)
        self.assertEqual(d("90 Sekunden"), 90)
        self.assertEqual(d("eine Viertelstunde"), 900)

    def test_spoken(self):
        self.assertEqual(scheduler.fmt_dur(90), "eine Minute und 30 Sekunden")
        self.assertEqual(scheduler.fmt_dur(7200), "2 Stunden")


class Lists(Base):
    def test_add_read_remove(self):
        self.assertIn("Milch und Eier", self.local("Setz Milch und Eier auf die Einkaufsliste", "lists"))
        self.assertIn("Butter", self.local("Füge Butter zur Einkaufsliste hinzu"))
        self.assertEqual(self.local("Was steht auf der Einkaufsliste?"),
                         "Auf der Einkaufsliste: Milch, Eier und Butter.")
        self.assertIn("gestrichen", self.local("Streich die Milch von der Einkaufsliste"))
        self.assertEqual(lists.entries("einkauf"), ["Eier", "Butter"])

    def test_no_duplicates(self):
        self.local("Milch auf die Einkaufsliste")
        self.assertIn("schon", self.local("Setz Milch auf die Einkaufsliste"))

    def test_todo_and_clear(self):
        self.local("Schreib Steuererklärung auf meine To-do-Liste")
        self.assertEqual(lists.entries("todo"), ["Steuererklärung"])
        self.assertEqual(lists.entries("einkauf"), [])
        self.assertIn("geleert", self.local("Lösch die To-do-Liste"))
        self.assertEqual(lists.entries("todo"), [])

    def test_note_is_a_checklist(self):
        self.local("Milch auf die Einkaufsliste")
        self.assertIn("- [ ] Milch", (lists.LIST_DIR / "Einkaufsliste.md").read_text(encoding="utf-8"))


class Routines(Base):
    def save(self, data):
        routines.PATH.write_text(json.dumps(data), encoding="utf-8")
        routines._CACHE["mtime"] = None

    def test_trigger_runs_steps(self):
        self.save([{"name": "Gute Nacht", "triggers": ["gute nacht"],
                    "steps": [{"do": "Licht Wohnzimmer aus"}, {"say": "Schlafen Sie gut."}]}])
        self.assertEqual(self.local("Gute Nacht, Jarvis", "routine"), "Schlafen Sie gut.")
        self.assertEqual(CALLS, [("light.wohnzimmer", "turn_off", {})])

    def test_only_exact_phrase(self):
        self.save([{"name": "Gute Nacht", "triggers": ["gute nacht"], "steps": [{"say": "x"}]}])
        self.assertNotEqual(route("Ich wünsche Anna eine gute Nacht").get("handler"), "routine")

    def test_tell_and_failures(self):
        self.save([{"name": "Test", "triggers": ["test routine"],
                    "steps": [{"tell": "Wie spät ist es?"}, {"do": "Erklär mir die Welt"}]}])
        reply = self.local("test routine")
        self.assertIn("Uhr", reply)
        self.assertIn("Nicht geklappt", reply)

    def test_validation(self):
        self.assertTrue(routines.validate([{"ha": "switch.turn_off", "entity_id": "all"}]))
        self.assertTrue(routines.validate([{"ha": "switch.turn_off", "entity_id": "switch.router"}]))
        self.assertTrue(routines.validate([{"ha": "lock.unlock", "entity_id": "lock.door"}]))
        self.assertEqual(routines.validate([{"ha": "light.turn_off", "entity_id": "all"}]), "")

    def test_tool_save(self):
        res = routines.tool("routine_save", {"name": "Kino", "triggers": ["kinoabend"],
                                             "steps": [{"ha": "light.turn_on", "entity_id": "light.kueche",
                                                        "data": {"brightness_pct": 10}}]})
        self.assertIn("gespeichert", res)
        self.local("Kinoabend")
        self.assertEqual(CALLS, [("light.kueche", "turn_on", {})])


class Misc(Base):
    def test_time(self):
        self.assertIn("Uhr", self.local("Wie spät ist es?", "time"))

    def test_smart_question(self):
        r = self.llm("Erklär mir, warum der Himmel blau ist")
        self.assertEqual(r["model"], router.MODEL_SMART)

    def test_quiet_hours(self):
        self.assertTrue(events.quiet_now(dt.datetime(2026, 1, 1, 23)))
        self.assertTrue(events.quiet_now(dt.datetime(2026, 1, 1, 6)))
        self.assertFalse(events.quiet_now(dt.datetime(2026, 1, 1, 12)))

    def test_claude_blocked(self):
        self.assertTrue(fallback_llm.claude_blocked({"status": "rejected", "resets": time.time() + 600}))
        self.assertFalse(fallback_llm.claude_blocked({"status": "rejected", "resets": time.time() - 600}))
        self.assertFalse(fallback_llm.claude_blocked({"status": "allowed"}))


class PcControl(Base):
    def test_volume(self):
        self.assertIn("30", self.local("Lautstärke auf 30 Prozent", "pc"))
        self.local("PC lauter")
        self.local("Ton aus")
        self.local("Stumm aus")
        self.assertEqual(PC, [("volume", 30), ("step", 10), ("mute", True), ("mute", False)])

    def test_volume_misheard(self):
        # Heard like this at 11:54: no handler matched, and Jarvis said his volume tool was missing.
        self.assertIn("100", self.local("stellt die Lautspecke auf 100%.", "pc"))
        self.assertEqual(PC, [("volume", 100)])

    def test_follow_up_keeps_the_last_tools(self):
        # "Also was." after a mail question used to get memory only — "mir fehlt das Mail-Werkzeug".
        self.assertIn("mail", route("Was steht in meinen Mails?")["groups"])
        follow = route("Dann nochmal alles vor, aber du hast ja schon das meiste gesagt.")
        self.assertIn("mail", follow["groups"])
        self.assertEqual(follow["reason"], "Folgefrage")
        self.assertIn("mail", route("Also was.")["groups"], "a chain of vague turns keeps the topic")
        self.assertNotIn("mail", route("Wie wird das Wetter morgen in Hamburg?").get("groups", []),
                         "a new topic brings its own tools only")

    def test_follow_up_with_own_topic_keeps_the_full_ha_server(self):
        # 13:12: "ab wie viel Prozent ..." brought "home" itself and lost ha_full from the automation question.
        self.assertIn("ha_full", router.route("Zeig mir die Stromautomatisierung im Home Assistant", ha_full=True)["groups"])
        r = route("Ich will wissen, ab wie viel Prozent er aufhört, aus der Batterie Strom zu ziehen.")
        self.assertIn("home", r["groups"])
        self.assertIn("ha_full", r["groups"])

    def test_joined_groups_do_not_pile_up(self):
        route("Was steht in meinen Mails?")
        route("Recherchier mir bitte gründlich, welche Golfplätze es in Brandenburg gibt")   # takes mail along, only itself
        self.assertNotIn("mail", route("Erklär mir, warum die Lampe im Wohnzimmer manchmal flackert")["groups"])

    def test_follow_up_carry_expires(self):
        route("Was steht in meinen Mails?")
        router._LAST["at"] -= router.CARRY_S + 1
        self.assertEqual(route("Also was.")["groups"], ["memory", "web"], "only the search that rides along")

    def test_web_search_rides_along_without_the_big_model(self):
        # "bitte finde selber heraus, welches Course Rating der Platz hat" got memory only and no search.
        r = route("Wie lange hat das Freibad in Pankow heute offen?")
        self.assertIn("web", r["groups"])
        r = route("ich habe ein 48er Handicap und bitte finde selber heraus, welches Course Rating der Platz hat.")
        self.assertIn("web", r["groups"])
        self.assertEqual(r["model"], router.MODEL_SMART, "an explicit search request gets the bigger model")
        router.forget_topic()
        self.assertEqual(route("Sag mir einen netten Spruch").get("model"), router.MODEL_SIMPLE,
                         "the search riding along does not make every turn expensive")

    def test_web_search_is_no_topic_for_follow_ups(self):
        route("Was steht in meinen Mails?")
        self.assertIn("mail", route("Also was.")["groups"], "riding-along web must not end the follow-up carry")

    def test_volume_number_first(self):
        # Heard like this: the number before "Lautstärke" must set, not read the volume out.
        self.assertIn("100", self.local("stelle mein PC auf 100% Lautstärke.", "pc"))
        self.assertIn("100", self.local("Ein PC Jarvis auf 100% Lautstärke.", "pc"))
        self.assertIn("40", self.local("Stell den Rechner auf 40 Prozent", "pc"))
        self.assertEqual(PC, [("volume", 100), ("volume", 100), ("volume", 40)])

    def test_music_volume_stays_with_music(self):
        r = route("Musik leiser")
        self.assertNotEqual(r.get("handler"), "pc")

    def test_apps_and_lock(self):
        self.local("Öffne Spotify", "pc")
        self.local("Sperr den Bildschirm", "pc")
        self.assertEqual(PC, [("open", "spotify"), ("lock",)])

    def test_unknown_app_and_light_are_not_pc(self):
        self.assertNotEqual(route("Öffne die Steuererklärung von 2019").get("handler"), "pc")
        self.local("Mach das Licht im Wohnzimmer an", "home")
        self.assertEqual(PC, [])


class Learning(Base):
    def test_learns_a_switch_and_does_it_alone_next_time(self):
        self.llm("Mach die Leselampe aus")              # unknown name: the LLM has to find it
        calls = [("mcp__jarvis__home_states", {"search": "lese"}),
                 ("mcp__jarvis__home_control", {"entity_id": "light.kueche", "service": "turn_off"})]
        learned = learning.observe("Mach die Leselampe aus", calls)
        self.assertEqual(learned, "leselampe")
        self.llm("Mach die Leselampe aus")              # only a draft: still the LLM
        import skill_factory
        skill_factory._ha_entities = lambda: {"light.kueche"}
        self.assertIn("Aktiv", skill_factory.activate("leselampe"))   # what the card's yes does
        self.local("Mach die Leselampe aus", "home")
        self.assertEqual(CALLS, [("light.kueche", "turn_off", {})])
        self.local("Leselampe an")
        self.assertEqual(CALLS[-1], ("light.kueche", "turn_on", {}))

    def test_does_not_learn_questions_or_values(self):
        self.assertIsNone(learning.observe("Ist die Leselampe an?",
                                           [("home_control", {"entity_id": "light.x", "service": "turn_on"})]))
        self.assertIsNone(learning.observe("Leselampe auf 30 Prozent",
                                           [("home_control", {"entity_id": "light.x", "service": "turn_on",
                                                              "data": {"brightness_pct": 30}})]))
        self.assertIsNone(learning.observe("Mach die Leselampe aus", [("web_fetch", {"url": "x"})]))

    def test_learned_protected_device_still_protected(self):
        learning.observe("Mach den Kasten aus", [("home_control", {"entity_id": "switch.router",
                                                                  "service": "turn_off"})])
        import skill_factory
        skill_factory._ha_entities = lambda: {"switch.router"}
        skill_factory.activate("kasten")
        self.llm("Mach den Kasten aus")
        self.assertEqual(CALLS, [])

    def test_report(self):
        rows = [dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), route="llm:simple", message="Hab ich Mails?",
                     total_in=9000, output=100)] * 3 + [dict(at=time.strftime("%Y-%m-%d %H:%M:%S"),
                                                             route="local:home", message="Licht an")]
        learning.USAGE.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        reply = self.local("Verbrauchsbericht", "report")
        self.assertIn("25 Prozent", reply)
        self.assertIn("hab ich mails", reply)


class Sun(Base):
    def setUp(self):
        super().setUp()
        router._WX.update(at=time.time(), d={"daily": {
            "sunrise": ["2026-10-15T07:32", "2026-10-16T07:34", "2026-10-17T07:36"],
            "sunset": ["2026-10-15T18:12", "2026-10-16T18:10", "2026-10-17T18:08"]}})

    def tearDown(self):
        router._WX.clear()
        super().tearDown()

    def test_sunset_today(self):
        self.assertIn("18:12", self.local("Wann geht die Sonne unter?", "sun"))

    def test_sunrise_tomorrow(self):
        reply = self.local("Wann ist morgen Sonnenaufgang?", "sun")
        self.assertIn("07:34", reply)
        self.assertNotIn("18:10", reply)

    def test_both_with_daylight(self):
        reply = self.local("Wie lange ist es übermorgen hell?", "sun")
        self.assertIn("07:36", reply)
        self.assertIn("10 Stunden 32 Minuten", reply)

    def test_rest_goes_to_weather_tool(self):
        for text in ("Wie hoch ist der UV-Index?", "Scheint morgen die Sonne?", "Wie viele Sonnenstunden heute?",
                     "Wie wird das Wetter am Wochenende?", "Wie warm wird es in Madrid?"):
            self.llm(text, "weather")
            # the search that rides along in every turn is fine; no search *request* though
            self.assertNotIn("web", router.llm_route(text)["groups"], text)

    def test_no_weather_for_unrelated(self):
        for text in ("Starte Windows neu", "Mach das Licht heller", "Wie ist die Temperatur im Wohnzimmer?"):
            self.assertNotIn("weather", router.llm_route(text)["groups"], text)


class MailRoute(Base):
    def test_mark_read_goes_to_mail(self):
        self.llm("Markier alle Mails als gelesen", "mail")
        self.llm("Markier die von Amazon als gelesen", "mail")


class CalendarRoute(Base):
    def test_day_plan_questions_get_the_calendar(self):
        # "was steht heute noch an?" got memory only, and Jarvis said he could not see the calendar
        for text in ("was steht heute noch an?", "Was hab ich heute vor?", "Habe ich heute noch was vor?",
                     "Was steht diese Woche an", "Hab ich am Samstag Zeit?"):
            self.llm(text, "calendar")

    def test_switches_stay_with_the_house(self):
        self.assertNotIn("calendar", route("Steht der Fernseher an?").get("groups", []))


class Cache(unittest.TestCase):
    def setUp(self):
        answer_cache.clear()

    def test_read_only_is_cached(self):
        answer_cache.put("Hab ich neue Mails?", ["memory", "mail"], "Zwei, Sir.")
        self.assertEqual(answer_cache.get("hab ich neue mails", ["mail", "memory"]), "Zwei, Sir.")

    def test_changes_and_saves_are_not(self):
        answer_cache.put("Schalt das Licht an", ["memory", "home"], "x")
        answer_cache.put("Merk dir, dass Anna Tee mag", ["memory"], "Notiert.")
        answer_cache.put("Was weißt du über Anna?", ["memory"], "y", tools_used=["mcp__jarvis__memory_save"])
        self.assertIsNone(answer_cache.get("Schalt das Licht an", ["memory", "home"]))
        self.assertIsNone(answer_cache.get("Merk dir, dass Anna Tee mag", ["memory"]))
        self.assertIsNone(answer_cache.get("Was weißt du über Anna?", ["memory"]))

    def test_marking_mails_read_empties_cache(self):
        answer_cache.put("Hab ich neue Mails?", ["mail"], "Zwei, Sir.")
        answer_cache.put("Markier die Mails von Amazon als gelesen", ["mail"], "Erledigt.")
        self.assertEqual(answer_cache.get("Hab ich neue Mails?", ["mail"]), "Zwei, Sir.")
        answer_cache.put("Die von Amazon sind gelesen", ["mail"], "Erledigt.",
                         tools_used=["mcp__jarvis__mail_mark_read"])
        self.assertIsNone(answer_cache.get("Hab ich neue Mails?", ["mail"]))
        self.assertIsNone(answer_cache.get("Markier die Mails von Amazon als gelesen", ["mail"]))

    def test_expires(self):
        answer_cache.put("Wie geht es dem NAS?", ["systems"], "Gut.")
        key = answer_cache._key("Wie geht es dem NAS?", ["systems"])
        answer_cache._DATA[key] = (time.time() - answer_cache.TTL - 1, "Gut.")
        self.assertIsNone(answer_cache.get("Wie geht es dem NAS?", ["systems"]))


class Pronounce(Base):
    def test_defaults_whole_words(self):
        self.assertEqual(pronounce.apply("Das NAS hat 3 kWh."), "Das Nass hat 3 Kilowattstunden.")
        self.assertEqual(pronounce.apply("Pizza und Pi"), "Pizza und Pei")

    def test_learn_by_voice(self):
        self.assertIn("Tuja", self.local("Sprich Tuya als Tuja aus", "pronounce"))
        self.assertEqual(pronounce.apply("Tuya-Lampe"), "Tuja-Lampe")
        pronounce.PATH.unlink()
        pronounce._CACHE["mtime"] = "reset"


class Commute(Base):
    def test_when_to_leave(self):
        start = dt.datetime.now().replace(second=0, microsecond=0) + dt.timedelta(hours=2)
        orig = commute.next_trip
        commute.next_trip = lambda now=None: ({"title": "Zahnarzt", "start": start}, 20,
                                              commute.leave_at(start, 20))
        try:
            reply = self.local("Wann muss ich los?", "commute")
        finally:
            commute.next_trip = orig
        leave = start - dt.timedelta(minutes=20 + commute.BUFFER_MIN)
        self.assertIn("Zahnarzt", reply)
        self.assertIn(f"{leave.hour}:{leave.minute:02d}", reply)

    def test_how_long(self):
        orig = commute.geocode, commute.drive_minutes
        commute.geocode, commute.drive_minutes = (lambda p: ("50.1", "8.6")), (lambda d: 17)
        try:
            self.assertIn("17 Minuten", self.local("Wie lange brauche ich zum Hauptbahnhof?", "commute"))
        finally:
            commute.geocode, commute.drive_minutes = orig


class Parcels(Base):
    def setUp(self):
        super().setUp()
        now = time.time()
        self.items = [
            dict(sender="DHL", subject="Ihr Paket kommt heute", status="heute", when=now, id="1"),   # now: always today, even just after midnight
            dict(sender="Amazon", subject="Versandt: Kabel", status="unterwegs", when=now - 86400, id="2"),
            dict(sender="Hermes", subject="Zugestellt", status="zugestellt", when=now - 5 * 86400, id="3"),
        ]
        self.orig = parcels.shipments
        parcels.shipments = lambda days=14, ttl=600: self.items

    def tearDown(self):
        parcels.shipments = self.orig

    def test_classify(self):
        self.assertEqual(parcels.classify("Ihre Sendung ist in Zustellung"), "heute")
        self.assertEqual(parcels.classify("Paket liegt in der Packstation bereit"), "abholbereit")
        self.assertEqual(parcels.classify("Ihr Paket wurde zugestellt"), "zugestellt")
        self.assertEqual(parcels.classify("Bestellung versandt"), "unterwegs")
        self.assertIsNone(parcels.classify("Newsletter Oktober"))

    def test_questions(self):
        self.assertEqual(self.local("Kommt heute ein Paket?", "parcels"), "Pakete, heute in Zustellung: DHL.")
        reply = self.local("Welche Pakete sind unterwegs?", "parcels")
        self.assertIn("Amazon", reply)
        self.assertNotIn("Hermes", reply)               # delivered five days ago: old news

    def test_other_meanings_of_paket(self):
        self.assertNotEqual(route("Installier das Paket auf dem Pi").get("handler"), "parcels")


if __name__ == "__main__":
    unittest.main()


class MusicRooms(Base):
    """The music into another room goes to the music tool as a transfer, without the LLM."""

    def setUp(self):
        super().setUp()
        import speakers
        self.moved = []
        self.patches = [unittest.mock.patch.object(speakers, "rooms", return_value=["Schlafzimmer", "Wohnzimmer"]),
                        unittest.mock.patch.object(jarvis_mcp, "_music_room",
                                                   side_effect=lambda room: self.moved.append(room) or f"Die Musik läuft jetzt im {room}.")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_target_is_the_room_not_said_as_source(self):
        self.assertIn("Wohnzimmer", self.local("Mach die Musik aufm Schlafzimmer im Wohnzimmer an", "music"))
        self.local("Schieb die Musik vom Schlafzimmer ins Wohnzimmer", "music")
        self.local("Mach im Schlafzimmer die Musik aus dem Wohnzimmer an", "music")
        self.local("Musik ins Schlafzimmer", "music")
        self.local("Es läuft im Schlafzimmer Musik und ich sitze im Wohnzimmer", "music")
        self.local("Die Musik spielt noch im Wohnzimmer, hol sie ins Schlafzimmer", "music")
        self.local("Ich bin im Wohnzimmer, die Musik läuft im Schlafzimmer", "music")
        self.assertEqual(self.moved, ["Wohnzimmer", "Wohnzimmer", "Schlafzimmer", "Schlafzimmer",
                                      "Wohnzimmer", "Schlafzimmer", "Wohnzimmer"])

    def test_plain_music_commands_and_lights_stay_as_they_were(self):
        route("Musik leiser")
        self.assertEqual(self.moved, [])
        self.assertNotEqual(route("Mach das Licht im Wohnzimmer an").get("handler"), "music")
        self.llm("Spiel die Playlist Chill im Wohnzimmer", "music")
        self.assertEqual(self.moved, [])
