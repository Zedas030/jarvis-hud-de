import isolate  # noqa: F401 — first: temp state, no house

import datetime as dt
import json
import os
import struct
import unittest
import urllib.error
import urllib.request
from unittest import mock

import sonos  # noqa: E402

# server.py may have imported sonos already (other test files), so the speakers are read here, not at import
with mock.patch.dict(os.environ, JARVIS_SONOS="media_player.schlafzimmer=Sonos Schlafzimmer, kaputt, media_player.bad"):
    SPEAKERS = sonos._speakers()
_PATCHES = [mock.patch.object(sonos, "SPEAKERS", SPEAKERS), mock.patch.object(sonos, "HOST", "127.0.0.1"),
            mock.patch.object(sonos, "PORT", 0)]


def setUpModule():
    for p in _PATCHES:
        p.start()


def tearDownModule():
    for p in _PATCHES:
        p.stop()


def _mp3(seconds, kbps=128):
    frame = bytes([0xFF, 0xFB, 0x90, 0x64])            # MPEG-1 layer III, index 9 = 128 kbit/s
    return frame + b"\0" * (int(seconds * kbps * 1000 / 8) - 4)


class Speakers(unittest.TestCase):
    def test_parsed_from_env(self):
        self.assertEqual(sonos.SPEAKERS, [
            {"id": "media_player.schlafzimmer", "label": "Sonos Schlafzimmer"},
            {"id": "media_player.bad", "label": "Sonos Bad"}])

    def test_off_without_home_assistant(self):
        self.assertFalse(sonos.configured())
        self.assertEqual(sonos.output_for("8c2fcc86"), "")
        self.assertEqual(sonos.status("pc")["speakers"], [])


@mock.patch.object(sonos, "HA_TOKEN", "t")
class PerDevice(unittest.TestCase):
    def test_each_device_keeps_its_own(self):
        sonos.set_output("8c2fcc86", "media_player.schlafzimmer")
        self.assertEqual(sonos.output_for("8c2fcc86"), "media_player.schlafzimmer")
        self.assertEqual(sonos.output_for("pc"), "")
        sonos.set_output("8c2fcc86", "")
        self.assertEqual(sonos.output_for("8c2fcc86"), "")

    def test_unknown_speaker_refused(self):
        with self.assertRaises(ValueError):
            sonos.set_output("pc", "media_player.fire_tv")


class Duration(unittest.TestCase):
    def test_mp3_from_bitrate(self):
        self.assertAlmostEqual(sonos.duration(_mp3(4.0), "audio/mpeg"), 4.0, places=1)

    def test_mp3_after_id3_tag(self):
        tag = b"ID3\x04\0\0\0\0\0\x0a" + b"\0" * 10
        self.assertAlmostEqual(sonos.duration(tag + _mp3(2.0), "audio/mpeg"), 2.0, places=1)

    def test_wav_from_header(self):
        head = b"RIFF" + b"\0" * 24 + struct.pack("<I", 48000) + b"\0" * 12
        self.assertAlmostEqual(sonos.duration(head + b"\0" * 96000, "audio/wav"), 2.0, places=2)

    def test_unknown_falls_back_to_text(self):
        self.assertAlmostEqual(sonos.duration(b"xx", "audio/ogg", "a" * 140), 10.5)


