"""
Answers on the Echo, the morning briefing in the living room, follow-ups to switching. No network, no house.

    .venv\\Scripts\\python.exe -m unittest tests.test_echo -v
"""
import datetime as dt
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import alexa_voice       # noqa: E402
import commands          # noqa: E402
import morning_echo      # noqa: E402
import router            # noqa: E402
import sonos             # noqa: E402

ECHO = "notify.alexa_media_nutzer_echo_dot"
PROF = {"speakers": {"Wohnzimmer": {"notify": ECHO},
                     "Schlafzimmer": {"tts": "tts.google", "player": "media_player.sonos"}},
        "morning_briefing": {"room": "Wohnzimmer", "triggers": ["light.licht", "media_player.nutzer_echo_dot"]}}


def prof():
    return mock.patch("jarvis_profile.ha", side_effect=lambda k, d=None: PROF.get(k, d))


class EchoOutput(unittest.TestCase):
    def test_echo_is_an_output(self):
        with prof(), mock.patch.object(sonos, "HA_TOKEN", "x"), mock.patch.object(sonos, "SPEAKERS", []):
            self.assertTrue(sonos.configured())
            self.assertEqual(sonos.label(ECHO), "Echo Wohnzimmer")
            self.assertEqual([s["id"] for s in sonos.status("pc")["speakers"]], [ECHO])
            sonos.set_output("pc", ECHO)
            self.assertEqual(sonos.output_for("pc"), ECHO)
            sonos.set_output("pc", "")

    def test_clean_and_pieces(self):
        self.assertEqual(alexa_voice.clean("[ruhig] **Gut**, Sir… siehe https://x.y/z"), "Gut , Sir. siehe")
        self.assertEqual(alexa_voice.clean("📦 Amazon: Erde · DHL ✅"), "Amazon: Erde, DHL")
        long = "Ein Satz mit etwas Inhalt. " * 20
        parts = alexa_voice.pieces(long)
        self.assertTrue(all(len(p) <= alexa_voice.MAX_CHARS for p in parts))
        self.assertTrue(all(p.endswith(".") for p in parts))
        self.assertEqual(" ".join(parts), long.strip())

    def test_sentences_wait_for_each_other(self):
        sent = []
        with mock.patch("jarvis_mcp._ha", side_effect=lambda path, body: sent.append((path, body))), \
                mock.patch.object(alexa_voice, "CHARS_PER_S", 1000.0), mock.patch.object(alexa_voice, "LAG_S", 0.05):
            first = alexa_voice.say(ECHO, "Erster Satz.")
            second = alexa_voice.say(ECHO, "Zweiter Satz.")
            self.assertGreater(second, first)
            time.sleep(second + 0.3)
        self.assertEqual([b["message"] for _, b in sent], ["Erster Satz.", "Zweiter Satz."])
        self.assertEqual(sent[0][0], "/api/services/notify/alexa_media_nutzer_echo_dot")
        self.assertEqual(sent[0][1]["data"], {"type": "tts"})

    def test_sentences_while_she_speaks_go_out_as_one(self):
        sent = []
        with mock.patch("jarvis_mcp._ha", side_effect=lambda path, body: sent.append(body["message"])),                 mock.patch.object(alexa_voice, "CHARS_PER_S", 1000.0), mock.patch.object(alexa_voice, "LAG_S", 0.3):
            alexa_voice.say(ECHO, "Eins.")
            alexa_voice.say(ECHO, "Zwei.")
            done = alexa_voice.say(ECHO, "Drei.")
            time.sleep(done + 0.3)
        self.assertEqual(sent, ["Eins.", "Zwei. Drei."])

    def test_stop_drops_what_waits(self):
        sent = []
        with mock.patch("jarvis_mcp._ha", side_effect=lambda path, body: sent.append(body)):
            alexa_voice._BUSY[ECHO] = time.time() + 5             # Alexa still speaks the sentence before
            alexa_voice.say(ECHO, "Gleich.")
            alexa_voice.stop(ECHO)
            time.sleep(0.2)
        self.assertEqual(sent, [])


    def test_hard_stop_ends_the_sentence_too(self):
        sent = []
        with prof(), mock.patch("jarvis_mcp._ha", side_effect=lambda path, body: sent.append((path, body))):
            alexa_voice.stop(ECHO, hard=True)
        self.assertEqual(sent, [("/api/services/media_player/media_stop", {"entity_id": "media_player.nutzer_echo_dot"})])


