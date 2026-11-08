"""
End to end: a fake Home Assistant (a real HTTP server on 127.0.0.1) and Jarvis' own HTTP server. Nothing is
stubbed between the proactive mode and the wire — the briefing goes out through speakers.announce as a
tts.speak call, the switches as input_boolean services, the dashboard reads /api/proactive.

    .venv\\Scripts\\python.exe -m unittest tests.test_proactive_e2e -v
"""
import datetime as dt
import json
import os
import sys
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402  (must come before any Jarvis module)

import away_check        # noqa: E402
import background        # noqa: E402
import briefing          # noqa: E402
import commands          # noqa: E402
import events            # noqa: E402
import jarvis_mcp        # noqa: E402
import proactive         # noqa: E402
import proactive_config as pconf  # noqa: E402
import router            # noqa: E402
import server            # noqa: E402
import whereabouts       # noqa: E402

UTC = dt.timezone.utc
PROFILE = {"person": "person.me", "presence_room": "input_select.room",
           "speakers": {"Wohnzimmer": {"tts": "tts.google", "player": "media_player.wohnzimmer"}}}
YAML = """
enabled: true
ha: {switch_proactive: input_boolean.jarvis_proaktiv, switch_guests: input_boolean.jarvis_gaste_modus,
     switch_quiet: input_boolean.jarvis_ruhe}
output: {formulate_with_llm: true, llm_timeout_s: 3}
quiet: {hours: ""}
calls: {windows_mic: false, entities: []}
sources:
  calendar: {enabled: false}
  weather: {enabled: false}
  dwd: {enabled: false}
  parcels: {enabled: false}
  energy: {enabled: false}
  noah: {enabled: true, switch: input_boolean.noah_diff_warnung, master: sensor.bat0, slave: sensor.bat1}
"""


class FakeHA:
    def __init__(self):
        self.states, self.calls = {}, []
        ha = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, body):
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer test-token":
                    self.send_response(401)
                    self.end_headers()
                    return
                if self.path == "/api/states":
                    return self._send(list(ha.states.values()))
                eid = self.path.rsplit("/", 1)[-1]
                return self._send(ha.states.get(eid) or {})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                ha.calls.append((self.path, body))
                if self.path.startswith("/api/services/input_boolean/"):
                    on = self.path.endswith("turn_on")
                    ha.set(body["entity_id"], "on" if on else "off")
                return self._send([])

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def set(self, eid, state, **kw):
        self.states[eid] = dict({"entity_id": eid, "state": state, "attributes": {},
                                 "last_changed": dt.datetime.now(UTC).isoformat()}, **kw)

    def spoken(self):
        return [b["message"] for p, b in self.calls if p == "/api/services/tts/speak"]


