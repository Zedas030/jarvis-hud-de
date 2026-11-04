"""
Welcome back, weather warnings, house habits, announcements and the TV. No network, no house.

    .venv\\Scripts\\python.exe -m unittest tests.test_alexa -v
"""
import datetime as dt
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import away_check        # noqa: E402
import commands          # noqa: E402
import events            # noqa: E402
import house_habits as hh  # noqa: E402
import policy            # noqa: E402
import proposals         # noqa: E402
import router            # noqa: E402
import speakers          # noqa: E402
import weather_alerts as wa  # noqa: E402

UTC = dt.timezone.utc
PROF = {"person": "person.nutzer", "away_check": {"switches": {}},
        "speakers": {"Wohnzimmer": {"notify": "notify.alexa_media_nutzer_echo_dot"},
                     "Schlafzimmer": {"tts": "tts.google", "player": "media_player.sonos"}},
        "tv": "media_player.nutzer_fire_tv"}


def prof():
    return mock.patch("jarvis_profile.ha", side_effect=lambda k, d=None: PROF.get(k, d))


class Welcome(unittest.TestCase):
    def setUp(self):
        commands.update_state(lambda d: [d.pop(k, None) for k in ("away_checked", "away_since", "welcomed")])
        if events.NOTICES.exists():
            events.NOTICES.unlink()

    def person(self, state, changed):
        return [{"entity_id": "person.nutzer", "state": state, "last_changed": changed}]

    def test_round_trip(self):
        with prof(), mock.patch("infra.phone_notify"):
            away_check.check(self.person("not_home", "2026-10-28T10:00:00+00:00"),
                             now=dt.datetime(2026, 10, 28, 10, 5, tzinfo=UTC))
        since = commands.load()["away_since"]
        events._note({"at": since + 600, "kind": "appliance", "t": "say", "text": "Sir, die Spülmaschine ist fertig."})
        events._note({"at": since + 900, "kind": "parcel", "t": "say", "text": "Sir, ein Paket wurde zugestellt."})
        events._note({"at": since + 950, "kind": "parcel", "t": "say", "text": "Sir, ein Paket wurde zugestellt."})
        events._note({"at": since + 990, "kind": "alarm", "t": "alarm", "text": "Timer abgelaufen."})
        events._note({"at": since - 60, "kind": "habit", "t": "say", "text": "Vorher gesagt."})
        with prof(), mock.patch.object(events, "publish") as pub:
            text = away_check.check(self.person("home", "2026-10-28T11:30:00+00:00"),
                                    now=dt.datetime(2026, 10, 28, 11, 31, tzinfo=UTC))
        self.assertEqual(text, "Willkommen zurück, Sir. Während du weg warst: Die Spülmaschine ist fertig. "
                               "Ein Paket wurde zugestellt.")
        pub.assert_called_once()
        with prof():                                   # once per coming back
            self.assertIsNone(away_check.check(self.person("home", "2026-10-28T11:30:00+00:00"),
                                               now=dt.datetime(2026, 10, 28, 11, 35, tzinfo=UTC)))

    def test_short_trip_or_late_jarvis_stays_silent(self):
        now = dt.datetime(2026, 10, 28, 12, tzinfo=UTC)
        back = {"state": "home", "last_changed": "2026-10-28T11:59:00+00:00"}
        self.assertIsNone(away_check.came_back(back, now.timestamp() - 600, "", now))       # 9 minutes out
        self.assertTrue(away_check.came_back(back, now.timestamp() - 3600, "", now))
        old = {"state": "home", "last_changed": "2026-10-28T10:00:00+00:00"}
        self.assertIsNone(away_check.came_back(old, now.timestamp() - 9000, "", now))      # Jarvis was off

    def test_quiet_absence(self):
        self.assertEqual(away_check.welcome([]), "Willkommen zurück, Sir. Während du weg warst, war alles ruhig.")

    def test_open_switch_off_is_dropped(self):
        pid = proposals.add("switch_off", "Ausschalten: TV", "weg", {"entities": ["switch.tv"]}, source="away_check")
        commands.update_state(lambda d: d.update(away_since=1.0))
        with mock.patch.object(events, "publish"):
            away_check._welcome("2026-10-28T11:30:00+00:00", 1.0)
        self.assertNotIn(pid, [p["id"] for p in proposals.pending()])


