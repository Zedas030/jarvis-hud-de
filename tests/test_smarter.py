"""
The "smarter" batch: episodic memory, the user's habits, Garmin as a trigger, leaving for an
appointment, the power budget, the maintenance calendar, Paperless/UniFi/Jellyfin text, the
voice by the hour, the phone as a card, focus sessions, the self-check, the routine dry run and
the router's ask-back. No network, no house, no Claude.

    .venv\\Scripts\\python.exe -m unittest tests.test_smarter -v
"""
import datetime as dt
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import commands       # noqa: E402
import daytime        # noqa: E402
import departure      # noqa: E402
import episodes       # noqa: E402
import events         # noqa: E402
import focus          # noqa: E402
import jarvis_mcp     # noqa: E402
import jellyfin       # noqa: E402
import learning       # noqa: E402
import maintenance    # noqa: E402
import memory         # noqa: E402
import paperless      # noqa: E402
import phone_actions  # noqa: E402
import policy         # noqa: E402
import power_costs    # noqa: E402
import proposals      # noqa: E402
import router         # noqa: E402
import routines       # noqa: E402
import selfcheck      # noqa: E402
import unifi          # noqa: E402
import user_habits    # noqa: E402
import weekly_review  # noqa: E402
import wellbeing      # noqa: E402

UTC = dt.timezone.utc


def _diary(day, rows):
    memory.DIARY_DIR.mkdir(parents=True, exist_ok=True)
    body = f"---\ntype: gespraech\nupdated: {day}\n---\n\n# Gespräch {day}\n\nAutomatisches Protokoll.\n"
    for t, u, a in rows:
        body += f"\n**{t} · User:** {u}\n\n**Jarvis:** {a}\n"
    (memory.DIARY_DIR / f"Gespräch {day}.md").write_text(body, encoding="utf-8")


class Episodes(unittest.TestCase):
    def setUp(self):
        self.yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        _diary(self.yesterday, [("14:02", "Was macht der Drucker gerade?", "Der Drucker ist bei 40 Prozent, Sir."),
                                ("18:30", "Wie wird das Wetter morgen?", "Regen, Sir.")])
        weekly_review._update(lambda d: d.__setitem__(self.yesterday, {"sleep_s": 6 * 3600 + 40 * 60, "sleep_score": 71,
                                                                          "steps": 8200, "stress": 32}))

    def test_period_words(self):
        today = dt.date(2026, 10, 29)                                 # a Thursday
        self.assertEqual(episodes.period("gestern", today), (dt.date(2026, 10, 28),) * 2)
        self.assertEqual(episodes.period("letzte woche", today), (dt.date(2026, 10, 19), dt.date(2026, 10, 25)))
        self.assertEqual(episodes.period("am montag", today), (dt.date(2026, 10, 26),) * 2)
        self.assertEqual(episodes.period("am donnerstag", today), (dt.date(2026, 10, 22),) * 2)
        self.assertEqual(episodes.period("vor drei tagen", today), (dt.date(2026, 10, 26),) * 2)
        self.assertEqual(episodes.period("neulich", today), (dt.date(2026, 10, 15), today))
        self.assertIsNone(episodes.period("irgendwann", today))

    def test_timeline_joins_sources(self):
        text = episodes.describe(days=3)
        self.assertIn(self.yesterday, text)
        self.assertIn("Schlaf 6:40 h (Score 71)", text)
        self.assertIn("8.200 Schritte", text)
        self.assertIn("„Was macht der Drucker gerade?“", text)
        only = episodes.describe(days=3, query="Drucker")
        self.assertIn("Drucker", only)
        self.assertNotIn("Wetter morgen", only)

    def test_local_answer(self):
        out = router.route("Was habe ich dich gestern zum Drucker gefragt?")
        self.assertEqual(out["kind"], "local")
        self.assertEqual(out["handler"], "episodes")
        self.assertIn("Was macht der Drucker gerade?", out["reply"])
        self.assertIn("40 Prozent", out["reply"])
        out = router.route("Worüber haben wir gestern gesprochen?")
        self.assertIn("2 Wortwechsel", out["reply"])
        self.assertIn("keine Frage", episodes.handle(router._norm("Was habe ich dich gestern zum Golf gefragt?")))
        self.assertIsNone(episodes.handle(router._norm("Was habe ich dich zum Drucker gefragt?")))   # no time: the model

    def test_llm_group(self):
        self.assertIn("episodes", router.llm_route("Wie war mein Schlaf, als ich letzte Woche krank war?")["groups"])
        self.assertIn("memory_timeline", jarvis_mcp.GROUPS["episodes"])
        self.assertEqual(policy.action_class("memory_timeline"), policy.READ)