class EchoAtNight(unittest.TestCase):
    """In the quiet hours the Echo is turned down before Alexa speaks and back afterwards (04.11.2026)."""

    def run_say(self, quiet, start_volume, hand_turned=None):
        calls, vol = [], {"v": start_volume}

        def ha(path, body=None):
            calls.append((path, body))
            if path.startswith("/api/states/"):
                return {"state": "idle", "attributes": {"volume_level": hand_turned or vol["v"]}}
            if path.endswith("/volume_set"):
                vol["v"] = body["volume_level"]
            return None
        with prof(), mock.patch("jarvis_mcp._ha", side_effect=ha), \
                mock.patch.object(alexa_voice, "_quiet", return_value=quiet), \
                mock.patch.object(alexa_voice, "RESTORE_AFTER_S", 0.05), \
                mock.patch.object(alexa_voice, "CHARS_PER_S", 1000.0), mock.patch.object(alexa_voice, "LAG_S", 0.05):
            alexa_voice.say(ECHO, "Gute Nacht.")
            time.sleep(0.5)
        return [(p, b) for p, b in calls if b is not None]

    def test_night_turns_down_then_back(self):
        posts = self.run_say(True, 0.6)
        self.assertEqual(posts[0], ("/api/services/media_player/volume_set",
                                    {"entity_id": "media_player.nutzer_echo_dot", "volume_level": alexa_voice.NIGHT_VOLUME}))
        self.assertEqual(posts[1][1]["message"], "Gute Nacht.")
        self.assertEqual(posts[-1][1], {"entity_id": "media_player.nutzer_echo_dot", "volume_level": 0.6})

    def test_day_and_already_quiet_leave_volume(self):
        for quiet, v in ((False, 0.6), (True, 0.1)):
            posts = self.run_say(quiet, v)
            self.assertEqual([p for p, _ in posts], ["/api/services/notify/alexa_media_nutzer_echo_dot"])

    def test_turned_by_hand_stays(self):
        posts = self.run_say(True, 0.6, hand_turned=0.5)        # someone set 0.5 while Alexa spoke
        self.assertEqual(len([p for p, _ in posts if p.endswith("volume_set")]), 1)   # only the turn-down


class Morning(unittest.TestCase):
    def setUp(self):
        morning_echo._LAST.clear()
        commands.update_state(lambda d: d.pop("briefing_day", None))

    def st(self, light="off", called="1"):
        return [{"entity_id": "light.licht", "state": light},
                {"entity_id": "media_player.nutzer_echo_dot", "state": "idle",
                 "attributes": {"last_called_timestamp": called}}]

    def test_triggers(self):
        last = {}
        trig = ["light.licht", "media_player.nutzer_echo_dot"]
        self.assertIsNone(morning_echo.triggered(self.st(), trig, last))          # first look only
        self.assertIsNone(morning_echo.triggered(self.st(), trig, last))
        self.assertEqual(morning_echo.triggered(self.st("on"), trig, last), "light.licht")
        self.assertIsNone(morning_echo.triggered(self.st("on"), trig, last))
        self.assertEqual(morning_echo.triggered(self.st("on", "2"), trig, last), "media_player.nutzer_echo_dot")

    def test_once_a_morning_and_counts_as_briefing(self):
        now = dt.datetime(2026, 10, 29, 7, 10)
        with prof(), mock.patch.object(morning_echo, "text", return_value="Guten Morgen, Sir.") as text, \
                mock.patch.object(morning_echo, "_speak") as speak, mock.patch.object(morning_echo, "ECHO_WAIT_S", 0):
            morning_echo.check(self.st(), now)
            morning_echo.check(self.st("on"), now)
            morning_echo.check(self.st("off"), now)
            morning_echo.check(self.st("on"), now)
            time.sleep(0.2)
        self.assertEqual(speak.call_count, 1)
        speak.assert_called_with("Wohnzimmer", "Guten Morgen, Sir.")
        self.assertEqual(commands.load()["briefing_day"], "2026-10-29")
        text.assert_called_once()

    def test_not_in_the_evening(self):
        with prof(), mock.patch.object(morning_echo, "_speak") as speak:
            morning_echo.check(self.st(), dt.datetime(2026, 10, 29, 18, 0))
            morning_echo.check(self.st("on"), dt.datetime(2026, 10, 29, 18, 0))
        speak.assert_not_called()

    def test_agenda_with_parcel(self):
        today = dt.datetime.combine(dt.date.today(), dt.time(9, 30))
        evts = [{"start": today, "allday": True, "title": "📦 Amazon: Erde · DHL"},
                {"start": today, "title": "Zahnarzt"}]
        with mock.patch("icloud_calendar.configured", return_value=True),                 mock.patch("icloud_calendar.events", return_value=evts):
            self.assertEqual(morning_echo._agenda(), "Heute kommt ein Paket von Amazon. Termine: um 9:30 Zahnarzt.")

    def test_text(self):
        with mock.patch.object(morning_echo, "_weather", return_value="Heute teils bewölkt, 5 bis 12 Grad"), \
                mock.patch.object(morning_echo, "_alerts", return_value=""), \
                mock.patch.object(morning_echo, "_agenda", return_value="Termine: um 9:30 Zahnarzt."), \
                mock.patch.object(morning_echo, "_coach", side_effect=RuntimeError("weg")), \
                mock.patch.object(morning_echo, "_night", return_value=""), \
                mock.patch.object(morning_echo, "_mail", return_value="Sie haben 2 ungelesene Mails."), \
                mock.patch("jarvis_profile.USER_NAME", ""):
            self.assertEqual(morning_echo.text(), "Guten Morgen, Sir. Heute teils bewölkt, 5 bis 12 Grad. "
                                                  "Termine: um 9:30 Zahnarzt. Sie haben 2 ungelesene Mails.")


