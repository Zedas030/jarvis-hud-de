"""
TV/YouTube go quiet while a living-room satellite listens and Jarvis answers, and come back exactly —
no PC sound, no network.

    .venv\\Scripts\\python.exe -m unittest tests.test_listen_duck -v
"""
import isolate  # noqa: F401  (must come first)
import time
import unittest
from unittest import mock

import listen_duck

PE = "assist_satellite.wohnzimmer_home_assistant_voice_09fdba_assist_satellit"


def settle(limit=2.0):
    deadline = time.time() + limit
    while time.time() < deadline and (listen_duck._STATE["pc"] or listen_duck._STATE["bar_muted"]):
        time.sleep(0.02)
    time.sleep(0.05)


def change(eid, old, new):
    return {"entity_id": eid, "old_state": {"state": old}, "new_state": {"state": new}}


class Fake:
    def __init__(self):
        self.vol = {1: ("chrome.exe", 0.8), 2: ("python.exe", 1.0), 3: ("spotify.exe", 0.5)}
        self.bar = {"mute": "off"}
        self.bar_calls = []

    def sessions(self):
        return [(k, n, (lambda k=k: self.vol[k][1]), (lambda v, k=k: self.vol.__setitem__(k, (self.vol[k][0], v))))
                for k, (n, _) in self.vol.items()]

    def soundbar(self, cmd, *params):
        self.bar_calls.append(cmd)
        if cmd == "GetMute":
            return f'<response result="ok"><mute>{self.bar["mute"]}</mute></response>'
        if cmd == "SetMute":
            self.bar["mute"] = params[0][1]
        return '<response result="ok"/>'


class ListenDuck(unittest.TestCase):
    def setUp(self):
        listen_duck.reset()
        self.f = Fake()
        self.talking = [False]
        self.p = [mock.patch.object(listen_duck, "_pc_sessions", self.f.sessions),
                  mock.patch.object(listen_duck, "_bar", self.f.soundbar),
                  mock.patch.object(listen_duck, "SOUNDBAR", "192.0.2.10"),
                  mock.patch.object(listen_duck, "ENABLED", True),
                  mock.patch.object(listen_duck, "GRACE_S", 0),
                  mock.patch.object(listen_duck, "QUIET_S", 0.05),
                  mock.patch.object(listen_duck, "POLL_S", 0.01),
                  mock.patch.object(listen_duck, "_jarvis_talking", lambda: self.talking[0]),
                  mock.patch.object(listen_duck, "_area", lambda sat: self.areas.get(sat, ""))]
        self.areas = {}
        for p in self.p:
            p.start()

    def tearDown(self):
        listen_duck.reset()
        for p in self.p:
            p.stop()

    def test_down_while_listening_and_back_after(self):
        self.assertEqual(listen_duck.start(PE), ["PC x2", "Soundbar stumm"])
        self.assertAlmostEqual(self.f.vol[1][1], 0.08)
        self.assertEqual(self.f.vol[2][1], 1.0, "Jarvis' own process is left alone")
        self.assertEqual(self.f.bar["mute"], "on")
        self.assertTrue(listen_duck.end(PE))
        settle()
        self.assertEqual((self.f.vol[1][1], self.f.vol[3][1]), (0.8, 0.5))
        self.assertEqual(self.f.bar["mute"], "off")

    def test_stays_quiet_while_jarvis_answers(self):
        listen_duck.start(PE)
        self.talking[0] = True
        listen_duck.end(PE)
        time.sleep(0.3)
        self.assertAlmostEqual(self.f.vol[1][1], 0.08, msg="still answering: the room stays quiet")
        self.talking[0] = False
        settle()
        self.assertEqual(self.f.vol[1][1], 0.8)

    def test_listening_again_while_answering_keeps_it_down_once(self):
        listen_duck.start(PE)
        self.talking[0] = True
        listen_duck.end(PE)
        listen_duck.start(PE)                     # he talks over the answer
        self.talking[0] = False
        time.sleep(0.3)
        self.assertAlmostEqual(self.f.vol[1][1], 0.08, msg="the first release must not undo the second listening")
        self.assertEqual(self.f.bar_calls.count("SetMute"), 1)
        listen_duck.end(PE)
        settle()
        self.assertEqual(self.f.vol[1][1], 0.8, "back to the volume before the FIRST duck")
        self.assertEqual(self.f.bar["mute"], "off")

    def test_a_soundbar_he_muted_stays_muted(self):
        self.f.bar["mute"] = "on"
        self.assertEqual(listen_duck.start(PE), ["PC x2"])
        listen_duck.end(PE)
        settle()
        self.assertEqual(self.f.bar["mute"], "on")
        self.assertNotIn("SetMute", self.f.bar_calls)

    def test_only_living_room_satellites(self):
        self.assertEqual(listen_duck.start("assist_satellite.esp32_s3_box_3_a8af8c_assist_satellit"), [])
        self.assertEqual(self.f.vol[1][1], 0.8)

    def test_a_satellite_without_the_room_in_its_id_counts_by_its_area(self):
        # 20.11.2026: the re-flashed Voice PE is assist_satellite.home_assistant_voice_0ae557_assist_satellit
        new = "assist_satellite.home_assistant_voice_0ae557_assist_satellit"
        self.assertEqual(listen_duck.start(new), [])
        self.areas[new] = "wohnzimmer"
        self.assertEqual(listen_duck.start(new), ["PC x2", "Soundbar stumm"])

    def test_soundbar_off_does_not_stop_the_pc(self):
        def off(cmd, *params):
            raise OSError("timed out")
        with mock.patch.object(listen_duck, "_bar", off):
            self.assertEqual(listen_duck.start(PE), ["PC x2"])
            self.assertTrue(listen_duck.end(PE))
            settle()
        self.assertEqual(self.f.vol[1][1], 0.8)

    def test_safety_limit_brings_it_back(self):
        self.talking[0] = True                    # even while Jarvis still talks
        with mock.patch.object(listen_duck, "MAX_S", 0.05):
            listen_duck.start(PE)
            time.sleep(0.3)
        self.assertEqual(self.f.vol[1][1], 0.8)
        self.assertEqual(self.f.bar["mute"], "off")

    def test_a_restart_in_between_is_put_right_on_the_next_start(self):
        listen_duck.start(PE)
        self.assertTrue(listen_duck.STATE_FILE.exists())
        listen_duck._STATE["timer"].cancel()
        listen_duck._STATE.update(who=set(), pc={}, bar_muted=False, timer=None)   # the process is gone
        self.assertTrue(listen_duck.recover())
        self.assertEqual((self.f.vol[1][1], self.f.vol[3][1]), (0.8, 0.5))
        self.assertEqual(self.f.bar["mute"], "off")
        self.assertFalse(listen_duck.STATE_FILE.exists())
        self.assertFalse(listen_duck.recover(), "nothing left: nothing to do")

    def test_events_run_in_order(self):
        listen_duck.on_change(change(PE, "idle", "listening"))
        listen_duck.on_change(change(PE, "listening", "processing"))
        deadline = time.time() + 2
        while listen_duck._JOBS.unfinished_tasks and time.time() < deadline:
            time.sleep(0.02)
        settle()
        self.assertEqual(self.f.vol[1][1], 0.8)
        self.assertEqual(self.f.bar_calls, ["GetMute", "SetMute", "SetMute"])
        self.assertEqual(self.f.bar["mute"], "off")


if __name__ == "__main__":
    unittest.main()