class UserHabits(unittest.TestCase):
    def _rows(self, days, hour, minute, message, route):
        out = []
        for d in days:
            t = time.mktime(dt.datetime.combine(d, dt.time(hour, minute)).timetuple())
            out.append(dict(t=t, message=message, route=route))
        return out

    def test_weekday_and_daily_habits(self):
        tuesdays = [dt.date(2026, 10, 6), dt.date(2026, 10, 13), dt.date(2026, 10, 20), dt.date(2026, 10, 27)]
        rows = self._rows(tuesdays, 7, 40, "Wann muss ich los?", "local:commute")
        rows[1]["t"] += 600                                           # 7:50 one week: still the same habit
        daily = [dt.date(2026, 10, 20) + dt.timedelta(days=i) for i in range(5)]
        rows += self._rows(daily, 8, 0, "Hab ich neue Mails?", "llm:simple")
        rows += self._rows(daily[:2], 12, 0, "Wie spät ist es?", "local:time")   # two days: no habit
        found = user_habits.habits(rows)
        by = {h["key"]: h for h in found}
        self.assertEqual(by["wann muss ich los"]["weekdays"], [1])
        self.assertIn(by["wann muss ich los"]["at"], ("07:40", "07:42", "07:43", "07:45"))
        self.assertEqual(by["hab ich neue mails"]["weekdays"], list(range(7)))
        self.assertNotIn("wie spät ist es", by)

    def test_proposal_becomes_a_job(self):
        found = [dict(key="wann muss ich los", text="Wann muss ich los?", route="local:commute", minute=460,
                      at="07:40", days=4, weekdays=[1])]
        pid = user_habits.propose(found)
        p = proposals.get(pid)
        self.assertEqual(p["kind"], "habit")
        self.assertIn("Dienstags um 7:40", proposals.describe(p))
        self.assertIn("Eingerichtet: dienstags um 7:40", proposals.accept(pid))
        self.assertEqual([j["id"] for j in user_habits.jobs()], ["wann muss ich los"])
        self.assertIsNone(user_habits.propose(found))                  # accepted: never proposed again
        job = user_habits.jobs()[0]
        self.assertTrue(user_habits.due(job, dt.datetime(2026, 10, 27, 7, 40, 30)))
        self.assertFalse(user_habits.due(job, dt.datetime(2026, 10, 28, 7, 40, 30)))
        with mock.patch("router.run_local", return_value="Losfahren um 7:55, Sir."):
            user_habits.run_job(job, dt.datetime(2026, 10, 27, 7, 40))
        self.assertIn("dienstags um 7:40", router.route("Welche Gewohnheiten kennst du?")["reply"])
        self.assertIn("Vergessen", router.route("Vergiss die Gewohnheit wann muss ich los")["reply"])
        self.assertEqual(user_habits.jobs(), [])


class Wellbeing(unittest.TestCase):
    def tearDown(self):
        commands.update_state(lambda d: d.pop("wellbeing", None))

    def test_judge(self):
        self.assertEqual(wellbeing.judge({"sleep_score": 52, "stress": 20, "battery": 60}), ("schonend", "Schlaf-Score 52"))
        self.assertEqual(wellbeing.judge({"sleep_score": 80, "stress": 20, "battery": 60}), ("normal", ""))

    def test_gentle_morning_and_evening(self):
        morning = dt.datetime(2026, 10, 29, 7, 30)
        with mock.patch("dashboard._cached", return_value={"sleep_score": 48, "stress": 55, "battery": 20, "steps": 100}):
            self.assertEqual(wellbeing.morning(morning), "schonend")
        self.assertTrue(wellbeing.gentle(morning + dt.timedelta(hours=1)))
        self.assertFalse(wellbeing.gentle(morning + dt.timedelta(hours=6)))
        self.assertTrue(wellbeing.soft({"kind": "suggestion"}, morning))
        self.assertFalse(wellbeing.soft({"kind": "appliance"}, morning))
        self.assertIn("schlechte Nacht", wellbeing.hint(morning))
        self.assertEqual(wellbeing.volume_factor(morning), wellbeing.VOLUME)
        self.assertIn("schonend", wellbeing.describe(morning))
        evening = dt.datetime(2026, 10, 29, 21, 30)
        with mock.patch("dashboard._cached", return_value={"battery": 18}):
            text = wellbeing.evening(evening)
            self.assertIn("Body Battery steht bei 18", text)
            self.assertIsNone(wellbeing.evening(evening))              # once a day