def real_ha(path, body=None):
    """jarvis_mcp._ha as it is in the code (test_router replaces it module-wide for its own fake house)."""
    req = urllib.request.Request(jarvis_mcp.HA_URL + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {jarvis_mcp.HA_TOKEN}",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"null")


def real_states():
    """briefing._ha_states as it is in the code (test_router swaps it for its fake house, module-wide)."""
    if not briefing.HA_TOKEN:
        return []
    return briefing._get(briefing.HA_URL + "/api/states", {"Authorization": f"Bearer {briefing.HA_TOKEN}"}, timeout=15)


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ha = FakeHA()
        cls.jarvis = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.jarvis.server_address[1]
        threading.Thread(target=cls.jarvis.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for srv in (cls.jarvis, cls.ha.srv):
            srv.shutdown()
            srv.server_close()
        pconf.PATH.unlink(missing_ok=True)
        pconf._CACHE.update(cfg=None, mtime=None)

    def setUp(self):
        ha = self.ha
        ha.states.clear()
        ha.calls.clear()
        for eid, st in (("person.me", "home"), ("input_select.room", "Wohnzimmer"), ("sensor.bat0", "52"),
                        ("sensor.bat1", "40"), ("input_boolean.noah_diff_warnung", "off"),
                        ("input_boolean.jarvis_proaktiv", "on"), ("input_boolean.jarvis_gaste_modus", "off"),
                        ("input_boolean.jarvis_ruhe", "off")):
            ha.set(eid, st)
        pconf.PATH.write_text(YAML, encoding="utf-8")
        pconf._CACHE.update(cfg=None, mtime=None)
        proactive.SPEAK_HOOK.clear()
        commands.update_state(lambda d: [d.pop(k, None) for k in ("proactive", "away_since", "welcomed",
                                                                  "away_checked", "briefing_day")])
        proactive.HISTORY.unlink(missing_ok=True)
        events.NOTICES.unlink(missing_ok=True)
        whereabouts.forget()
        for p in (mock.patch.multiple(briefing, HA_URL=ha.url, HA_TOKEN="test-token", _ha_states=real_states),
                  mock.patch.multiple(whereabouts, HA_URL=ha.url, HA_TOKEN="test-token"),
                  mock.patch.multiple(jarvis_mcp, HA_URL=ha.url, HA_TOKEN="test-token", _ha=real_ha),
                  mock.patch("jarvis_profile.ha", side_effect=lambda k, d=None: PROFILE.get(k, d)),
                  mock.patch.object(server, "PORT", self.port),
                  mock.patch.object(background, "ask", side_effect=self.model)):
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def model(job, system, prompt, tools=""):
        # the stand-in for Claude words the briefing from the notices it was given
        lines = [ln.split("] ", 1)[1] for ln in prompt.splitlines() if ln.startswith("- [")]
        return "Willkommen zurück, Sir. " + " ".join(lines)

    def api(self, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="POST" if body else "GET",
                                     data=json.dumps(body).encode() if body else None,
                                     headers={"X-Jarvis-Token": server.API_TOKEN, "Host": f"127.0.0.1:{self.port}",
                                              "Origin": f"http://127.0.0.1:{self.port}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def states(self):
        return list(self.ha.states.values())

    def test_a_whole_day(self):
        ha = self.ha
        # 1. leaving, two hours away; meanwhile the battery drift starts
        left = dt.datetime.now(UTC) - dt.timedelta(hours=2)
        ha.set("person.me", "not_home", last_changed=left.isoformat())
        away_check.check(self.states(), now=left + dt.timedelta(minutes=5))
        ha.set("input_boolean.noah_diff_warnung", "on")
        ha.set("input_select.room", "Unbekannt")
        proactive.tick(self.states())
        self.assertEqual(ha.spoken(), [])                            # nobody home: kept for the arrival
        self.assertEqual(proactive.history(1)[0]["reason"], "nicht zu Hause")

        # 2. coming home: the briefing waits for the room, then speaks there, with what happened
        ha.calls.clear()
        ha.set("person.me", "home")
        self.assertEqual(away_check.check(self.states()), "proaktiv")
        proactive.tick(self.states())
        self.assertEqual(ha.spoken(), [])                            # no room yet
        ha.set("input_select.room", "Wohnzimmer")
        whereabouts.forget()
        proactive.tick(self.states())
        self.assertEqual(ha.spoken(), ["Willkommen zurück, Sir. Die NOAH-Akkus laufen auseinander: 12 Prozent "
                                       "Unterschied."])
        proactive.tick(self.states())
        self.assertEqual(len(ha.spoken()), 1)                        # said once — not again by the instant round
        speak = [b for p, b in ha.calls if p == "/api/services/tts/speak"][0]
        self.assertEqual(speak["media_player_entity_id"], "media_player.wohnzimmer")

        # 3. "Jarvis, Ruhe für eine Stunde" → HA switch on; a new drift is held, not spoken
        ha.calls.clear()
        self.assertTrue(router.route("Jarvis, Ruhe für eine Stunde")["reply"].startswith("Verstanden"))
        self.assertEqual(ha.states["input_boolean.jarvis_ruhe"]["state"], "on")
        ha.set("input_boolean.noah_diff_warnung", "off")
        proactive.tick(self.states())
        ha.set("input_boolean.noah_diff_warnung", "on")
        proactive.tick(self.states())
        self.assertEqual(ha.spoken(), [])

        # 4. the user switches the quiet off in HA → the held drift follows
        ha.set("input_boolean.jarvis_ruhe", "off")
        proactive.tick(self.states())
        proactive.tick(self.states())
        self.assertEqual(ha.spoken(), ["Die NOAH-Akkus laufen auseinander: 12 Prozent Unterschied."])

        # 5. the dashboard: history with reasons, feedback by button, details for "mehr dazu"
        status = self.api("/api/proactive")
        actions = [(r.get("action"), r.get("reason")) for r in status["history"]]
        self.assertIn(("zurückgehalten", "Ruhe"), actions)
        self.assertIn(("gesprochen", ""), actions)
        self.assertEqual(self.api("/api/proactive/feedback", {"kind": "noah"})["learned"][0]["marks"], 1)
        self.assertIn("Master 52 %, Slave 40 %", router.route("mehr dazu")["reply"])
        bell = self.api("/api/notifications")
        self.assertTrue(any(i["kind"] == "proactive" for i in bell["items"]))

    def test_dead_home_assistant_does_not_stop_the_briefing(self):
        self.ha.set("person.me", "home")
        with mock.patch.multiple(briefing, HA_URL="http://127.0.0.1:9"):
            proactive.on_arrival(time.time() - 7200)
            proactive.tick()
        self.assertEqual(self.ha.spoken(), ["Willkommen zurück, Sir. Während du weg warst, war alles ruhig."])
        self.assertIn("noah", proactive._st()["down"])

    def test_trigger_endpoint_only_on_the_pc(self):
        self.assertEqual(self.api("/api/proactive/trigger", {"mode": "evening"})["text"],
                         "Gute Nacht, Sir. Alles erledigt, nichts mehr offen.")
        with self.assertRaises(urllib.error.HTTPError):
            self.api("/api/proactive/trigger", {"mode": "quatsch"})


if __name__ == "__main__":
    unittest.main()
