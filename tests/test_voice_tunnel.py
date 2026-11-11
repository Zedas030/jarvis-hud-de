"""
iPad/iPhone → voice engine through the remote HUD (server._voice_tunnel). A small WebSocket echo server
stands in for the engine; nothing leaves the PC.

    .venv\\Scripts\\python.exe -m unittest tests.test_voice_tunnel -v
"""
import os
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402  (must come before any Jarvis module)

import devices  # noqa: E402
devices.PATH = TMP / "devices.json"
import server   # noqa: E402
import voice_ws  # noqa: E402

HOST = "jarvis-pc.tail1234.ts.net"


def echo_server():
    from websockets.sync.server import serve
    seen = {}

    def handler(ws):
        seen["cookie"] = ws.request.headers.get("Cookie")
        for msg in ws:
            ws.send("echo:" + msg)
    srv = serve(handler, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.socket.getsockname()[1], seen


class Tunnel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine, cls.engine_port, cls.seen = echo_server()
        cls.remote = ThreadingHTTPServer(("127.0.0.1", 0), server.RemoteHandler)
        threading.Thread(target=cls.remote.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.remote.shutdown()
        cls.engine.shutdown()

    def setUp(self):
        devices.PATH.unlink(missing_ok=True)
        devices._CODE.update(code=None, until=0, tries=0)
        server.REMOTE_HOSTS.clear()
        p = mock.patch.object(voice_ws, "status", return_value={"running": True, "port": self.engine_port})
        p.start()
        self.addCleanup(p.stop)

    def connect(self, cookie=None, origin="https://" + HOST):
        import socket
        from websockets.sync.client import connect
        headers = {"Origin": origin}
        if cookie:
            headers["Cookie"] = cookie
        # the URI names the tailnet host (it becomes the Host header), the socket goes to the test server
        sock = socket.create_connection(("127.0.0.1", self.remote.server_address[1]), timeout=5)
        return connect(f"ws://{HOST}/voice-ws", sock=sock, additional_headers=headers, open_timeout=5)

    def paired_cookie(self):
        code, _ = devices.start_pairing()
        token = devices.pair(code, "iPad")
        return devices.cookie_header(token).split(";")[0]

    def test_paired_device_reaches_the_engine(self):
        cookie = self.paired_cookie()
        with self.connect(cookie) as ws:
            ws.send("hallo")
            self.assertEqual(ws.recv(timeout=5), "echo:hallo")
        self.assertEqual(self.seen["cookie"], cookie)          # the engine can tell which device it is

    def test_unpaired_device_is_refused(self):
        from websockets.exceptions import InvalidStatus
        with self.assertRaises(InvalidStatus):
            self.connect()

    def test_foreign_origin_is_refused(self):
        from websockets.exceptions import InvalidStatus
        with self.assertRaises(InvalidStatus):
            self.connect(self.paired_cookie(), origin="https://evil.example")

    def test_engine_remote_switch(self):
        from websockets.exceptions import InvalidStatus
        cfg = server.voice_config.load()
        with mock.patch.dict(cfg.engine, {"remote": False}), \
                mock.patch.object(server.voice_config, "load", return_value=cfg), \
                self.assertRaises(InvalidStatus):
            self.connect(self.paired_cookie())


if __name__ == "__main__":
    unittest.main()
