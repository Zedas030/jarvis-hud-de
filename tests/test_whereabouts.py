"""
Which room the user is in (presence sensors through Home Assistant) and what follows from it:
the "Wo ich bin" output, announcements without a room, the model's context line. No network, no house.

    .venv\\Scripts\\python.exe -m unittest tests.test_whereabouts -v
"""
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import sonos             # noqa: E402
import speakers          # noqa: E402
import whereabouts       # noqa: E402

ECHO = "notify.alexa_media_nutzer_echo_dot"
SONOS = "media_player.schlafzimmer"
PROF = {"speakers": {"Wohnzimmer": {"notify": ECHO},
                     "Schlafzimmer": {"tts": "tts.google", "player": SONOS}},
        "presence_room": "input_select.nutzer_raum"}


def prof(**over):
    data = {**PROF, **over}
    return mock.patch("jarvis_profile.ha", side_effect=lambda k, d=None: data.get(k, d))


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def ha_state(state):
    body = json.dumps({"state": state}).encode()
    return mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: _Resp(body))   # fresh body per read


class Room(unittest.TestCase):
    def setUp(self):
        whereabouts.forget()

    def test_off_without_entity_or_home_assistant(self):
        with prof(presence_room=""), mock.patch.object(whereabouts, "HA_TOKEN", "x"):
            self.assertFalse(whereabouts.configured())
            self.assertEqual(whereabouts.room(), "")
        with prof(), mock.patch.object(whereabouts, "HA_TOKEN", ""):
            self.assertEqual(whereabouts.room(), "")
            self.assertEqual(whereabouts.line(), "")

    def test_reads_the_helper_and_caches(self):
        with prof(), mock.patch.object(whereabouts, "HA_TOKEN", "x"), ha_state("Schlafzimmer") as u:
            self.assertEqual(whereabouts.room(now=1000.0), "Schlafzimmer")
            self.assertEqual(whereabouts.room(now=1005.0), "Schlafzimmer")
            self.assertEqual(u.call_count, 1)                    # within TTL: no second request
            self.assertIn("input_select.nutzer_raum", u.call_args[0][0].full_url)
            self.assertEqual(whereabouts.room(now=1020.0), "Schlafzimmer")
            self.assertEqual(u.call_count, 2)

    def test_unknown_is_empty(self):
        for state in ("Unbekannt", "unknown", "unavailable", ""):
            whereabouts.forget()
            with prof(), mock.patch.object(whereabouts, "HA_TOKEN", "x"), ha_state(state):
                self.assertEqual(whereabouts.room(), "", state)

    def test_home_assistant_down_keeps_the_last_room(self):
        with prof(), mock.patch.object(whereabouts, "HA_TOKEN", "x"):
            with ha_state("Wohnzimmer"):
                self.assertEqual(whereabouts.room(now=1000.0), "Wohnzimmer")
            with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
                self.assertEqual(whereabouts.room(now=1100.0), "Wohnzimmer")

    def test_speaker_for_room(self):
        with prof():
            self.assertEqual(whereabouts.speaker("Wohnzimmer"), ECHO)
            self.assertEqual(whereabouts.speaker("schlafzimmer"), SONOS)
            self.assertEqual(whereabouts.speaker("Bad"), "")
            self.assertEqual(whereabouts.speaker(""), "")

    def test_context_line(self):
        with prof(), mock.patch.object(whereabouts, "HA_TOKEN", "x"), ha_state("Wohnzimmer"):
            line = whereabouts.line()
            self.assertIn("im Wohnzimmer", line)
            self.assertIn("Bad und Schlafzimmer", line)


class FollowMe(unittest.TestCase):
    """The output "Wo ich bin": the device's answers come from the speaker of the room the user is in."""

    def setUp(self):
        whereabouts.forget()
        self.patches = [prof(), mock.patch.object(whereabouts, "HA_TOKEN", "x"), mock.patch.object(sonos, "HA_TOKEN", "x"),
                        mock.patch.object(sonos, "SPEAKERS", [{"id": SONOS, "label": "Sonos Schlafzimmer"}])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        sonos.set_output("ipad", "")

    def test_auto_is_an_output_once_presence_is_configured(self):
        self.assertEqual([s["id"] for s in sonos.outputs()], [SONOS, ECHO, sonos.AUTO])
        self.assertEqual(sonos.label(sonos.AUTO), "Wo ich bin")
        with prof(presence_room=""):
            self.assertNotIn(sonos.AUTO, [s["id"] for s in sonos.outputs()])

    def test_follows_the_room(self):
        sonos.set_output("ipad", sonos.AUTO)
        with ha_state("Wohnzimmer"):
            self.assertEqual(sonos.output_for("ipad"), ECHO)
            self.assertEqual(sonos.status("ipad")["output"], sonos.AUTO)
            self.assertEqual(sonos.status("ipad")["output_label"], "Wo ich bin · Echo Wohnzimmer")
        whereabouts.forget()
        with ha_state("Schlafzimmer"):
            self.assertEqual(sonos.output_for("ipad"), SONOS)
        whereabouts.forget()
        with ha_state("Unbekannt"):                                   # nobody seen: the device itself
            self.assertEqual(sonos.output_for("ipad"), "")
            self.assertEqual(sonos.status("ipad")["output_label"], "Wo ich bin · hier")

    def test_default_is_auto_and_local_is_a_choice(self):
        with ha_state("Wohnzimmer"):
            self.assertEqual(sonos.output_for("neu"), ECHO)           # never chose: follows the user
            sonos.set_output("neu", "")
            self.assertEqual(sonos.output_for("neu"), "")             # "Ton hier", chosen on purpose
            self.assertEqual(sonos.status("neu")["output"], "")
            sonos.set_output("neu", ECHO)
            self.assertEqual(sonos.output_for("neu"), ECHO)
        with prof(presence_room=""):
            self.assertEqual(sonos.output_for("anders"), "")          # no sensors: as before


class Announce(unittest.TestCase):
    def setUp(self):
        whereabouts.forget()

    def test_without_room_goes_where_the_user_is(self):
        calls = []
        with prof(), mock.patch.object(whereabouts, "HA_TOKEN", "x"), ha_state("Wohnzimmer"), \
                mock.patch("alexa_voice._quiet", return_value=False), \
                mock.patch("jarvis_mcp._ha", side_effect=lambda path, body=None: calls.append((path, body))):
            self.assertEqual(speakers.announce("", "Essen ist fertig"), "Durchgesagt im Wohnzimmer.")
            self.assertEqual(speakers.announce("hier", "Essen ist fertig"), "Durchgesagt im Wohnzimmer.")
        self.assertEqual(len(calls), 2)
        self.assertIn("alexa_media_nutzer_echo_dot", calls[0][0])

    def test_without_room_and_nobody_seen_asks(self):
        with prof(), mock.patch.object(whereabouts, "HA_TOKEN", "x"), ha_state("Unbekannt"), \
                mock.patch("jarvis_mcp._ha") as ha:
            self.assertIn("welchem Raum", speakers.announce("", "Hallo"))
            ha.assert_not_called()


if __name__ == "__main__":
    unittest.main()
