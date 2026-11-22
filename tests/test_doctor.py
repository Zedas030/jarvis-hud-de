"""
Doctor script: the checks themselves, with made-up installations. No network, no real .env.

    .venv\\Scripts\\python.exe -m unittest tests.test_doctor -v
"""
import os
import pathlib
import sys
import unittest
from importlib import metadata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402  (must come before any Jarvis module)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import doctor  # noqa: E402

BOX = TMP / "doctor"


def write(name, text):
    path = BOX / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class Files(unittest.TestCase):
    def test_env_file(self):
        env = doctor.read_env(write(".env", "# Kommentar\nHA_URL=http://ha.example.local:8123\nHA_TOKEN='abc'\n\nLEER=\n"))
        self.assertEqual(env, {"HA_URL": "http://ha.example.local:8123", "HA_TOKEN": "abc", "LEER": ""})
        self.assertEqual(doctor.read_env(BOX / "gibt-es-nicht"), {})

    def test_requirements(self):
        path = write("req.txt", "# x\nfaster-whisper==1.2.1    # Kommentar\nwebsockets>=13\nnumpy\npypdf>=5,<7\n-r other.txt\n")
        self.assertEqual(doctor.requirements(path),
                         [("faster-whisper", "1.2.1"), ("websockets", ""), ("numpy", ""), ("pypdf", "")])

    def test_packages(self):
        path = write("req2.txt", "da==1.0\nfehlt\nalt==2.0\n")

        def installed(name):
            if name == "fehlt":
                raise metadata.PackageNotFoundError(name)
            return "1.0"
        self.assertEqual(doctor.check_packages([path], installed), (doctor.FAIL, "Pakete", "fehlen: fehlt"))
        path = write("req3.txt", "da==1.0\nalt==2.0\n")
        self.assertEqual(doctor.check_packages([path], installed), (doctor.WARN, "Pakete", "andere Version: alt 1.0 statt 2.0"))
        path = write("req4.txt", "da==1.0\n")
        self.assertEqual(doctor.check_packages([path], installed)[0], doctor.OK)

    def test_configs(self):
        write("config/home.json", '{"a": 1}')
        write("config/home.json.bak-20260101", "kaputt")
        write("config/x.example.json", "kaputt")
        self.assertEqual(doctor.check_configs(BOX / "config")[0], doctor.OK)
        write("config/routines.json", '{"a": ')
        status, _, detail = doctor.check_configs(BOX / "config")
        self.assertEqual(status, doctor.FAIL)
        self.assertIn("routines.json", detail)
        self.assertNotIn("bak", detail)

    def test_state(self):
        self.assertEqual(doctor.check_state(BOX / "kein-state.json")[0], doctor.OK)
        self.assertEqual(doctor.check_state(write("state.json", "{}"))[0], doctor.OK)
        self.assertEqual(doctor.check_state(write("state2.json", "{halb"))[0], doctor.FAIL)


class Environment(unittest.TestCase):
    def test_python(self):
        self.assertEqual(doctor.check_python((3, 11, 9), "a", "b")[0], doctor.FAIL)
        self.assertEqual(doctor.check_python((3, 12, 4), "a", "a")[0], doctor.WARN)
        self.assertEqual(doctor.check_python((3, 12, 4), "a", "b")[0], doctor.OK)

    def test_env_rules(self):
        self.assertEqual(doctor.check_env({}, exists=False)[0][0], doctor.FAIL)
        self.assertEqual(doctor.check_env({"HA_URL": "x"})[0][0], doctor.WARN)
        self.assertEqual([r[0] for r in doctor.check_env({"HA_URL": "x", "HA_TOKEN": "y"})], [doctor.OK])
        rows = doctor.check_env({"HA_URL": "x", "HA_TOKEN": "y", "ANTHROPIC_API_KEY": "nie"})
        self.assertEqual(rows[1][:2], (doctor.FAIL, "ANTHROPIC_API_KEY"))
        self.assertNotIn("nie", str(rows))                 # values are never printed

    def test_running(self):
        closed = doctor.check_running({}, port_open=lambda p: False)
        self.assertEqual([(r[0], r[1]) for r in closed], [(doctor.WARN, "Jarvis")])
        up = doctor.check_running({"JARVIS_PORT": "8799"}, port_open=lambda p: p == 8799, http_status=lambda u: 200)
        self.assertEqual([r[0] for r in up], [doctor.OK, doctor.WARN])
        self.assertIn("8799", up[0][2])
        hung = doctor.check_running({}, port_open=lambda p: True, http_status=lambda u: "timed out")
        self.assertEqual([r[0] for r in hung], [doctor.FAIL, doctor.OK])

    def test_home_assistant(self):
        env = {"HA_URL": "http://ha.example.local:8123/", "HA_TOKEN": "geheim"}
        seen = []

        def status(code):
            return lambda url, token: seen.append(url) or code
        self.assertEqual(doctor.check_home_assistant({})[0], doctor.WARN)
        self.assertEqual(doctor.check_home_assistant(env, status(200))[0], doctor.OK)
        self.assertEqual(seen[-1], "http://ha.example.local:8123/api/")
        row = doctor.check_home_assistant(env, status(401))
        self.assertEqual(row[0], doctor.FAIL)
        self.assertNotIn("geheim", row[2])
        self.assertEqual(doctor.check_home_assistant(env, status("timed out"))[0], doctor.FAIL)


if __name__ == "__main__":
    unittest.main()