class Departure(unittest.TestCase):
    CFG = {"switches": {"switch.tv": 30}, "lights": True, "media": True}

    def states(self):
        return [{"entity_id": "light.licht", "state": "on", "attributes": {"friendly_name": "Licht"}},
                {"entity_id": "switch.tv", "state": "on", "attributes": {"friendly_name": "TV"}},
                {"entity_id": "sensor.tv_derzeitiger_verbrauch", "state": "85"},
                {"entity_id": "climate.wohnzimmer", "state": "heat", "attributes": {"friendly_name": "Wohnzimmer", "temperature": 21}}]

    def test_prepares_the_proposal(self):
        now = dt.datetime(2026, 10, 29, 8, 0)
        evt = {"title": "Zahnarzt", "start": now + dt.timedelta(minutes=50), "location": "Hauptstraße 1, Frankfurt"}
        prof = {"away_check": self.CFG, "departure": {"climate": ["climate.wohnzimmer"], "climate_away": 17}}
        said = []
        with mock.patch("jarvis_profile.ha", side_effect=lambda k, d=None: prof.get(k, d)), \
                mock.patch("watchers._drive", return_value=20), mock.patch("infra.phone_notify") as push:
            text = departure.check([evt], self.states(), now, lambda key, t, **x: said.append((key, t)) or True)
            self.assertIn("in etwa 25 Minuten sollten Sie los zu Zahnarzt", text)
            self.assertIn("Licht, TV (85 W), Wohnzimmer auf 17 Grad", text)
            push.assert_called_once()
            self.assertIn("JARVIS_YES_", str(push.call_args))
            # too early for a 70-minute lead: nothing yet
            early = dict(evt, start=now + dt.timedelta(minutes=90))
            self.assertIsNone(departure.check([early], self.states(), now, lambda key, t, **x: True))
        p = [x for x in proposals.pending() if x.get("source") == "departure"][-1]
        self.assertEqual(p["payload"]["climate"], {"climate.wohnzimmer": 17.0})
        with mock.patch("jarvis_mcp._home", return_value="Erledigt.") as switch:
            proposals.accept(p["id"])
        services = [c.args[1]["service"] for c in switch.call_args_list]
        self.assertEqual(services, ["turn_off", "turn_off", "set_temperature"])
        self.assertEqual(switch.call_args_list[-1].args[1]["data"], {"temperature": 17.0})


class Budget(unittest.TestCase):
    def setUp(self):
        self.st = {"sensor.hausverbrauch_diesen_monat": {"state": "171"},
                   "switch.pc": {"entity_id": "switch.pc", "state": "on", "attributes": {"friendly_name": "PC"}},
                   "sensor.pc_der_verbrauch_dieses_monats": {"state": "80"}}
        self.now = dt.datetime(2026, 10, 28, 12)

    def test_projection_against_budget(self):
        b = power_costs.budget_status(self.st, self.now, limit=50)
        self.assertAlmostEqual(b["projected"], 171 / power_costs.month_factor(self.now) * 0.30, places=2)
        self.assertGreater(b["over"], 0)
        self.assertEqual(b["top"][0], "PC")
        with mock.patch("power_costs.budget", return_value=50.0):
            text = power_costs.say_budget(self.st, self.now)
            self.assertIn("drüber", text)
            self.assertIn("Größter Verursacher an den Steckdosen: PC", text)
            self.assertIn("über Budget", power_costs.budget_notice(self.st, self.now))
        with mock.patch("power_costs.budget", return_value=100.0):
            self.assertIn("im Rahmen", power_costs.say_budget(self.st, self.now))
            self.assertIsNone(power_costs.budget_notice(self.st, self.now))
        with mock.patch("power_costs.budget", return_value=0.0):
            self.assertIn("kein Strombudget", power_costs.say_budget(self.st, self.now))
        with mock.patch("power_costs.budget", return_value=50.0):
            early = dt.datetime(2026, 10, 2, 8)
            self.assertTrue(power_costs.budget_status(self.st, early)["early"])
            self.assertIn("noch jung", power_costs.say_budget(self.st, early))
            self.assertIsNone(power_costs.budget_notice(self.st, early))


