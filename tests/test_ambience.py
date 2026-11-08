import isolate  # noqa: F401 — first: temp state, no house

import time
import unittest
from unittest.mock import patch

import ambience


def player(eid, state="playing", **attrs):
    return {"entity_id": f"media_player.{eid}", "state": state, "attributes": attrs}


class Classify(unittest.TestCase):
    def test_nothing_playing(self):
        self.assertEqual(ambience.classify([player("tv", "idle"), {"entity_id": "light.flur", "state": "on"}]),
                         ("", []))

    def test_sport_by_title_or_app(self):
        self.assertEqual(ambience.classify([player("tv", friendly_name="Fernseher",
                                                   media_title="Bundesliga: Bayern – Dortmund")])[0], "sport")
        self.assertEqual(ambience.classify([player("tv", app_name="DAZN")])[0], "sport")

    def test_music(self):
        self.assertEqual(ambience.classify([player("kueche", media_content_type="music")])[0], "music")
        self.assertEqual(ambience.classify([player("sonos", app_name="Spotify")])[0], "music")

    def test_video_without_hints_is_tv(self):
        self.assertEqual(ambience.classify([player("tv", app_name="Netflix", media_title="Dark")])[0], "tv")

    def test_sport_wins_and_names_listed(self):
        kind, names = ambience.classify([player("kueche", friendly_name="Küche", media_content_type="music"),
                                         player("tv", friendly_name="TV", media_channel="Sky Sport Bundesliga")])
        self.assertEqual(kind, "sport")
        self.assertEqual(names, ["Küche", "TV"])

    def test_muted_and_ignored_players_do_not_count(self):
        self.assertEqual(ambience.classify([player("tv", is_volume_muted=True, app_name="DAZN")])[0], "")
        old = ambience.IGNORE
        ambience.IGNORE = __import__("re").compile("handy", __import__("re").I)
        try:
            self.assertEqual(ambience.classify([player("handy_nutzer", media_content_type="music")])[0], "")
        finally:
            ambience.IGNORE = old


class Status(unittest.TestCase):
    def setUp(self):
        ambience._STATE.update(kind="", players=[], at=0.0)

    def test_stale_answer_counts_as_nothing(self):
        ambience._STATE.update(kind="music", players=["Küche"], at=time.time() - ambience.STALE_S - 1)
        self.assertEqual(ambience.status(), {"kind": "", "players": []})
        ambience._STATE.update(at=time.time())
        self.assertEqual(ambience.status(), {"kind": "music", "players": ["Küche"]})

    def test_no_token_means_nothing_playing(self):
        ambience.step()        # isolate.py clears HA_TOKEN: no request, no sound
        self.assertEqual(ambience.status()["kind"], "")

    def test_ha_music_is_hidden_when_spotify_account_is_not_playing(self):
        import briefing
        import spotify
        with patch.object(briefing, "_ha_states", return_value=[player("echo", media_content_type="music")]), \
             patch.object(spotify, "configured", return_value=True), \
             patch.object(spotify, "active_device", return_value=(False, {"id": "echo"})):
            ambience.step()
        self.assertEqual(ambience.status(), {"kind": "", "players": []})

    def test_spotify_playback_is_shown_even_without_a_house_player(self):
        import briefing
        import spotify
        with patch.object(briefing, "_ha_states", return_value=[]), \
             patch.object(spotify, "configured", return_value=True), \
             patch.object(spotify, "active_device", return_value=(True, {"id": "echo", "type": "Speaker"})):
            ambience.step()
        self.assertEqual(ambience.status(), {"kind": "music", "players": ["Spotify"]})


    def _step(self, states, on, dev_type):
        import briefing
        import spotify
        with patch.object(briefing, "_ha_states", return_value=states),              patch.object(spotify, "configured", return_value=True),              patch.object(spotify, "active_device", return_value=(on, {"id": "x", "type": dev_type})):
            ambience.step()
        return ambience.status()

    def test_phone_on_the_road_is_not_music_at_home(self):
        states = [player("spotify_konto", friendly_name="Spotify", source="iPhone", media_artist="X"),
                  {"entity_id": "person.nutzer", "state": "not_home", "attributes": {}}]
        self.assertEqual(self._step(states, True, "Smartphone"), {"kind": "", "players": []})

    def test_phone_at_home_counts(self):
        states = [{"entity_id": "person.nutzer", "state": "home", "attributes": {}}]
        self.assertEqual(self._step(states, True, "Smartphone"), {"kind": "music", "players": ["Spotify"]})

    def test_home_speaker_counts_even_when_nobody_is_home(self):
        states = [{"entity_id": "person.nutzer", "state": "not_home", "attributes": {}}]
        self.assertEqual(self._step(states, True, "Speaker")["kind"], "music")

    def test_spotify_account_entity_is_not_a_room_player(self):
        self.assertEqual(ambience.classify([player("spotify_konto", source="iPhone", media_artist="X")]), ("", []))


class DeviceLog(unittest.TestCase):
    def test_one_line_per_report_and_trimmed(self):
        import wakeword
        old = wakeword.DEVICE_LOG_KEEP
        wakeword.DEVICE_LOG_KEEP = 3
        try:
            for i in range(5):
                wakeword.device_log("iPad", f"verworfen · Wert 0.0{i}\nzweite Zeile")
            lines = wakeword.DEVICE_LOG.read_text(encoding="utf-8").splitlines()
        finally:
            wakeword.DEVICE_LOG_KEEP = old
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[-1].endswith("iPad  verworfen · Wert 0.04 zweite Zeile"))


if __name__ == "__main__":
    unittest.main()