def dev(eid, name, words=None):
    base = set(router._norm(name).split())
    return {"eid": eid, "dom": eid.split(".")[0], "name": name, "base": base, "words": base | set(words or []),
            "alias": set(words or [])}


class FollowUp(unittest.TestCase):
    DEVS = [dev("light.licht", "LED", ["wohnzimmer", "licht"]), dev("switch.tv", "TV"),
            dev("switch.tv_schlafzimmer", "TV Schlafzimmer")]

    def setUp(self):
        self.saved = dict(router._DEV)
        router._DEV.update(at=time.time() + 10 ** 9, list=self.DEVS)
        router._SWITCHED.update(devs=[], service="", at=0.0)
        self.calls = []
        self.p = mock.patch("jarvis_mcp._home", side_effect=lambda tool, a: self.calls.append(a) or "Erledigt.")
        self.p.start()
        self.lp = mock.patch("learning.alias", return_value=None)
        self.lp.start()

    def tearDown(self):
        self.p.stop()
        self.lp.stop()
        router._DEV.update(self.saved)

    def run_(self, text):
        return router._local(text, router.LOCAL_HANDLERS)

    def test_nothing_without_a_switch_before(self):
        self.assertIsNone(router._followup("heller"))

    def test_brighter_then_off(self):
        reply, h = self.run_("Licht im Wohnzimmer an")
        self.assertEqual(h, "home")
        reply, h = self.run_("Etwas heller bitte")
        self.assertEqual(h, "followup")
        self.assertEqual(self.calls[-1], {"entity_id": "light.licht", "service": "turn_on",
                                          "data": {"brightness_step_pct": 25}})
        self.run_("dunkler")
        self.assertEqual(self.calls[-1]["data"], {"brightness_step_pct": -25})
        self.run_("auf 30 Prozent")
        self.assertEqual(self.calls[-1]["data"], {"brightness_pct": 30})
        reply, _ = self.run_("Und wieder aus")
        self.assertEqual(self.calls[-1]["service"], "turn_off")
        self.assertIn("LED ist aus", reply)

    def test_other_room_too(self):
        self.run_("Schalte den TV an")
        self.assertEqual(self.calls[-1]["entity_id"], "switch.tv")
        reply, h = self.run_("Und im Schlafzimmer auch")
        self.assertEqual(self.calls[-1], {"entity_id": "switch.tv_schlafzimmer", "service": "turn_on", "data": {}})

    def test_too_late(self):
        self.run_("Licht im Wohnzimmer an")
        router._SWITCHED["at"] -= router.FOLLOW_S + 1
        self.assertIsNone(router._followup("heller"))

    def test_protected_not_off(self):
        router._SWITCHED.update(devs=[dev("switch.router", "Router")], service="turn_on", at=time.time())
        self.assertIsNone(router._followup("wieder aus"))



class GolfIsNotAlwaysWeather(unittest.TestCase):
    def test_weather_questions_only(self):
        fc = {"daily": {"weather_code": [3, 3], "temperature_2m_max": [12, 12], "temperature_2m_min": [5, 5],
                        "precipitation_probability_max": [10, 10], "wind_speed_10m_max": [10, 10]}}
        with mock.patch.object(router, "_forecast", return_value=fc),                 mock.patch.object(router, "_dash", return_value={"golf": "ja", "golf_day": "morgen"}):
            for asks in ("Kann ich morgen Golf spielen?", "Wie ist das Golfwetter morgen?", "Golf heute?",
                         "Lohnt sich Golf morgen?"):
                self.assertTrue(router._weather(router._norm(asks)), asks)
            for talk in ("Ich habe morgen Trainerstunde. Golf, was empfiehlst du mir?",
                         "Ich habe morgen eine Trainerstunde zum Golfen. Was würdest du mir empfehlen?",
                         "Ich habe mir neue Golfschläger gekauft, Ping G440, was hältst du davon?"):
                self.assertIsNone(router._weather(router._norm(talk)), talk)


if __name__ == "__main__":
    unittest.main()
