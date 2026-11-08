import isolate  # noqa: F401 — first: temp state, no house

import json
import unittest

import spotify


class FakePlayer:
    """Spotify's player endpoints as far as spotify.py uses them, on one phone."""

    def __init__(self, playing=True, device="phone"):
        self.playing, self.device, self.calls = playing, device, []

    def __call__(self, method, path, retry=True):
        self.calls.append((method, path.split("?")[0]))
        if path == "/me/player":
            if self.device is None:
                return 204, None
            return 200, {"is_playing": self.playing, "device": {"id": self.device}}
        if path.startswith("/me/player/pause"):
            self.playing = False
        elif path.startswith("/me/player/play"):
            self.playing = True
        elif path.startswith(("/me/player/next", "/me/player/previous")):
            self.playing = True                    # Spotify starts the new track
        return 204, None


class Hold(unittest.TestCase):
    def setUp(self):
        self.saved = (spotify._api, spotify.CLIENT_ID)
        spotify.CLIENT_ID = "test"
        spotify.save_token({"refresh_token": "r"})
        spotify.forget()
        self.player = spotify._api = FakePlayer()

    def tearDown(self):
        spotify._api, spotify.CLIENT_ID = self.saved
        spotify.forget()
        spotify.TOKEN_FILE.unlink(missing_ok=True)

    def test_wake_word_pauses_and_plays_on(self):
        self.assertTrue(spotify.hold())
        self.assertFalse(self.player.playing)
        self.assertTrue(spotify.release())
        self.assertTrue(self.player.playing)

    def test_nothing_playing_nothing_to_resume(self):
        self.player.playing = False
        self.assertFalse(spotify.hold())
        self.assertFalse(spotify.release())
        self.assertFalse(self.player.playing)

    def test_music_off_during_the_hold_stays_off(self):
        spotify.hold()
        self.assertTrue(spotify.control("pause"))
        spotify.release()
        self.assertFalse(self.player.playing)

    def test_toggle_during_the_hold_means_pause(self):
        spotify.hold()
        spotify.control("play_pause")
        spotify.release()
        self.assertFalse(self.player.playing)

    def test_next_track_during_the_hold_plays(self):
        spotify.hold()
        self.assertTrue(spotify.control("next"))
        self.assertTrue(self.player.playing)
        self.player.calls.clear()
        spotify.release()
        self.assertNotIn(("PUT", "/me/player/play"), self.player.calls)

    def test_music_started_elsewhere_is_left_alone(self):
        spotify.hold()
        self.player.playing, self.player.device = True, "pc"
        self.player.calls.clear()
        self.assertTrue(spotify.release())
        self.assertEqual(self.player.calls, [("GET", "/me/player")])

    def test_stale_playing_on_the_paused_phone_still_plays(self):
        spotify.hold()
        self.player.playing = True               # what the iPhone claimed while it was silent
        self.player.calls.clear()
        self.assertTrue(spotify.release())
        self.assertIn(("PUT", "/me/player/play"), self.player.calls)

    def test_hold_outlives_a_restart(self):
        spotify.hold("PC")
        spotify._HELD.update(device=None, at=0.0)  # what a fresh process knows
        spotify._restore()
        self.assertTrue(spotify.release("PC", "denkt"))
        self.assertTrue(self.player.playing)
        self.assertFalse(spotify.HELD_FILE.exists())
        self.assertIn("PC: weiter nach", spotify.LOG.read_text(encoding="utf-8"))

    def test_off_switch_and_no_login(self):
        spotify.HOLD = False
        try:
            self.assertFalse(spotify.hold())
        finally:
            spotify.HOLD = True
        spotify.TOKEN_FILE.unlink()
        self.assertFalse(spotify.hold())
        self.assertFalse(spotify.control("pause"))
        self.assertTrue(self.player.playing)


