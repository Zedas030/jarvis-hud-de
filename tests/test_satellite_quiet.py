"""satellite_quiet: a satellite that starts listening stops Jarvis' voice everywhere, and only then."""
import os
import sys
import time
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import alexa_voice  # noqa: E402
import ha_events  # noqa: E402
import remote_ask  # noqa: E402
import satellite_quiet  # noqa: E402
import sonos  # noqa: E402

SAT = "assist_satellite.voice_pe_assist_satellit"
ECHO = "notify.alexa_media_echo_wohnzimmer"
SONOS = "media_player.schlafzimmer"


def change(eid, old, new):
    return {"entity_id": eid, "old_state": {"state": old} if old else None, "new_state": {"state": new}}


class FakeSession:
    def __init__(self, device, busy):
        self.device, self._busy, self.stopped = device, busy, []

    def busy(self):
        return self._busy

    def stop(self, why):
        self.stopped.append(why)


class StartsListening(unittest.TestCase):
    def test_only_the_step_into_listening(self):
        self.assertEqual(satellite_quiet.starts_listening(change(SAT, "idle", "listening")), SAT)
        self.assertEqual(satellite_quiet.starts_listening(change(SAT, None, "listening")), SAT)
        for old, new in (("listening", "listening"), ("listening", "processing"), ("processing", "idle"),
                         ("idle", "responding"), ("idle", "unavailable")):
            self.assertEqual(satellite_quiet.starts_listening(change(SAT, old, new)), "", (old, new))

    def test_other_entities_never(self):
        self.assertEqual(satellite_quiet.starts_listening(change("media_player.x", "idle", "listening")), "")
        self.assertEqual(satellite_quiet.starts_listening({}), "")

    def test_ha_events_hands_satellite_changes_over(self):
        with unittest.mock.patch.object(satellite_quiet, "hush") as hush:
            ha_events._satellite(change(SAT, "idle", "listening"))
            ha_events._satellite(change("binary_sensor.door", "off", "on"))
            for _ in range(50):
                if hush.called:
                    break
                time.sleep(0.01)
        hush.assert_called_once_with(SAT)

    def test_switched_off(self):
        with unittest.mock.patch.object(satellite_quiet, "ENABLED", False), \
                unittest.mock.patch.object(satellite_quiet, "hush") as hush:
            satellite_quiet.on_change(change(SAT, "idle", "listening"))
            time.sleep(0.05)
        hush.assert_not_called()


class Hush(unittest.TestCase):
    def setUp(self):
        outs = [{"id": ECHO, "label": "Echo Wohnzimmer"}, {"id": SONOS, "label": "Sonos"}]
        for p in (unittest.mock.patch.object(sonos, "outputs", return_value=outs),
                  unittest.mock.patch.object(alexa_voice, "stop"),
                  unittest.mock.patch.object(sonos, "stop")):
            p.start()
            self.addCleanup(p.stop)

    def test_busy_engine_sessions_stop_idle_ones_not(self):
        talking, idle = FakeSession("pc", True), FakeSession("ipad", False)
        with unittest.mock.patch.object(satellite_quiet, "_engine_sessions", return_value=[talking, idle]), \
                unittest.mock.patch.object(alexa_voice, "speaking", return_value=False), \
                unittest.mock.patch.object(sonos, "speaking", return_value=False):
            stopped = satellite_quiet.hush(SAT)
        self.assertEqual(talking.stopped, [satellite_quiet.WHY])
        self.assertEqual(idle.stopped, [])
        self.assertEqual(stopped, ["engine:pc"])
        alexa_voice.stop.assert_not_called()
        sonos.stop.assert_not_called()

    def test_speakers_only_while_jarvis_talks_there(self):
        # music the user plays on the Echo or Sonos is not Jarvis talking: left alone
        with unittest.mock.patch.object(satellite_quiet, "_engine_sessions", return_value=[]), \
                unittest.mock.patch.object(alexa_voice, "speaking", side_effect=lambda e: e == ECHO), \
                unittest.mock.patch.object(sonos, "speaking", return_value=False):
            stopped = satellite_quiet.hush(SAT)
        self.assertEqual(stopped, [ECHO])
        alexa_voice.stop.assert_called_once_with(ECHO, hard=True)
        sonos.stop.assert_not_called()
        with unittest.mock.patch.object(satellite_quiet, "_engine_sessions", return_value=[]), \
                unittest.mock.patch.object(alexa_voice, "speaking", return_value=False), \
                unittest.mock.patch.object(sonos, "speaking", side_effect=lambda e: e == SONOS):
            self.assertEqual(satellite_quiet.hush(SAT), [SONOS])
        sonos.stop.assert_called_once_with(SONOS)

    def test_engine_off_does_not_break_the_rest(self):
        with unittest.mock.patch.object(satellite_quiet, "_engine_sessions", side_effect=RuntimeError("off")), \
                unittest.mock.patch.object(alexa_voice, "speaking", return_value=True), \
                unittest.mock.patch.object(sonos, "speaking", return_value=False):
            self.assertEqual(satellite_quiet.hush(SAT), [ECHO])


class RemoteAnswer(unittest.TestCase):
    def test_room_voice_and_run_of_an_earlier_satellite_question(self):
        voice = unittest.mock.Mock()
        import runtime
        with unittest.mock.patch.dict(remote_ask._ACTIVE, {"asks": 1, "voices": [voice]}), \
                unittest.mock.patch.object(runtime, "cancel_active") as cancel:
            self.assertEqual(remote_ask.hush(), 1)
        voice.cancel.assert_called_once()
        cancel.assert_called_once()

    def test_nothing_running_nothing_cancelled(self):
        import runtime
        with unittest.mock.patch.dict(remote_ask._ACTIVE, {"asks": 0, "voices": []}), \
                unittest.mock.patch.object(runtime, "cancel_active") as cancel:
            self.assertEqual(remote_ask.hush(), 0)
        cancel.assert_not_called()

    def test_cancelled_room_voice_says_nothing_more(self):
        said = []
        with unittest.mock.patch.object(remote_ask.RoomVoice, "_say", lambda self, t: said.append(t)), \
                unittest.mock.patch.object(sonos, "stop") as stop:
            v = remote_ask.RoomVoice(SONOS)
            v.cancel()
            v.add("Das ist der erste Satz.")
            left = v.finish(timeout=2)
        self.assertEqual(said, [])
        self.assertEqual(left, ["Das ist der erste Satz."])
        stop.assert_called_once_with(SONOS)


class AlexaSpeaking(unittest.TestCase):
    def tearDown(self):
        alexa_voice._BUSY.pop(ECHO, None)

    def test_speaking_while_a_sentence_should_still_play(self):
        now = 1000.0
        self.assertFalse(alexa_voice.speaking(ECHO, now))
        alexa_voice._BUSY[ECHO] = now + 3
        self.assertTrue(alexa_voice.speaking(ECHO, now))
        self.assertFalse(alexa_voice.speaking(ECHO, now + 3 + alexa_voice.LAG_S + 0.1))


class SonosSpeaking(unittest.TestCase):
    def test_open_live_stream_only(self):
        self.assertFalse(sonos.speaking(SONOS))
        live = sonos._Live(SONOS)
        with sonos._LOCK:
            sonos._LIVE_BY_SPEAKER[SONOS] = live
        try:
            self.assertTrue(sonos.speaking(SONOS))
            live.closed = True
            self.assertFalse(sonos.speaking(SONOS))
        finally:
            with sonos._LOCK:
                sonos._LIVE_BY_SPEAKER.pop(SONOS, None)


if __name__ == "__main__":
    unittest.main()