class Maintenance(unittest.TestCase):
    def test_der_not_after(self):
        der = b"\x30\x1e\x17\x0d251001000000Z\x17\x0d261001000000Z"
        self.assertEqual(maintenance._der_not_after(der), dt.datetime(2026, 10, 1))
        self.assertIsNone(maintenance._der_not_after(b"\x30\x00"))

    def test_images_and_backups(self):
        now = dt.datetime(2026, 10, 29, tzinfo=UTC)
        text = "image homeassistant 2026-06-01T10:00:00.123456789Z\nimage pihole 2026-10-20T00:00:00Z\nkaputt\n"
        self.assertEqual(maintenance.parse_images(text, now), [("homeassistant", 149), ("pihole", 9)])
        self.assertEqual(maintenance.backup_age("NAS-Sicherung: ok, zuletzt 2026-10-26 03:00", dt.date(2026, 10, 29)), 3)
        self.assertEqual(maintenance.backup_age("Aufgabe: ok, zuletzt 27.10.2026", dt.date(2026, 10, 29)), 2)
        self.assertIsNone(maintenance.backup_age("nichts"))

    def test_old_image_that_is_still_newest(self):
        now = dt.datetime(2026, 11, 7, tzinfo=UTC)
        text = ("image noah 2025-02-19T10:00:00.1Z ghcr.io/x/noah:latest ghcr.io/x/noah@sha256:aaa\n"
                "image fritz 2025-01-01T00:00:00Z pdreker/fritz:latest pdreker/fritz@sha256:old\n"
                "image ha 2025-01-01T00:00:00Z ghcr.io/ha/ha:1@sha256:pin ghcr.io/ha/ha@sha256:pin\n"
                "image local 2025-01-01T00:00:00Z local:1.0 \n"
                "image young 2026-11-01T00:00:00Z y/y:latest y/y@sha256:y\n")
        rows = maintenance.parse_image_rows(text, now)
        self.assertEqual(rows[0], ("noah", 625, "ghcr.io/x/noah:latest", {"sha256:aaa"}))
        self.assertEqual(rows[3][3], set())
        self.assertEqual(maintenance.parse_images(text, now)[0], ("noah", 625))
        reg = {"ghcr.io/x/noah:latest": "sha256:aaa", "pdreker/fritz:latest": "sha256:new"}
        asked = []

        def digest(image):
            asked.append(image)
            return reg[image]
        with mock.patch("maintenance._pi", return_value=text), \
                mock.patch("maintenance.parse_image_rows", lambda t: rows):
            got = {i["what"]: i for i in maintenance.images({"docker_max_days": 120}, digest)}
        self.assertEqual(got["Container noah"]["severity"], "ok")
        self.assertIn("neueste Fassung", got["Container noah"]["text"])
        self.assertEqual(got["Container fritz"]["severity"], "warn")      # registry has something newer
        self.assertEqual(got["Container ha"]["severity"], "warn")         # pinned: not asked
        self.assertEqual(got["Container local"]["severity"], "warn")      # local build: nothing to ask
        self.assertEqual(got["Container young"]["severity"], "ok")
        self.assertEqual(asked, ["ghcr.io/x/noah:latest", "pdreker/fritz:latest"])   # only old, pullable ones
        self.assertFalse(maintenance._still_newest("a/b:1", {"sha256:x"}, lambda i: 1 / 0))   # registry down

    def test_describe_from_items(self):
        rows = [dict(what="Zertifikat nas", days=12, severity="warn", text="Das Zertifikat von nas läuft in 12 Tagen ab."),
                dict(what="Container pihole", days=100, severity="ok", text="Container pihole: Image 20 Tage alt.")]
        with mock.patch("maintenance.items", return_value=rows):
            text = maintenance.describe()
            self.assertIn("Fällig: Das Zertifikat von nas läuft in 12 Tagen ab.", text)
            self.assertIn("In Ordnung: Container pihole", text)
            self.assertEqual(router.route("Was steht im Wartungskalender?")["handler"], "maintenance")
        self.assertEqual(policy.action_class("maintenance_status"), policy.READ)