class LoudPlayer(FakePlayer):
    """A device Spotify can set the volume of (phone, PC, Connect speaker)."""

    def __init__(self, volume=60, **kw):
        super().__init__(**kw)
        self.volume = volume

    def __call__(self, method, path, retry=True):
        if path == "/me/player" and self.device is not None:
            self.calls.append((method, path))
            return 200, {"is_playing": self.playing,
                         "device": {"id": self.device, "volume_percent": self.volume, "supports_volume": True}}
        if path.startswith("/me/player/volume"):
            self.calls.append((method, "/me/player/volume"))
            self.volume = int(path.split("volume_percent=")[1].split("&")[0])
            return 204, None
        return super().__call__(method, path, retry)


class RoundedLoudPlayer(LoudPlayer):
    """Some Spotify Connect devices report a neighboring integer after setting volume."""

    def __call__(self, method, path, retry=True):
        if path.startswith("/me/player/volume"):
            self.calls.append((method, "/me/player/volume"))
            requested = int(path.split("volume_percent=")[1].split("&")[0])
            self.volume = max(0, requested - 1) if requested == spotify.quiet_target(60) else requested
            return 204, None
        return super().__call__(method, path, retry)


class Quiet(unittest.TestCase):
    """JARVIS_SPOTIFY_HOLD_MODE=leise: down to the lowest step instead of a pause, and back after."""

    def setUp(self):
        self.saved = (spotify._api, spotify.CLIENT_ID, spotify.HOLD_MODE)
        spotify.CLIENT_ID, spotify.HOLD_MODE = "test", "leise"
        spotify.save_token({"refresh_token": "r"})
        spotify.forget()
        self.player = spotify._api = LoudPlayer()

    def tearDown(self):
        spotify._api, spotify.CLIENT_ID, spotify.HOLD_MODE = self.saved
        spotify.forget()
        spotify.TOKEN_FILE.unlink(missing_ok=True)

    def test_quiet_not_paused_and_back(self):
        self.assertTrue(spotify.hold("PC"))
        self.assertTrue(self.player.playing)
        self.assertEqual(self.player.volume, spotify.quiet_target(60))
        self.assertNotIn(("PUT", "/me/player/pause"), self.player.calls)
        self.assertTrue(spotify.release("PC"))
        self.assertEqual(self.player.volume, 60)
        self.assertNotIn(("PUT", "/me/player/play"), self.player.calls)

    def test_volume_changed_by_hand_stays(self):
        spotify.hold()
        self.player.volume = 35
        spotify.release()
        self.assertEqual(self.player.volume, 35)

    def test_one_percent_rounding_still_restores_old_volume(self):
        self.player = spotify._api = RoundedLoudPlayer()
        self.assertTrue(spotify.hold("PC"))
        self.assertEqual(self.player.volume, spotify.quiet_target(60) - 1)
        self.assertEqual(spotify._HELD["quiet"], spotify.quiet_target(60))
        self.assertTrue(spotify.release("PC"))
        self.assertEqual(self.player.volume, 60)

    def test_stale_old_volume_at_release_reasserts_saved_value(self):
        self.assertTrue(spotify.hold("PC"))
        self.player.calls.clear()
        self.player.volume = 60  # Connect may still report the pre-duck volume for a while.
        self.assertTrue(spotify.release("PC"))
        self.assertEqual(self.player.volume, 60)
        self.assertIn(("PUT", "/me/player/volume"), self.player.calls)

    def test_legacy_hold_without_quiet_snapshot_restores(self):
        self.assertTrue(spotify.hold("PC"))
        held = json.loads(spotify.HELD_FILE.read_text(encoding="utf-8"))
        held.pop("quiet", None)
        spotify.HELD_FILE.write_text(json.dumps(held), encoding="utf-8")
        self.player.volume = 1  # The earlier version ducked to 1% and did not save that target.
        spotify._HELD.update(device=None, at=0.0, volume=None, quiet=None)
        spotify._restore()
        self.assertTrue(spotify.release("PC"))
        self.assertEqual(self.player.volume, 60)

    def test_music_off_during_the_hold_gets_its_volume_back(self):
        spotify.hold()
        self.assertTrue(spotify.control("pause"))
        self.assertFalse(self.player.playing)
        self.assertEqual(self.player.volume, 60)
        spotify.release()
        self.assertFalse(self.player.playing)

    def test_toggle_during_the_hold_still_pauses(self):
        spotify.hold()
        spotify.control("play_pause")
        self.assertFalse(self.player.playing)

    def test_survives_a_restart(self):
        spotify.hold()
        spotify._HELD.update(device=None, at=0.0, volume=None)
        spotify._restore()
        spotify.release()
        self.assertEqual(self.player.volume, 60)

    def test_device_without_volume_is_paused(self):
        spotify._api = self.player = FakePlayer()
        self.assertTrue(spotify.hold())
        self.assertFalse(self.player.playing)
        self.assertTrue(spotify.release())
        self.assertTrue(self.player.playing)

    def test_low_volume_is_still_turned_down(self):
        # 15 % was the living room speaker's normal level, below the old fixed 20 %: nothing happened
        self.player.volume = 15
        self.assertTrue(spotify.hold("PC"))
        self.assertEqual(self.player.volume, spotify.quiet_target(15))
        self.assertLess(self.player.volume, 15)
        self.assertTrue(spotify.release("PC"))
        self.assertEqual(self.player.volume, 15)

    def test_loud_music_is_capped(self):
        self.player.volume = 90
        spotify.hold("PC")
        self.assertEqual(self.player.volume, spotify.QUIET_PCT)

    def test_very_quiet_music_is_left_alone(self):
        self.player.volume = 1
        self.assertFalse(spotify.hold("PC"))
        self.assertEqual(self.player.volume, 1)

    def test_target(self):
        self.assertEqual(spotify.quiet_target(15), 4)
        self.assertEqual(spotify.quiet_target(60), 18)
        self.assertEqual(spotify.quiet_target(100), spotify.QUIET_PCT)
        self.assertEqual(spotify.quiet_target(2), 1)

    def test_pause_mode_still_pauses(self):
        spotify.HOLD_MODE = "pause"
        spotify.hold()
        self.assertFalse(self.player.playing)
        self.assertEqual(self.player.volume, 60)