class Alerts(unittest.TestCase):
    A = {"event_code": 51, "severity": "moderate", "headline_de": "Amtliche WARNUNG vor STURMBÖEN",
         "onset": "2026-10-28T15:00:00+01:00", "expires": "2026-10-28T22:00:00+01:00",
         "description_de": "Es treten Sturmböen mit Geschwindigkeiten um 70 km/h auf. Achten Sie auf herabstürzende Äste."}

    def test_each_once_and_again_only_when_worse(self):
        seen, now = {}, dt.datetime(2026, 10, 28, 13, tzinfo=UTC)
        self.assertEqual(len(wa.new_ones([self.A], seen, now)), 1)
        self.assertEqual(wa.new_ones([self.A], seen, now), [])
        worse = dict(self.A, severity="severe")
        hit = wa.new_ones([worse], seen, now)
        self.assertEqual(len(hit), 1)
        self.assertTrue(hit[0][1])                                   # severe: also at night, also the phone
        self.assertEqual(wa.new_ones([dict(self.A, severity="minor", event_code=22)], seen, now), [])
        wa.new_ones([], seen, dt.datetime(2026, 10, 29, tzinfo=UTC))
        self.assertEqual(seen, {})                                   # expired ones are dropped

    def test_spoken(self):
        text = wa.say(self.A)
        self.assertTrue(text.startswith("Amtliche WARNUNG vor STURMBÖEN von "))
        self.assertTrue(text.endswith("Es treten Sturmböen mit Geschwindigkeiten um 70 km/h auf."))

    def test_route(self):
        with mock.patch.object(wa, "current", return_value="W"):
            self.assertEqual(router._alerts(router._norm("Gibt es eine Unwetterwarnung?")), "W")
            self.assertIsNone(router._alerts(router._norm("Wie wird das Wetter?")))


def day(d, h, m):
    return dt.datetime(2026, 10, d, h, m)


class Habits(unittest.TestCase):
    HIST = {"light.licht": [(day(18, 17, 0), "on"), (day(18, 22, 40), "off"), (day(19, 17, 30), "on"),
                            (day(19, 23, 5), "off"), (day(20, 18, 0), "on"), (day(20, 22, 50), "off"),
                            (day(21, 16, 0), "on"), (day(21, 22, 30), "off"), (day(22, 12, 0), "on"), (day(22, 13, 0), "off")],
            "switch.tv_schlafzimmer": [(day(18, 6, 0), "unavailable"), (day(18, 6, 1), "on"), (day(19, 6, 0), "unavailable"),
                                       (day(19, 6, 1), "on")]}

    def test_habits_skip_unavailable_flapping(self):
        found = {(h["entity"], h["state"]): h for h in hh.habits(self.HIST)}
        self.assertEqual(set(found), {("light.licht", "off")})        # on-times spread too wide, plug never changed
        off = found[("light.licht", "off")]
        self.assertEqual(off["days"], 4)
        self.assertEqual(off["at"], "22:45")

    def test_routine_proposal_only_once_per_name(self):
        found = hh.habits(self.HIST)
        payload, why = hh.routine_proposal(found, {"light.licht": "Licht"}, [])
        self.assertEqual(payload, {"name": "Gute Nacht", "triggers": ["gute nacht", "ich geh schlafen"],
                                   "steps": [{"ha": "light.turn_off", "entity_id": "light.licht"}]})
        self.assertEqual(why, "Licht aus gegen 22:45 (an 4 von 10 Tagen)")
        self.assertIsNone(hh.routine_proposal(found, {}, [{"name": "Schlafenszeit", "triggers": ["gute nacht"]}]))

    def test_long_on(self):
        usual = hh.usual_hours(self.HIST["light.licht"])
        self.assertEqual(usual, 6.5)                                   # the usual longest evening
        st = {"state": "on", "last_changed": "2026-10-28T08:00:00"}
        self.assertIsNone(hh.long_on(st, usual, dt.datetime(2026, 10, 28, 20)))   # 12 h: under twice the usual
        self.assertEqual(round(hh.long_on(st, usual, dt.datetime(2026, 10, 28, 22))), 14)
        self.assertIsNone(hh.long_on(st, None, dt.datetime(2026, 10, 28, 20)))