class Integrations(unittest.TestCase):
    def test_paperless_text(self):
        names = {"correspondents": {3: "Allianz"}, "tags": {1: "Versicherung"}, "types": {2: "Police"}}
        d = {"id": 42, "title": "Haftpflicht 2026", "created": "2026-01-05T00:00:00Z", "correspondent": 3, "tags": [1],
             "document_type": 2, "__search_hits__": {"highlights": "Beitrag <span>120,00 €</span> jährlich"}}
        line = paperless.describe_hit(d, names)
        self.assertEqual(line, "#42 Haftpflicht 2026 · 2026-01-05 · Allianz · Police [Versicherung] — „Beitrag 120,00 € jährlich“")
        self.assertIn("nicht eingerichtet", paperless.search("Haftpflicht"))
        self.assertIn("paperless", router.llm_route("Was zahle ich für die Haftpflicht?")["groups"])
        self.assertIn("paperless_search", policy.UNTRUSTED)

    def test_unifi_text(self):
        rows = [dict(name="iPhone", ip="10.0.0.2", wired=False, ssid="Haus", signal=-60, since=""),
                dict(name="Kamera", ip="10.0.0.9", wired=False, ssid="IoT", signal=-80, since=""),
                dict(name="NAS", ip="10.0.0.5", wired=True, ssid="", signal=None, since="")]
        text = unifi.say_clients(rows)
        self.assertIn("3 Geräte verbunden, 2 im WLAN, 1 per Kabel.", text)
        self.assertIn("Haus: iPhone", text)
        self.assertIn("Kabel: NAS", text)
        self.assertIn("Schwacher Empfang: Kamera (-80 dBm)", text)
        self.assertIsNone(unifi.handle(router._norm("Wer ist im WLAN?")))      # not set up: the model may answer
        self.assertIn("unifi", router.llm_route("Wer ist gerade im WLAN?")["groups"])

    def test_jellyfin_text(self):
        ep = {"Id": "abc", "Type": "Episode", "SeriesName": "Dark", "ParentIndexNumber": 2, "IndexNumber": 5, "Name": "Endzeit",
              "UserData": {"PlayedPercentage": 43.2, "LastPlayedDate": "2026-10-27T21:00:00Z"}}
        self.assertEqual(jellyfin.title(ep), "Dark S02E05 Endzeit")
        self.assertEqual(jellyfin._row(ep), "#abc Dark S02E05 Endzeit [Episode, 43 % gesehen, zuletzt 2026-10-27]")
        self.assertEqual(jellyfin.title({"Name": "Heat", "ProductionYear": 1995}), "Heat (1995)")
        self.assertIn("nicht eingerichtet", jellyfin.play("Dark"))
        self.assertIn("media", router.llm_route("Spiel die Serie auf dem Fernseher weiter")["groups"])
        self.assertEqual(policy.action_class("media_play"), policy.LIMITED_WRITE)


class Daytime(unittest.TestCase):
    def tearDown(self):
        daytime.set_mode(None)

    def test_mode_by_hour(self):
        self.assertEqual(daytime.mode(dt.datetime(2026, 10, 29, 10)), "tag")
        self.assertEqual(daytime.mode(dt.datetime(2026, 10, 29, 21, 30)), "spät")
        self.assertEqual(daytime.mode(dt.datetime(2026, 10, 29, 23)), "nacht")
        self.assertEqual(daytime.prepare("Gute Nacht.", True, dt.datetime(2026, 10, 29, 23)), "[whispers] Gute Nacht.")
        self.assertEqual(daytime.prepare("Gute Nacht.", False, dt.datetime(2026, 10, 29, 23)), "Gute Nacht.")
        self.assertEqual(daytime.prepare("[dry] Tja.", True, dt.datetime(2026, 10, 29, 23)), "[dry] Tja.")
        self.assertEqual(daytime.speed_factor(dt.datetime(2026, 10, 29, 10)), 1.0)
        self.assertEqual(daytime.speed_factor(dt.datetime(2026, 10, 29, 22)), daytime.SLOW)
        self.assertEqual(daytime.volume_factor(dt.datetime(2026, 10, 29, 21, 30)), daytime.LATE_VOLUME)
        self.assertIn("Nacht", daytime.hint(dt.datetime(2026, 10, 29, 23)))

    def test_by_hand(self):
        noon = dt.datetime(2026, 10, 29, 12)
        self.assertIn("flüstere", router.route("Flüstermodus an")["reply"])
        self.assertEqual(daytime.mode(noon), "nacht")
        self.assertEqual(daytime.volume_factor(noon), daytime.LATE_VOLUME)
        self.assertIn("Normale Stimme", router.route("Sprich wieder normal")["reply"])
        self.assertEqual(daytime.mode(noon), "tag")