if __name__ == "__main__":
    unittest.main()


class Transfer(unittest.TestCase):
    """The music into another room: Spotify Connect, device found by its name."""

    DEVICES = [{"id": "echo", "name": "Nutzer' Echo Dot", "type": "Speaker", "is_active": False},
               {"id": "wz", "name": "Wohnzimmer", "type": "Unknown", "is_active": False},
               {"id": "pc", "name": "NUTZER", "type": "Computer", "is_active": False}]

    def setUp(self):
        self.saved = (spotify._api, spotify.CLIENT_ID)
        spotify.CLIENT_ID = "test"
        spotify.save_token({"refresh_token": "r"})
        spotify.forget()
        self.calls = []
        spotify._api = self.api

    def tearDown(self):
        spotify._api, spotify.CLIENT_ID = self.saved
        spotify.forget()

    def api(self, method, path, retry=True, body=None):
        self.calls.append((method, path.split("?")[0], body))
        if path == "/me/player/devices":
            return 200, {"devices": list(self.DEVICES)}
        if path == "/me/player":                  # the Sonos plays, but Spotify leaves it out of the device list
            return 200, {"is_playing": True, "device": {"id": "sz", "name": "Schlafzimmer"}}
        return 204, None

    def test_active_device_missing_from_the_list_is_still_known(self):
        self.assertEqual([d["name"] for d in spotify.devices()][-1], "Schlafzimmer")
        self.assertEqual(spotify.find_device("schlafzimmer")["id"], "sz")

    def test_exact_name_beats_substring(self):
        self.assertEqual(spotify.find_device("Wohnzimmer")["id"], "wz")
        self.assertEqual(spotify.find_device("Echo")["id"], "echo")
        self.assertIsNone(spotify.find_device("Bad"))

    def test_transfer_moves_and_plays(self):
        self.assertEqual(spotify.transfer("Wohnzimmer"), (True, "Wohnzimmer"))
        self.assertIn(("PUT", "/me/player", {"device_ids": ["wz"], "play": True}), self.calls)

    def test_unknown_device_names_the_reachable_ones(self):
        ok, text = spotify.transfer("Bad")
        self.assertFalse(ok)
        self.assertIn("Wohnzimmer", text)
        self.assertFalse([c for c in self.calls if c[:2] == ("PUT", "/me/player")])

    def test_wake_word_hold_is_forgotten_and_old_room_gets_its_volume_back(self):
        spotify._HELD.update(device="sz", at=1.0, volume=40, quiet=12)
        self.assertTrue(spotify.transfer("Wohnzimmer")[0])
        self.assertIn(("PUT", "/me/player/volume", None), self.calls)
        self.assertIsNone(spotify._HELD["device"])


