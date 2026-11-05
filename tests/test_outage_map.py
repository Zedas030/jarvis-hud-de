"""
Outage map (Blast-Radius) — the HTTP call is faked; no network.

    .venv\\Scripts\\python.exe -m unittest tests.test_outage_map -v
"""
import isolate  # noqa: F401  (must come first)
import unittest

import outage_map
import policy
import router

ANSWER = {
    "matched": {"id": "hw:pi", "label": "Raspberry Pi (board)", "say": "der Pi"},
    "alternatives": [{"label": "Raspberry Pi (raspi, OS)"}],
    "summary": "If Raspberry Pi (board) fails, 3 of 17 functions stop working …",
    "functions": {"total": 17,
                  "failed": [{"id": "fn:heating", "label": "Heating", "say": "die Heizung"},
                             {"id": "fn:jarvis", "label": "Jarvis voice assistant", "say": "Jarvis"},
                             {"id": "fn:remote", "label": "Remote access via Tailscale", "say": "der Fernzugriff"}],
                  "degraded": [{"id": "fn:music", "label": "Music follows me"}]},
}


class OutageMap(unittest.TestCase):
    def setUp(self):
        self._ask = outage_map.ask
        self.asked = []

        def fake(q):
            self.asked.append(q)
            return ANSWER
        outage_map.ask = fake
        router.forget_topic()

    def tearDown(self):
        outage_map.ask = self._ask

    def test_tool_only_reads(self):
        self.assertEqual(policy.action_class("outage_impact"), policy.READ)

    def test_spoken_sentence_uses_spoken_names(self):
        self.assertEqual(outage_map.spoken(ANSWER),
                         "Wenn der Pi ausfällt, fallen 3 von 17 Funktionen aus: die Heizung, Jarvis und der Fernzugriff. "
                         "Eingeschränkt: Music follows me.")
        one = dict(ANSWER, functions={"total": 17, "failed": [{"label": "X", "say": "die Raumerkennung"}], "degraded": []})
        self.assertIn("fällt 1 von 17 Funktionen aus: die Raumerkennung.", outage_map.spoken(one))
        none = dict(ANSWER, functions={"total": 17, "failed": [], "degraded": []})
        self.assertEqual(outage_map.spoken(none), "Wenn der Pi ausfällt, fällt keine Funktion komplett aus.")
        self.assertIsNone(outage_map.spoken({"matched": None}))

    def test_long_lists_are_counted(self):
        many = dict(ANSWER, functions={"total": 17, "failed": [{"say": f"F{i}"} for i in range(9)], "degraded": []})
        self.assertIn("F5 und 3 weitere.", outage_map.spoken(many))

    def test_router_answers_outage_questions_locally(self):
        for text in ("Was passiert, wenn der Pi abstürzt?", "Jarvis, was fällt aus, wenn die Fritzbox ausfällt?",
                     "Was hängt alles am Zigbee-Koordinator?", "Was geht kaputt, wenn das NAS keinen Strom hat?"):
            r = router.route(text)
            self.assertEqual(r["kind"], "local", text)
            self.assertEqual(r["handler"], "outage", text)
            self.assertTrue(r["reply"].startswith("Wenn der Pi ausfällt"), text)
        self.assertEqual(self.asked[0], "Was passiert, wenn der Pi abstürzt?")  # raw text goes to Blast-Radius

    def test_other_questions_are_not_taken(self):
        for text in ("Was passiert heute noch?", "Wie geht es dem Pi?", "Mach das Licht aus",
                     "Was passiert, wenn ich das Licht ausschalte?"):
            r = router.route(text)
            self.assertNotEqual(r.get("handler"), "outage", text)

    def test_unknown_device_or_unreachable_falls_back_to_claude(self):
        outage_map.ask = lambda q: {"matched": None}
        r = router.route("Was passiert, wenn der Flux-Kompensator ausfällt?")
        self.assertEqual(r["kind"] if r.get("handler") == "outage" else "llm", "llm")

        def boom(q):
            raise OSError("unreachable")
        outage_map.ask = boom
        r = router.route("Was passiert, wenn der Pi abstürzt?")
        self.assertNotEqual(r.get("handler"), "outage")
        self.assertIn("homelab", r["groups"])  # Claude then has outage_impact

    def test_mcp_tool_text(self):
        text = outage_map.describe("Was passiert, wenn der Pi abstürzt?")
        self.assertIn("die Heizung", text)
        self.assertIn("Raspberry Pi (raspi, OS)", text)


if __name__ == "__main__":
    unittest.main()
