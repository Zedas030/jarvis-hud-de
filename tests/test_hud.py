"""
HUD data: notification centre (/api/notifications) and the PC row of the dashboard.

    .venv\\Scripts\\python.exe -m unittest tests.test_hud -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import emergency   # noqa: E402
import events      # noqa: E402
import proposals   # noqa: E402
import server      # noqa: E402


class Notifications(unittest.TestCase):
    def tearDown(self):
        emergency.resume()

    def test_shape_and_content(self):
        events.publish("say", "Die Waschmaschine ist fertig.", kind="appliance")
        pid = proposals.add("idea", "Schlaf-Ampel", "Sie fragen oft nach dem Schlaf.", source="suggestions")
        emergency.pause(hours=1)
        n = server.notifications()
        self.assertEqual(set(n), {"items", "incidents", "proposals", "paused_until", "persona", "tasks"})
        self.assertEqual(n["items"][0]["text"], "Die Waschmaschine ist fertig.")
        self.assertEqual(n["items"][0]["kind"], "appliance")
        self.assertIn(pid, [p["id"] for p in n["proposals"]])
        self.assertIsNotNone(n["paused_until"])
        proposals.decline(pid)

    def test_route_rows_in_ui(self):
        js = open(os.path.join(os.path.dirname(server.__file__), "ui", "app.js"), encoding="utf-8").read()
        for needle in ("case 'route':", "/api/notifications", "'Dieser PC'", "view-wall", "Wave.speech"):
            self.assertIn(needle, js)



class AbortedRequests(unittest.TestCase):
    """A request the browser gave up on is no error worth a traceback; anything else still is."""
    def report(self, exc):
        import contextlib
        import io
        srv = server.Server(("127.0.0.1", 0), server.Handler)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                try:
                    raise exc
                except Exception:  # noqa: BLE001
                    srv.handle_error(None, ("127.0.0.1", 1))
        finally:
            srv.server_close()
        return err.getvalue()

    def test_browser_gone_is_quiet(self):
        for exc in (ConnectionAbortedError(10053, "abgebrochen"), ConnectionResetError(), BrokenPipeError()):
            self.assertEqual(self.report(exc), "", type(exc).__name__)

    def test_real_errors_still_show(self):
        self.assertIn("ValueError", self.report(ValueError("kaputt")))

class BusyRun(unittest.TestCase):
    """Talking on: the voice engine cancels its run and asks again while the old turn is still ending.
    It used to get 409 and Jarvis said "ich komme nicht an mein Gehirn" (07.11.2026)."""
    def post(self, voice):
        import json
        import threading
        import urllib.error
        import urllib.request
        from unittest import mock
        srv = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        headers = {"content-type": "application/json", "X-Jarvis-Token": server.API_TOKEN,
                   "Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}"}
        if voice:
            headers["X-Jarvis-Voice"] = "1"
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/run", data=json.dumps({"message": "Mails"}).encode(),
                                     headers=headers, method="POST")
        server.RUN_LOCK.acquire()                       # the old turn
        threading.Timer(0.3, server.RUN_LOCK.release).start()
        try:
            with mock.patch.object(server, "PORT", port), \
                    mock.patch.object(server.runtime, "cancel_active") as cancel, \
                    mock.patch.object(server.Handler, "_stream", lambda self, raw: self._json({"ok": True})):
                try:
                    with urllib.request.urlopen(req, timeout=10) as r:
                        return r.status, cancel.called
                except urllib.error.HTTPError as e:
                    return e.code, cancel.called
        finally:
            srv.shutdown()
            srv.server_close()
            import time
            time.sleep(0.35)                            # the timer has given the lock back

    def test_voice_engine_waits_for_the_old_turn(self):
        self.assertEqual(self.post(voice=True), (200, True))

    def test_hud_still_gets_busy(self):
        self.assertEqual(self.post(voice=False), (409, False))

    def test_cancelled_turn_starts_no_claude(self):
        import runtime
        mark = runtime.cancel_mark()
        self.assertFalse(runtime.cancelled_since(mark))
        runtime.cancel_active()
        self.assertTrue(runtime.cancelled_since(mark))


if __name__ == "__main__":
    unittest.main()