class RoomViaHomeAssistant(unittest.TestCase):
    """A Sonos that Spotify does not list right now gets the playlist through Home Assistant."""

    def setUp(self):
        import jarvis_mcp
        import speakers
        from unittest import mock
        self.saved = (spotify._api, spotify.CLIENT_ID)
        spotify.CLIENT_ID = "test"
        spotify.save_token({"refresh_token": "r"})
        self.calls, self.ha = [], []
        spotify._api = self.api
        self.patches = [
            mock.patch.object(speakers, "_cfg", return_value=({"Schlafzimmer": {"player": "media_player.sz"},
                                                               "Wohnzimmer": {"notify": "notify.echo"}}, None)),
            mock.patch.object(jarvis_mcp, "_ha", side_effect=lambda path, body=None: self.ha.append((path, body))),
            mock.patch.object(jarvis_mcp, "_speaker_stream", return_value=None)]   # no Sonos streams by itself here
        for p in self.patches:
            p.start()
        self.music = jarvis_mcp._music

    def tearDown(self):
        for p in self.patches:
            p.stop()
        spotify._api, spotify.CLIENT_ID = self.saved
        spotify.forget()

    def api(self, method, path, retry=True, body=None):
        self.calls.append((method, path.split("?")[0], body))
        if path == "/me/player/devices":
            return 200, {"devices": [{"id": "wz", "name": "Wohnzimmer"}]}
        if path == "/me/player":
            return 200, {"is_playing": True, "device": {"id": "wz", "name": "Wohnzimmer"},
                         "context": {"uri": "spotify:user:spotify:playlist:37i9"}, "item": {"uri": "spotify:track:t1"}}
        return 204, None

    def test_listed_device_goes_through_spotify_connect(self):
        text = self.music({"action": "transfer", "room": "Wohnzimmer"})
        self.assertEqual(text, "Die Musik läuft jetzt im Wohnzimmer.")
        self.assertEqual(self.ha, [])

    def test_unlisted_sonos_gets_the_playlist_from_home_assistant(self):
        text = self.music({"action": "transfer", "room": "Schlafzimmer"})
        self.assertIn("im Schlafzimmer", text)
        self.assertEqual(self.ha, [("/api/services/media_player/play_media",
                                    {"entity_id": "media_player.sz", "media_content_type": "music",
                                     "media_content_id": "spotify:playlist:37i9", "enqueue": "replace"})])
        self.assertIn(("PUT", "/me/player/pause", None), self.calls)      # not playing twice

    def test_unknown_room_without_player_is_refused(self):
        text = self.music({"action": "transfer", "room": "Bad"})
        self.assertIn("kennt kein Gerät", text)
        self.assertEqual(self.ha, [])