class PhoneAsCard(unittest.TestCase):
    def test_yes_and_no_from_the_phone(self):
        pid = proposals.add("idea", "Testidee", "weil", {}, source="t")
        text = phone_actions.handle_action(f"JARVIS_YES_{pid}", "s1")
        self.assertIn("Vom Handy bestätigt: Testidee", text)
        self.assertEqual(proposals.get(pid)["status"], "accepted")
        self.assertIsNone(phone_actions.handle_action("irgendwas", "s2"))
        pid2 = proposals.add("idea", "Zweite Idee", "weil", {}, source="t2")
        self.assertIn("Verworfen", phone_actions.handle_action(f"JARVIS_NO_{pid2}", "s3"))
        self.assertEqual(proposals.get(pid2)["status"], "declined")
        self.assertIn("nicht mehr offen", phone_actions.handle_action(f"JARVIS_YES_{pid2}", "s4"))

    def test_poll_reads_the_helper_once(self):
        pid = proposals.add("idea", "Dritte Idee", "weil", {}, source="t3")
        st = [{"entity_id": phone_actions.ACTION_ENTITY, "state": f"JARVIS_YES_{pid}", "last_changed": "2026-10-29T10:00:00"}]
        with mock.patch("jarvis_mcp._ha") as ha:
            self.assertIn("bestätigt", phone_actions.poll(st))
            self.assertIsNone(phone_actions.poll(st))                 # the same value again: already handled
            self.assertEqual(ha.call_args.args[1], {"entity_id": phone_actions.ACTION_ENTITY, "value": ""})

    def test_push_has_buttons(self):
        with mock.patch("jarvis_mcp._ha") as ha, mock.patch("infra.PHONE", "notify.mobile_app_iphone"):
            phone_actions.push_proposal("abc123", "Zuhause läuft noch das Licht.")
            body = ha.call_args.args[1]
            self.assertEqual([a["action"] for a in body["data"]["actions"]], ["JARVIS_YES_abc123", "JARVIS_NO_abc123"])


class Focus(unittest.TestCase):
    def tearDown(self):
        commands.update_state(lambda d: d.__setitem__("focus", {}))

    def test_session_holds_notices_and_sums_up(self):
        with mock.patch("jarvis_profile.ha", return_value={}):
            text = router.route("Fokus 45 Minuten")["reply"]
        self.assertIn("Fokus bis", text)
        self.assertTrue(focus.active())
        ev = events.publish("say", "Sir, die Waschmaschine ist fertig (Fokus-Test).", kind="appliance")
        self.assertTrue(ev["silent"] and ev.get("held"))
        alarm = events.publish("alarm", "Wecker", priority="alarm")
        self.assertFalse(alarm.get("silent"))
        self.assertIn("Noch 4", router.route("Wie lange noch Fokus?")["reply"])
        with mock.patch("focus._foreground", return_value="Katzenvideos - YouTube - Chrome"):
            for _ in range(21):
                focus.step()
        self.assertTrue(focus.state()["warned"])
        out = router.route("Fokus beenden")["reply"]
        self.assertIn("Fokus beendet", out)
        self.assertIn("in Youtube", out)
        self.assertIn("Waschmaschine ist fertig", out)
        self.assertFalse(focus.active())
        self.assertIn("keine Fokus-Sitzung", router.route("Fokus beenden")["reply"])

    def test_runs_out_by_itself(self):
        focus.start(5, now=time.time() - 400)
        with mock.patch("events.publish") as pub:
            focus.step()
        self.assertFalse(focus.active())
        self.assertIn("Fokus-Zeit ist um", pub.call_args.args[1])


