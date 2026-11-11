"""
Sign of life for the HA watchdog (heartbeat.py) — HA and the own server faked; nothing leaves the PC.

    .venv\\Scripts\\python.exe -m unittest tests.test_heartbeat -v
"""
import isolate  # noqa: F401  (must come first)
import json
import os
import unittest
import urllib.error
from unittest import mock

import heartbeat


class FakeResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Heartbeat(unittest.TestCase):
    def setUp(self):
        heartbeat.STATE.update(last_ok=0.0, last_error="", fails=0, running=False)
        for k, v in (("HA_URL", "http://ha.example.local:8123"), ("HA_TOKEN", "t"),
                     ("ENTITY", "input_datetime.jarvis_lebenszeichen")):
            p = mock.patch.object(heartbeat, k, v)
            p.start()
            self.addCleanup(p.stop)

    def env(self, **kw):
        p = mock.patch.dict(os.environ, kw)
        p.start()
        self.addCleanup(p.stop)

    def test_live_port_on_test_port_off(self):
        self.env(JARVIS_HEARTBEAT="")
        self.assertTrue(heartbeat.enabled(8720))
        self.assertFalse(heartbeat.enabled(8721))     # a test instance must not cover for a dead live one

    def test_switches(self):
        self.env(JARVIS_HEARTBEAT="0")
        self.assertFalse(heartbeat.enabled(8720))
        self.env(JARVIS_HEARTBEAT="1")
        self.assertTrue(heartbeat.enabled(8721))
        with mock.patch.object(heartbeat, "HA_TOKEN", ""):
            self.assertFalse(heartbeat.enabled(8720))

    def test_stamp_posts_timestamp(self):
        seen = {}

        def fake(req, timeout=0):
            seen.update(url=req.full_url, body=json.loads(req.data), auth=req.headers.get("Authorization"))
            return FakeResp()
        with mock.patch("urllib.request.urlopen", fake):
            self.assertTrue(heartbeat.stamp(now=1762770000))
        self.assertTrue(seen["url"].endswith("/api/services/input_datetime/set_datetime"))
        self.assertEqual(seen["body"], {"entity_id": "input_datetime.jarvis_lebenszeichen", "timestamp": 1762770000})
        self.assertEqual(seen["auth"], "Bearer t")

    def test_no_stamp_when_own_server_is_stuck(self):
        with mock.patch.object(heartbeat, "self_answers", return_value=False), \
                mock.patch.object(heartbeat, "stamp") as st:
            self.assertFalse(heartbeat.beat(8720))
        st.assert_not_called()

    def test_http_error_from_own_server_still_counts(self):
        err = urllib.error.HTTPError("u", 404, "nf", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertTrue(heartbeat.self_answers(8720))
        with mock.patch("urllib.request.urlopen", side_effect=ConnectionRefusedError()):
            self.assertFalse(heartbeat.self_answers(8720))

    def test_ha_down_counts_and_recovers(self):
        with mock.patch.object(heartbeat, "self_answers", return_value=True), \
                mock.patch.object(heartbeat, "stamp", side_effect=OSError("timed out")):
            for _ in range(4):
                self.assertFalse(heartbeat.beat(8720))
        self.assertEqual(heartbeat.STATE["fails"], 4)
        with mock.patch.object(heartbeat, "self_answers", return_value=True), \
                mock.patch.object(heartbeat, "stamp", return_value=True):
            self.assertTrue(heartbeat.beat(8720))
        self.assertEqual(heartbeat.STATE["fails"], 0)
        self.assertGreater(heartbeat.STATE["last_ok"], 0)


if __name__ == "__main__":
    unittest.main()