@mock.patch.object(sonos, "HA_TOKEN", "t")
class Play(unittest.TestCase):
    def test_announces_through_home_assistant_and_serves_the_clip_once_named(self):
        calls = []
        with mock.patch.object(sonos, "_ha", side_effect=lambda path, body: calls.append((path, body))), \
                mock.patch.object(sonos, "_late", return_value=False):
            secs = sonos.play("media_player.schlafzimmer", _mp3(3.0), "audio/mpeg", "Hallo")
        path, body = calls[0]
        self.assertEqual(path, "/api/services/media_player/play_media")
        self.assertTrue(body["announce"])
        self.assertEqual(body["extra"]["volume"], round(sonos.VOLUME * 100))     # Sonos wants 0-100
        self.assertAlmostEqual(secs, 3.0 + sonos.LEAD_S, places=1)
        url = body["media_content_id"]
        self.assertRegex(url, r"/sonos/[0-9a-f]{32}\.mp3$")
        with urllib.request.urlopen(url, timeout=5) as r:
            self.assertEqual(r.headers["Content-Type"], "audio/mpeg")
            self.assertEqual(len(r.read()), len(_mp3(3.0)))
        guess = url.rsplit("/", 1)[0] + "/" + "0" * 32 + ".mp3"
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(guess, timeout=5)
        self.assertEqual(e.exception.code, 404)

    def test_quiet_hours_are_quieter(self):
        calls = []
        with mock.patch.object(sonos, "_ha", side_effect=lambda path, body: calls.append(body)):
            sonos.play("media_player.schlafzimmer", _mp3(1.0), "audio/mpeg", quiet=True)
        self.assertEqual(calls[0]["extra"]["volume"], round(sonos.NIGHT_VOLUME * 100))

    def test_softer_from_ten_in_the_evening(self):
        # 19.11.2026: at 23:27, before "Gute Nacht", the day volume was far too loud in the bedroom
        day, late, night = dt.datetime(2026, 11, 19, 21, 59), dt.datetime(2026, 11, 19, 22, 0), dt.datetime(2026, 11, 20, 6, 30)
        with mock.patch.object(sonos, "VOLUME", 0.63), mock.patch.object(sonos, "LATE_VOLUME", 0.35), \
                mock.patch.object(sonos, "NIGHT_VOLUME", 0.1), mock.patch.object(sonos, "LATE_FROM", 22):
            self.assertEqual(sonos.voice_volume(now=day), 0.63)
            self.assertEqual(sonos.voice_volume(now=late), 0.35)
            self.assertEqual(sonos.voice_volume(now=night), 0.35)
            self.assertEqual(sonos.voice_volume(quiet=True, now=late), 0.1)          # after "Gute Nacht"
            self.assertEqual(sonos.voice_volume(now=dt.datetime(2026, 11, 20, 7)), 0.63)
            with mock.patch.object(sonos, "LATE_VOLUME", 0.9):
                self.assertEqual(sonos.voice_volume(now=late), 0.63)                  # never louder than the day
            with mock.patch.object(sonos, "LATE_VOLUME", 0):
                self.assertEqual(sonos.voice_volume(now=late), 0.63)                  # 0 = off

    def test_volume_as_percent(self):
        self.assertEqual((sonos._percent(0.35), sonos._percent(40), sonos._percent(0.0), sonos._percent(3)),
                         (35, 40, 1, 3))

    def test_unknown_speaker_refused(self):
        with self.assertRaises(ValueError):
            sonos.play("media_player.fire_tv", b"", "audio/mpeg")