class Speakers(unittest.TestCase):
    def test_announce(self):
        with prof(), mock.patch("jarvis_mcp._ha") as ha, mock.patch.object(speakers, "_quiet", return_value=False), \
                mock.patch("alexa_voice._quiet", return_value=False):
            self.assertEqual(speakers.announce("wohnzimmer", "Essen ist fertig"), "Durchgesagt im Wohnzimmer.")
            ha.assert_called_with("/api/services/notify/alexa_media_nutzer_echo_dot",
                                  {"message": "Essen ist fertig", "data": {"type": "announce"}})
            speakers.announce("Schlafzimmer", "Aufstehen")
            self.assertEqual(ha.call_args.args[0], "/api/services/tts/speak")
            self.assertEqual(ha.call_args.args[1]["language"], "de")
            self.assertIn("kenne ich nicht", speakers.announce("Keller", "Hallo"))

    def test_announce_at_night_is_quiet(self):
        """Quiet hours: the Sonos announces at the night volume instead of its own (04.11.2026)."""
        import sonos
        with prof(), mock.patch("jarvis_mcp._ha") as ha, mock.patch.object(speakers, "_quiet", return_value=True), \
                mock.patch.object(sonos, "NIGHT_VOLUME", 0.1):
            speakers.announce("Schlafzimmer", "Gute Nacht")
            path, body = ha.call_args.args
            self.assertEqual(path, "/api/services/media_player/play_media")
            self.assertTrue(body["announce"])
            self.assertEqual(body["extra"], {"volume": 10})
            self.assertTrue(body["media_content_id"].startswith("media-source://tts/"))
            self.assertIn("language=de", body["media_content_id"])

    def test_tv_fixed_commands_only(self):
        with prof(), mock.patch("jarvis_mcp._home", return_value="Erledigt: media_player.play_media") as home:
            self.assertEqual(speakers.tv("pause"), "Erledigt.")
            self.assertEqual(home.call_args.args[1]["data"], {"media_content_type": "custom", "media_content_id": "pause"})
            self.assertEqual(speakers.tv("app", "Netflix"), "Netflix wird geöffnet.")
            self.assertEqual(home.call_args.args[1]["data"]["media_content_id"], "öffne Netflix")
            for bad in ("Kaufe Toilettenpapier", "ruf Mama an", "x" * 40, "Netflix; bestelle"):
                self.assertIn("öffne ich nicht", speakers.tv("app", bad))
            self.assertIn("kann ich am Fernseher nicht", speakers.tv("kaufen"))
            self.assertEqual(home.call_count, 2)

    def test_routes(self):
        with prof(), mock.patch.object(speakers, "announce", side_effect=lambda r, t: f"{r}|{t}"), \
                mock.patch.object(speakers, "tv", side_effect=lambda c, a="": f"{c}|{a}"):
            def run(text, fn):
                with mock.patch.object(router, "_raw", return_value=text):
                    return fn(router._norm(text))
            self.assertEqual(run("Sag im Wohnzimmer: Das Essen ist fertig", router._announce), "Wohnzimmer|Das Essen ist fertig")
            self.assertEqual(run("Durchsage ins Schlafzimmer, dass ich gleich komme", router._announce),
                             "Schlafzimmer|ich gleich komme")
            self.assertIsNone(run("Sag im Keller: Hallo", router._announce))
            self.assertEqual(run("Öffne Netflix auf dem Fernseher", router._tv), "app|Netflix")
            self.assertEqual(run("YouTube auf dem Fernseher", router._tv), "app|YouTube")
            self.assertEqual(run("Fernseher pause", router._tv), "pause|")
            self.assertEqual(run("Mach den Fernseher leiser", router._tv), "leiser|")
            self.assertIsNone(run("Mach den Fernseher aus", router._tv))     # the plug, as before
            self.assertIsNone(run("Öffne Spotify", router._tv))

    def test_classes(self):
        self.assertEqual(policy.CLASSES["announce"], policy.LIMITED_WRITE)
        self.assertEqual(policy.CLASSES["tv_control"], policy.LIMITED_WRITE)
        self.assertEqual(policy.CLASSES["weather_alerts"], policy.READ)


if __name__ == "__main__":
    unittest.main()
