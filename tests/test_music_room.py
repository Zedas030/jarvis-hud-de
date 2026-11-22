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


class Liked(unittest.TestCase):
    def setUp(self):
        self.patches = [prof(), mock.patch.object(spotify, "configured", return_value=True),
                        mock.patch.object(jarvis_mcp, "_speaker_stream", return_value=None),
                        mock.patch.object(spotify, "devices", return_value=[{"id": "d1", "name": "Wohnzimmer"}])]
        for p in self.patches:
            p.start()
        spotify._USER["id"] = ""

    def tearDown(self):
        for p in self.patches:
            p.stop()
        spotify._USER["id"] = ""

    def test_liked_songs_as_context_on_the_room_speaker(self):
        with mock.patch.object(spotify, "_api", return_value=(200, {"id": "u1"})), \
                mock.patch.object(spotify, "play_context", return_value=True) as play:
            self.assertEqual(jarvis_mcp._music({"action": "liked", "room": "Wohnzimmer"}),
                             "Ihre Lieblingssongs laufen jetzt im Wohnzimmer.")
        play.assert_called_once_with("spotify:user:u1:collection", "d1")

    def test_room_speaker_missing(self):
        with mock.patch.object(spotify, "play_context") as play:
            text = jarvis_mcp._music({"action": "liked", "room": "Schlafzimmer"})
        self.assertIn("nicht erreichbar", text)
        play.assert_not_called()


class LikedSoundbarFirst(unittest.TestCase):
    """19.11.2026: the liked songs in the Wohnzimmer belong on the soundbar; the Echo only when it stays away."""
    def setUp(self):
        self.patches = [prof(), mock.patch.object(spotify, "configured", return_value=True),
                        mock.patch.object(jarvis_mcp, "_speaker_stream", return_value=None),
                        mock.patch.object(jarvis_mcp.time, "sleep"), mock.patch.object(jarvis_mcp, "MUSIC_ROOM_TRIES", 3)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_soundbar_back_on_the_second_look(self):
        echo = {"id": "e1", "name": "Nutzer' Echo Dot"}
        looks = iter([[echo], [echo, {"id": "s1", "name": "Wohnzimmer"}]])
        with mock.patch.object(spotify, "devices", side_effect=lambda: next(looks)), \
                mock.patch.object(spotify, "play_liked", return_value=True) as play:
            self.assertEqual(jarvis_mcp._music({"action": "liked", "room": "Wohnzimmer"}),
                             "Ihre Lieblingssongs laufen jetzt im Wohnzimmer.")
        play.assert_called_once_with("s1")

    def test_echo_only_when_the_soundbar_stays_away(self):
        with mock.patch.object(spotify, "devices", return_value=[{"id": "e1", "name": "Nutzer' Echo Dot"}]) as devs, \
                mock.patch.object(spotify, "play_liked", return_value=True) as play:
            text = jarvis_mcp._music({"action": "liked", "room": "Wohnzimmer"})
        play.assert_called_once_with("e1")
        self.assertEqual(devs.call_count, 3)
        self.assertIn("ersatzweise auf „Nutzer' Echo Dot“", text)


class NoAlexaMusic(unittest.TestCase):
    """19.11.2026: Claude sent "spiele meine Lieblingssongs auf Spotify" to the Echo as an Alexa command.
    (tests.test_router swaps jarvis_mcp._home for a fake at import, so the check is tested on its own.)"""
    def setUp(self):
        self.p = mock.patch("jarvis_profile.ha", side_effect=lambda k, d=None: dict(PROF, tv="media_player.fire_tv").get(k, d))
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_music_command_to_the_echo_is_refused(self):
        for data in ({"media_content_type": "custom", "media_content_id": "spiele meine Lieblingssongs auf Spotify"},
                     '{"media_content_type": "custom", "media_content_id": "spiel Musik"}'):
            text = jarvis_mcp._alexa_music_refused("media_player.nutzer_echo_dot", "play_media", data)
            self.assertTrue(text.startswith("Abgelehnt"), text)
            self.assertIn("music", text)

    def test_other_commands_and_the_tv_still_go(self):
        r = jarvis_mcp._alexa_music_refused
        self.assertEqual(r("media_player.nutzer_echo_dot", "play_media",
                           {"media_content_type": "custom", "media_content_id": "wie spät ist es"}), "")
        self.assertEqual(r("media_player.fire_tv", "play_media",
                           {"media_content_type": "custom", "media_content_id": "öffne spotify"}), "")
        self.assertEqual(r("media_player.nutzer_echo_dot", "play_media",
                           {"media_content_type": "music", "media_content_id": "spotify:playlist:x"}), "")
        self.assertEqual(r("light.wohnzimmer", "turn_on", {}), "")


class Artist(unittest.TestCase):
    """19.11.2026: "Spiel alle Songs von X" — Jarvis said he could not and Nutzer had to do it himself."""
    def setUp(self):
        self.patches = [prof(), mock.patch.object(spotify, "configured", return_value=True),
                        mock.patch.object(jarvis_mcp, "_speaker_stream", return_value=None),
                        mock.patch.object(jarvis_mcp.time, "sleep"), mock.patch.object(jarvis_mcp, "MUSIC_ROOM_TRIES", 3)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_artist_as_context_on_the_soundbar(self):
        found = {"artists": {"items": [{"uri": "spotify:artist:a1", "name": "Linkin Park"},
                                       {"uri": "spotify:artist:a2", "name": "Linkin Park Tribute"}]}}
        with mock.patch.object(spotify, "devices", return_value=[{"id": "s1", "name": "Wohnzimmer"},
                                                                 {"id": "e1", "name": "Nutzer' Echo Dot"}]), \
                mock.patch.object(spotify, "_api", return_value=(200, found)) as api, \
                mock.patch.object(spotify, "play_context", return_value=True) as play:
            text = jarvis_mcp._music({"action": "artist", "query": "linkin park", "room": "Wohnzimmer"})
        self.assertEqual(text, "Linkin Park läuft jetzt im Wohnzimmer.")
        play.assert_called_once_with("spotify:artist:a1", "s1")
        self.assertIn("type=artist", api.call_args[0][1])

    def test_unknown_artist_or_none_given(self):
        with mock.patch.object(spotify, "devices", return_value=[{"id": "s1", "name": "Wohnzimmer"}]), \
                mock.patch.object(spotify, "_api", return_value=(200, {"artists": {"items": []}})), \
                mock.patch.object(spotify, "play_context") as play:
            self.assertIn("nicht gefunden", jarvis_mcp._music({"action": "artist", "query": "xyzzy", "room": "Wohnzimmer"}))
            self.assertEqual(jarvis_mcp._music({"action": "artist", "query": " "}), "Welchen Künstler soll ich spielen?")
        play.assert_not_called()
