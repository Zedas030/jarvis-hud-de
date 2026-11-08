"""
"Hol die Musik ins Wohnzimmer": the soundbar is the Spotify device of the room; when Spotify does not list
it, Jarvis asks again a few times before it settles for the worse speaker (the Echo Dot alone). No Spotify,
no house.

    .venv\\Scripts\\python.exe -m unittest tests.test_music_room -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import jarvis_mcp        # noqa: E402
import speakers          # noqa: E402
import spotify           # noqa: E402

PROF = {"speakers": {"Wohnzimmer": {"notify": "notify.alexa_media_nutzer_echo_dot", "spotify": "Wohnzimmer",
                                    "spotify_fallback": "Nutzer' Echo Dot"},
                     "Schlafzimmer": {"player": "media_player.schlafzimmer"}}}
MISSING = "Spotify kennt kein Gerät „Wohnzimmer“. Gerade erreichbar: Nutzer' Echo Dot."


def prof():
    return mock.patch("jarvis_profile.ha", side_effect=lambda k, d=None: PROF.get(k, d))


class Fallback(unittest.TestCase):
    def setUp(self):
        self.patches = [prof(), mock.patch.object(spotify, "configured", return_value=True),
                        mock.patch.object(jarvis_mcp.time, "sleep"), mock.patch.object(jarvis_mcp, "MUSIC_ROOM_TRIES", 3),
                        mock.patch.object(jarvis_mcp, "_speaker_stream", return_value=None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_config(self):
        self.assertEqual(speakers.spotify_device("Wohnzimmer"), ("Wohnzimmer", "Wohnzimmer", ""))
        self.assertEqual(speakers.spotify_fallback("wohnzimmer"), "Nutzer' Echo Dot")
        self.assertEqual(speakers.spotify_fallback("Schlafzimmer"), "")
        self.assertEqual(speakers.spotify_fallback("Keller"), "")

    def test_soundbar_first_time(self):
        with mock.patch.object(spotify, "transfer", return_value=(True, "Wohnzimmer")) as t:
            self.assertEqual(jarvis_mcp._music_room("Wohnzimmer"), "Die Musik läuft jetzt im Wohnzimmer.")
        self.assertEqual([c.args[0] for c in t.call_args_list], ["Wohnzimmer"])

    def test_soundbar_back_on_the_second_try(self):
        answers = iter([(False, MISSING), (True, "Wohnzimmer")])
        with mock.patch.object(spotify, "transfer", side_effect=lambda n: next(answers)) as t:
            self.assertEqual(jarvis_mcp._music_room("Wohnzimmer"), "Die Musik läuft jetzt im Wohnzimmer.")
        self.assertEqual([c.args[0] for c in t.call_args_list], ["Wohnzimmer", "Wohnzimmer"])
        self.assertEqual(jarvis_mcp.time.sleep.call_count, 1)

    def test_echo_dot_only_after_three_tries(self):
        answers = iter([(False, MISSING)] * 3 + [(True, "Nutzer' Echo Dot")])
        with mock.patch.object(spotify, "transfer", side_effect=lambda n: next(answers)) as t:
            text = jarvis_mcp._music_room("Wohnzimmer")
        self.assertEqual([c.args[0] for c in t.call_args_list], ["Wohnzimmer"] * 3 + ["Nutzer' Echo Dot"])
        self.assertIn("ersatzweise", text)
        self.assertIn("Nutzer' Echo Dot", text)
        self.assertEqual(jarvis_mcp.time.sleep.call_count, 2)

    def test_other_errors_do_not_retry(self):
        with mock.patch.object(spotify, "transfer", return_value=(False, "Spotify antwortet nicht (timeout).")) as t:
            self.assertEqual(jarvis_mcp._music_room("Wohnzimmer"), "Spotify antwortet nicht (timeout).")
        self.assertEqual(t.call_count, 1)

    def test_room_without_fallback_asks_once(self):
        with mock.patch.object(spotify, "transfer", return_value=(False, MISSING.replace("Wohnzimmer", "Schlafzimmer"))) as t, \
                mock.patch.object(jarvis_mcp, "_music_room_ha", return_value=False):
            self.assertIn("nicht erreichbar", jarvis_mcp._music_room("Schlafzimmer"))
        self.assertEqual(t.call_count, 1)


SONOS = "media_player.schlafzimmer"
TRACK = "spotify:track:6MXXY2eiWkpDCezVCc0cMH"


def sonos_state(state="playing", pos=30.0, ago_s=10):
    from datetime import datetime, timedelta, timezone
    at = (datetime.now(timezone.utc) - timedelta(seconds=ago_s)).isoformat()
    return {"state": state, "attributes": {
        "media_content_id": "x-sonos-spotify:spotify%3atrack%3a6MXXY2eiWkpDCezVCc0cMH?sid=9&flags=8232&sn=2",
        "media_position": pos, "media_position_updated_at": at}}


class SonosStream(unittest.TestCase):
    """The wake-up music (or Alexa) plays on the Sonos through its own Spotify link: no Connect session,
    so Jarvis starts the same track on the soundbar at the same spot and pauses the Sonos."""
    def setUp(self):
        self.calls = []
        self.state = sonos_state()

        def ha(path, body=None):
            self.calls.append((path, body))
            return self.state if path.startswith("/api/states/") else []
        self.patches = [prof(), mock.patch.object(spotify, "configured", return_value=True),
                        mock.patch.object(jarvis_mcp.time, "sleep"), mock.patch.object(jarvis_mcp, "MUSIC_ROOM_TRIES", 3),
                        mock.patch.object(jarvis_mcp, "_ha", side_effect=ha),
                        mock.patch.object(spotify, "playing", return_value=(False, None))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_stream_found_with_position(self):
        player, track, pos = jarvis_mcp._speaker_stream("")
        self.assertEqual((player, track), (SONOS, TRACK))
        self.assertTrue(39000 <= pos <= 41500, pos)

    def test_paused_sonos_is_no_source(self):
        self.state = sonos_state("paused")
        self.assertIsNone(jarvis_mcp._speaker_stream(""))

    def test_moves_track_to_soundbar_and_pauses_sonos(self):
        with mock.patch.object(spotify, "transfer", return_value=(True, "Wohnzimmer")) as t:
            self.assertEqual(jarvis_mcp._music_room("Wohnzimmer"), "Die Musik läuft jetzt im Wohnzimmer.")
        self.assertEqual(t.call_args.args[0], "Wohnzimmer")
        self.assertEqual(t.call_args.kwargs["track"], TRACK)
        self.assertGreater(t.call_args.kwargs["position_ms"], 30000)
        self.assertIn(("/api/services/media_player/media_pause", {"entity_id": SONOS}), self.calls)

    def test_soundbar_missing_keeps_sonos_playing(self):
        with mock.patch.object(spotify, "transfer", return_value=(False, MISSING.replace("Nutzers Echo Dot", "keins"))):
            with mock.patch.object(speakers, "spotify_fallback", return_value=""):
                text = jarvis_mcp._music_room("Wohnzimmer")
        self.assertIn("Spotify kennt kein Gerät", text)
        self.assertNotIn("/api/services/media_player/media_pause", [c[0] for c in self.calls])

    def test_connect_session_wins_over_the_sonos(self):
        with mock.patch.object(spotify, "playing", return_value=(True, "abc")), \
                mock.patch.object(spotify, "transfer", return_value=(True, "Wohnzimmer")) as t:
            jarvis_mcp._music_room("Wohnzimmer")
        self.assertEqual(t.call_args.kwargs, {})

    def test_already_in_the_bedroom(self):
        with mock.patch.object(spotify, "transfer") as t:
            self.assertEqual(jarvis_mcp._music_room("Schlafzimmer"), "Die Musik läuft schon im Schlafzimmer.")
        t.assert_not_called()


if __name__ == "__main__":
    unittest.main()
