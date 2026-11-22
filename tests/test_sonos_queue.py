"""
The rest of the playlist behind a song "Musik wandert mit" moved onto the bedroom Sonos (sonos_queue.py,
spotify.upcoming). Spotify and Home Assistant are faked; no network, no house.

    .venv\\Scripts\\python.exe -m unittest tests.test_sonos_queue -v
"""
import isolate  # noqa: F401  (must come first)
import unittest
from unittest import mock

import sonos_queue
import spotify

SONOS = "media_player.schlafzimmer_schlafzimmer"
NOW = "spotify:track:" + "A" * 22
NEXT = ["spotify:track:" + c * 22 for c in "BCD"]


def queue(current, items):
    return 200, {"currently_playing": {"uri": current}, "queue": [{"uri": u} for u in items]}


class Upcoming(unittest.TestCase):
    def test_rest_of_the_queue_without_the_song_and_repeats(self):
        items = NEXT + [NOW, NEXT[0], "spotify:episode:x"]       # short list: Spotify repeats it
        with mock.patch.object(spotify, "configured", return_value=True), \
                mock.patch.object(spotify, "_api", return_value=queue(NOW, items)):
            self.assertEqual(spotify.upcoming(), NEXT)
            self.assertEqual(spotify.upcoming(2), NEXT[:2])

    def test_nothing_playing_or_offline(self):
        with mock.patch.object(spotify, "configured", return_value=True):
            with mock.patch.object(spotify, "_api", return_value=(204, None)):
                self.assertEqual(spotify.upcoming(), [])
            with mock.patch.object(spotify, "_api", side_effect=OSError("offline")):
                self.assertEqual(spotify.upcoming(), [])


class FakeHa:
    """The Sonos starts the track after `starts_after` state reads; services are recorded."""

    def __init__(self, starts_after=1, track=NOW):
        self.reads, self.starts_after, self.track, self.calls = 0, starts_after, track, []

    def __call__(self, path, body=None):
        if path.startswith("/api/states/"):
            self.reads += 1
            on = self.reads > self.starts_after
            return {"state": "playing" if on else "paused",
                    "attributes": {"media_content_id": f"x-sonos-spotify:spotify%3atrack%3a{self.track[14:]}?sid=9"}}
        self.calls.append((path, body))
        return []


class Append(unittest.TestCase):
    def setUp(self):
        sonos_queue._RECENT.clear()
        self.p = [mock.patch.object(sonos_queue.time, "sleep"), mock.patch.object(sonos_queue, "WAIT_S", 5)]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def run_with(self, ha, tracks, ctx=""):
        with mock.patch.object(sonos_queue, "_ha", ha), \
                mock.patch.object(spotify, "upcoming", return_value=tracks), \
                mock.patch.object(spotify, "now_playing", return_value=(ctx, NOW, None)):
            return sonos_queue.append(SONOS, NOW)

    def queued(self, ha):
        return [(b["media_content_type"], b["media_content_id"]) for p, b in ha.calls if p.endswith("/play_media")]

    def test_waits_for_the_song_then_adds_the_queue_behind_it(self):
        ha = FakeHa(starts_after=2)
        self.assertEqual(self.run_with(ha, NEXT), "3 angehängt")
        self.assertEqual(self.queued(ha), [("music", u) for u in NEXT])
        self.assertTrue(all(b["enqueue"] == "add" and b["entity_id"] == SONOS for p, b in ha.calls if p.endswith("/play_media")))
        self.assertTrue(any(p.endswith("/logbook/log") for p, _ in ha.calls))

    def test_no_queue_takes_the_playlist(self):
        ha = FakeHa(starts_after=0)
        self.run_with(ha, [], ctx="spotify:playlist:xyz")
        self.assertEqual(self.queued(ha), [("playlist", "spotify:playlist:xyz")])

    def test_liked_songs_without_queue_add_nothing(self):
        ha = FakeHa(starts_after=0)
        self.run_with(ha, [], ctx="spotify:user:me:collection")
        self.assertEqual(self.queued(ha), [])

    def test_sonos_never_plays_the_song_nothing_is_added(self):
        ha = FakeHa(starts_after=10 ** 6)
        with mock.patch.object(sonos_queue.time, "time", side_effect=[0, 1, 2, 6, 7, 8]):
            self.assertIn("nicht gestartet", self.run_with(ha, NEXT))
        self.assertEqual(self.queued(ha), [])

    def test_another_song_on_the_sonos_is_not_ours(self):
        ha = FakeHa(starts_after=0, track=NEXT[2])
        with mock.patch.object(sonos_queue.time, "time", side_effect=[0, 1, 6, 7]):
            self.run_with(ha, NEXT)
        self.assertEqual(self.queued(ha), [])


class OnEvent(unittest.TestCase):
    def setUp(self):
        sonos_queue._RECENT.clear()

    def test_bad_data_and_repeats_are_ignored(self):
        with mock.patch.object(sonos_queue, "append", return_value="ok") as app, \
                mock.patch.object(sonos_queue.threading, "Thread") as th:
            th.return_value.start = lambda: th.call_args.kwargs["target"]()
            self.assertFalse(sonos_queue.on_event({"player": "light.x", "track": NOW}))
            self.assertFalse(sonos_queue.on_event({"player": SONOS, "track": "c l o s e"}))
            self.assertTrue(sonos_queue.on_event({"player": SONOS, "track": NOW}))
            self.assertFalse(sonos_queue.on_event({"player": SONOS, "track": NOW}), "HA restarted its run")
            app.assert_called_once_with(SONOS, NOW)

    def test_switched_off(self):
        with mock.patch.object(sonos_queue, "ENABLED", False):
            self.assertFalse(sonos_queue.on_event({"player": SONOS, "track": NOW}))


if __name__ == "__main__":
    unittest.main()