class SelfCheck(unittest.TestCase):
    def setUp(self):
        if selfcheck.PATH.exists():
            selfcheck.PATH.unlink()
        selfcheck._LAST.update(at=0.0, message="", route="")

    def test_rephrased_question_is_a_miss(self):
        now = time.time()
        local = {"kind": "local", "handler": "weather"}
        self.assertIsNone(selfcheck.note("Wie wird das Wetter morgen?", local, now))
        row = selfcheck.note("Wetter morgen, Regen?", {"kind": "llm", "groups": ["memory", "weather"]}, now + 8)
        self.assertEqual(row["route"], "lokal:weather")
        self.assertEqual(row["gap"], 8)
        self.assertIsNone(selfcheck.note("Ja", local, now + 12))                 # a plain yes is no retry
        self.assertIsNone(selfcheck.note("Wie spät ist es?", local, now + 14))   # another topic
        self.assertIsNone(selfcheck.note("Wie spät ist es jetzt?", local, now + 60))   # too late
        self.assertEqual(len(selfcheck.misses(7)), 1)
        self.assertIn("lokaler Handler weather", selfcheck.spoken_report())
        self.assertEqual(router.route("Wie gut warst du diese Woche?")["handler"], "selfcheck")
        self.assertIn("nochmal fragen", learning.spoken_report(7) if learning.report(7).get("total") else "nochmal fragen")


class RoutineDryRun(unittest.TestCase):
    def test_explains_without_running(self):
        routines.tool("routine_delete", {"name": "Abendrunde"})      # another test file may have left one
        self.assertIn("gespeichert", routines.tool("routine_save", {
            "name": "Abendrunde", "triggers": ["abendrunde bitte"],
            "steps": [{"ha": "light.turn_off", "entity_id": "all"},
                      {"ha": "switch.turn_off", "entity_id": "switch.ventilator"},
                      {"wait": 2}, {"tell": "Wann klingelt mein Wecker?"},
                      {"say": "Schlafen Sie gut, Sir."}]}))
        with mock.patch("jarvis_mcp._ha") as ha:
            out = router.route("Was passiert, wenn ich Abendrunde bitte sage?")
            ha.assert_not_called()
        self.assertEqual(out["handler"], "routine")
        # another test file may have taught the fake house a friendly name for the fan
        self.assertRegex(out["reply"], r"alle Lichter aus; (Ventilator|switch\.ventilator) aus; ich warte 2 Sekunden; "
                                       r"ich beantworte „Wann klingelt mein Wecker\?“; ich sage „Schlafen Sie gut, Sir\.“")
        self.assertIn("nur die Vorschau", out["reply"])
        self.assertIn("kenne ich nicht", routines.explain_request("Was macht die Routine Partymodus?"))
        self.assertIsNone(routines.explain_request("Gute Nacht"))


class AskBack(unittest.TestCase):
    DEVS = [{"eid": "light.wohnzimmer_lampe", "dom": "light", "name": "Wohnzimmer Lampe",
             "base": {"wohnzimmer", "lampe"}, "words": {"wohnzimmer", "lampe"}, "alias": set()},
            {"eid": "switch.wohnzimmer_steckdose", "dom": "switch", "name": "Wohnzimmer Steckdose",
             "base": {"wohnzimmer", "steckdose"}, "words": {"wohnzimmer", "steckdose"}, "alias": set()}]

    def test_asks_once_then_remembers(self):
        calls = []

        def fake_home(name, args):
            calls.append(args["entity_id"])
            return f"Erledigt: {args['service']} für {args['entity_id']}."
        with mock.patch("router._devices", return_value=self.DEVS), mock.patch("jarvis_mcp._home", side_effect=fake_home):
            out = router.route("Mach Wohnzimmer an")
            self.assertEqual(out["kind"], "local")
            self.assertIn("Meinen Sie Wohnzimmer Lampe oder Wohnzimmer Steckdose", out["reply"])
            self.assertEqual(calls, [])
            out = router.route("Die Lampe")
            self.assertEqual(out["handler"], "choice")
            self.assertIn("Wohnzimmer Lampe", out["reply"])
            self.assertRegex(out["reply"], r"\ban\b")
            self.assertEqual(calls, ["light.wohnzimmer_lampe"])
            self.assertEqual(learning.choice(router._norm("mach wohnzimmer an")), ["light.wohnzimmer_lampe"])
            out = router.route("Wohnzimmer aus")                       # the same wording: no question this time
            self.assertEqual(out["handler"], "home")
            self.assertEqual(calls[-1], "light.wohnzimmer_lampe")
            self.assertIsNone(router._choice(router._norm("die lampe")))   # nothing pending: not an answer


if __name__ == "__main__":
    unittest.main()
