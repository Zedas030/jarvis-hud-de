"""
Data flows: the list of what leaves the house matches the code and the document, and the
question is answered locally. No network, no real .env.

    .venv\\Scripts\\python.exe -m unittest tests.test_data_flows -v
"""
import os
import pathlib
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import data_flows  # noqa: E402
import router      # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Named in the code, but nothing is sent there: XML namespaces and a default for a local address.
NOT_A_FLOW = {"calendarserver.org", "apple.com", "homeassistant.local"}


def active(env):
    return {f["key"] for f in data_flows.flows(env) if f["active"]}


class Complete(unittest.TestCase):
    def test_every_host_in_the_code_is_listed(self):
        listed = {h for f in data_flows.FLOWS for h in f[3]}
        found = set()
        for path in [ROOT / "server.py", *(ROOT / "jarvis").rglob("*.py")]:
            if path.name == "data_flows.py":
                continue
            for host in re.findall(r"https?://([A-Za-z0-9.-]+\.[a-z]{2,})", path.read_text(encoding="utf-8")):
                if not re.search(r"example|localhost|w3\.org|^127\.|^192\.", host):
                    found.add(host)
        self.assertEqual(sorted(found - listed - NOT_A_FLOW), [], "a new external host: add it to data_flows.FLOWS")

    def test_document_is_current(self):
        doc = (ROOT / "docs" / "datenfluesse.md").read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertEqual(doc, data_flows.markdown(), "run scripts/data_flows.py --write")

    def test_rows_are_whole(self):
        keys = [f[0] for f in data_flows.FLOWS]
        self.assertEqual(len(keys), len(set(keys)))
        for key, group, name, hosts, what, check, off in data_flows.FLOWS:
            self.assertTrue(name and what and off and callable(check), key)
            self.assertNotIn("|", name + what + off, key)         # would break the table


class Settings(unittest.TestCase):
    def test_bare_installation(self):
        with mock.patch.object(data_flows, "HOME_CONFIG", TMP / "kein-home.json"):
            self.assertEqual(active({}), {"anthropic", "edge", "weather", "alerts", "commute", "web", "makerworld", "checks"})

    def test_fallback_needs_key_and_place_in_the_chain(self):
        self.assertIn("groq", active({"GROQ_API_KEY": "x"}))
        self.assertNotIn("groq", active({"GROQ_API_KEY": "x", "JARVIS_FALLBACK": "ollama"}))
        self.assertNotIn("gemini", active({"JARVIS_FALLBACK": "groq,gemini"}))
        self.assertNotIn("anthropic", active({"JARVIS_RUNTIME": "mock"}))

    def test_voice_chain(self):
        env = {"TTS_CHAIN": "fish,elevenlabs,local,edge", "FISH_AUDIO_API_KEY": "x"}
        self.assertEqual(active(env) & {"fish", "elevenlabs", "azure", "google_tts", "edge"}, {"fish", "edge"})
        self.assertNotIn("edge", active({"TTS_CHAIN": "local"}))
        self.assertIn("elevenlabs", active({"JARVIS_TTS": "elevenlabs", "ELEVENLABS_API_KEY": "x"}))
        self.assertNotIn("cloud_stt", active({"FISH_AUDIO_API_KEY": "x"}))            # recognition stays local by default
        self.assertIn("cloud_stt", active({"FISH_AUDIO_API_KEY": "x", "JARVIS_STT": "fish"}))

    def test_services(self):
        env = {"ICLOUD_USER": "a@example.com", "JARVIS_WEATHER_ALERTS": "0", "MAIL_USER": "a@example.com", "DHL_API_KEY": "k"}
        got = active(env)
        self.assertTrue({"icloud", "mail", "dhl"} <= got)
        self.assertFalse({"alerts", "garmin", "spotify", "track17", "phone", "cost"} & got)

    def test_echo_comes_from_the_home_config(self):
        path = TMP / "home-echo.json"
        path.write_text('{"speakers": {"wohnzimmer": "notify.alexa_media_echo"}}', encoding="utf-8")
        with mock.patch.object(data_flows, "HOME_CONFIG", path):
            self.assertIn("echo", active({}))


class Said(unittest.TestCase):
    ENV = {"GROQ_API_KEY": "x", "GEMINI_API_KEY": "y", "TTS_CHAIN": "fish,local", "FISH_AUDIO_API_KEY": "geheim-wert",
           "JARVIS_SPOTIFY_CLIENT_ID": "id", "JARVIS_COST_KEY": "k"}

    def test_spoken(self):
        with mock.patch.object(data_flows, "HOME_CONFIG", TMP / "kein-home.json"):
            text = data_flows.spoken(self.ENV)
        self.assertTrue(text.startswith("Außer Haus gehen Daten derzeit an 10 Dienste."), text)
        self.assertIn("Ihre Anfragen gehen an Claude, im Notbetrieb an Groq und Google Gemini.", text)
        self.assertIn("Was ich spreche, geht an Fish Audio.", text)
        self.assertIn("Spotify", text)
        self.assertNotIn("Cost API", text)                       # no personal data: not worth a breath
        self.assertNotIn("geheim-wert", text)
        self.assertLess(len(text), 600)

    def test_report_never_prints_a_value(self):
        text = data_flows.report(self.ENV)
        self.assertIn("[aktiv] Fish Audio (Sprachausgabe)", text)
        self.assertIn("[  aus] ElevenLabs (Sprachausgabe)", text)
        self.assertIn("Bleibt im Haus:", text)
        self.assertNotIn("geheim-wert", text)

    def test_router_answers_locally(self):
        for said in ("Welche Daten gehen in die Cloud?", "Jarvis, wohin gehen meine Daten?", "Zeig mir die Datenflüsse",
                     "Was schickst du alles in die Cloud?", "Wer bekommt meine Daten?"):
            reply, handler = router._local(said, router.LOCAL_HANDLERS)
            self.assertEqual(handler, "data_flows", said)
            self.assertTrue(reply.startswith("Außer Haus gehen Daten"), said)

    def test_other_sentences_are_left_alone(self):
        for said in ("Welche Daten hat der Drucker gerade?", "Sichere meine Daten auf dem NAS", "Wie wird das Wetter?"):
            self.assertIsNone(router._data_flows(router._norm(said)), said)


if __name__ == "__main__":
    unittest.main()