@mock.patch.object(sonos, "HA_TOKEN", "t")
@mock.patch.object(sonos, "LIVE_IDLE_S", 0.3)
class Live(unittest.TestCase):
    EID = "media_player.schlafzimmer"

    def test_silent_frame_matches_the_voice(self):
        bps, silence = sonos._mp3_frame(_mp3(0.1))
        self.assertEqual(bps, 128000)
        self.assertEqual(len(silence), 417)                 # 144 * 128000 / 44100
        self.assertEqual(silence[:2], b"\xff\xfb")

    def test_sentences_join_one_announcement_and_the_stream_ends_when_idle(self):
        calls = []
        with mock.patch.object(sonos, "_ha", side_effect=lambda path, body=None: calls.append(body)):
            first = sonos.say(self.EID, iter([_mp3(0.2)]), "audio/mpeg")
            sonos.say(self.EID, iter([_mp3(0.1)]), "audio/mpeg")
            self.assertEqual(len(calls), 1, "one announcement for both sentences")
            url = calls[0]["media_content_id"]
            self.assertRegex(url, r"/sonos/live/[0-9a-f]{32}\.mp3$")
            self.assertGreater(first, 0.2)
            with urllib.request.urlopen(url, timeout=10) as r:
                got = r.read()                               # returns once the stream has gone idle
        self.assertGreaterEqual(len(got), len(_mp3(0.2)) + len(_mp3(0.1)))
        self.assertTrue(got.startswith(_mp3(0.2)[:4]))
        with mock.patch.object(sonos, "_ha", side_effect=lambda path, body=None: calls.append(body)):
            sonos.say(self.EID, iter([_mp3(0.1)]), "audio/mpeg")
        self.assertEqual(len(calls), 2, "after the stream ended, a new announcement")

    def test_paced_and_a_stop_cuts_it_short(self):
        calls = []
        with mock.patch.object(sonos, "_ha", side_effect=lambda path, body=None: calls.append(body)):
            sonos.say(self.EID, iter([_mp3(4.0)]), "audio/mpeg")
        got = bytearray()
        import threading
        import time as _t

        def read():
            with urllib.request.urlopen(calls[0]["media_content_id"], timeout=10) as r:
                while True:
                    b = r.read(1024)
                    if not b:
                        return
                    got.extend(b)
        reader = threading.Thread(target=read)
        reader.start()
        _t.sleep(0.4)
        sonos.stop(self.EID, hold=True)
        reader.join(5)
        self.assertFalse(reader.is_alive())
        # about LIVE_AHEAD_S + 0.4 s went out, not the whole 4 s
        self.assertLess(len(got), len(_mp3(sonos.LIVE_AHEAD_S + 1.0)))
        self.assertTrue(sonos.hushed(self.EID))
        sonos.stop(self.EID)
        self.assertFalse(sonos.hushed(self.EID))

    def test_announcement_failing_hands_the_sound_back(self):
        def boom(path, body=None):
            raise OSError("house away")
        with mock.patch.object(sonos, "_ha", side_effect=boom):
            with self.assertRaises(sonos.Fallback) as e:
                sonos.say("media_player.bad", iter([_mp3(0.1), b"rest"]), "audio/mpeg")
        self.assertEqual(e.exception.data, _mp3(0.1) + b"rest")

    def test_other_formats_go_as_one_clip(self):
        with mock.patch.object(sonos, "play", return_value=2.5) as play:
            self.assertEqual(sonos.say(self.EID, iter([b"RIFF", b"data"]), "audio/wav"), 2.5)
        self.assertEqual(play.call_args.args[1], b"RIFFdata")


class FakeSpeaker:
    """Home Assistant as far as hold/release see it: one state, volume_set changes it."""
    def __init__(self, state="playing", volume=0.5):
        self.state, self.volume, self.sets = state, volume, []

    def __call__(self, path, body=None):
        if body is None:
            return json.dumps({"state": self.state, "attributes": {"volume_level": self.volume}}).encode()
        self.sets.append(body["volume_level"])
        self.volume = body["volume_level"]
        return b"[]"


@mock.patch.object(sonos, "HA_TOKEN", "t")
class Hold(unittest.TestCase):
    EID = "media_player.schlafzimmer"

    def run_with(self, fake, *steps):
        with mock.patch.object(sonos, "_ha", fake):
            for step in steps:
                step(self.EID)
        return fake

    def test_down_and_back(self):
        fake = self.run_with(FakeSpeaker(), sonos.hold, sonos.release)
        self.assertEqual(fake.sets, [sonos.QUIET, 0.5])

    def test_second_hold_keeps_the_first_volume(self):
        fake = self.run_with(FakeSpeaker(), sonos.hold, sonos.hold, sonos.release)
        self.assertEqual(fake.sets, [sonos.QUIET, 0.5])

    def test_nothing_playing_nothing_touched(self):
        fake = self.run_with(FakeSpeaker(state="paused"), sonos.hold, sonos.release)
        self.assertEqual(fake.sets, [])

    def test_already_quiet_stays(self):
        fake = self.run_with(FakeSpeaker(volume=0.05), sonos.hold, sonos.release)
        self.assertEqual(fake.sets, [])

    def test_turned_by_hand_meanwhile_is_kept(self):
        fake = FakeSpeaker()
        with mock.patch.object(sonos, "_ha", fake):
            sonos.hold(self.EID)
            fake.volume = 0.3
            sonos.release(self.EID)
        self.assertEqual(fake.sets, [sonos.QUIET])

    def test_unknown_speaker_ignored(self):
        fake = self.run_with(FakeSpeaker(), lambda e: sonos.hold("media_player.fire_tv"))
        self.assertEqual(fake.sets, [])


if __name__ == "__main__":
    unittest.main()
