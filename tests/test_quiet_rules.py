"""
The quiet rules of the proactive mode — the heart of "when does Jarvis keep his mouth shut".

    .venv\\Scripts\\python.exe -m unittest tests.test_quiet_rules -v
"""
import datetime as dt
import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import proactive_config as pc  # noqa: E402
import quiet_rules as qr       # noqa: E402

DAY = dt.datetime(2026, 11, 5, 14, 0)
NIGHT = dt.datetime(2026, 11, 5, 23, 30)
EARLY = dt.datetime(2026, 11, 6, 6, 15)


def ctx(now=DAY, **kw):
    return dict(now=now, quiet_hours="22-7", **kw)


class Levels(unittest.TestCase):
    def test_demote(self):
        self.assertEqual(qr.demote("wichtig", 1), "nett")
        self.assertEqual(qr.demote("wichtig", 2), "stumm")
        self.assertEqual(qr.demote("nett", 5), "stumm")
        self.assertEqual(qr.demote("kritisch", 3), "kritisch")       # never lowered
        self.assertEqual(qr.demote("wichtig", 0), "wichtig")
        self.assertEqual(qr.demote("quatsch", 0), "nett")

    def test_hours(self):
        self.assertTrue(qr.in_hours("22-7", NIGHT))
        self.assertTrue(qr.in_hours("22-7", EARLY))
        self.assertFalse(qr.in_hours("22-7", DAY))
        self.assertTrue(qr.in_hours("13-15", DAY))
        for off in ("", "off", "aus", "x-y", "5-5", None):
            self.assertFalse(qr.in_hours(off, NIGHT), off)


class Holds(unittest.TestCase):
    def test_none_by_day(self):
        self.assertEqual(qr.holds(ctx()), [])
        self.assertEqual(qr.holds(ctx(proactive_switch=None)), [])      # unknown switch = on

    def test_each_hold(self):
        cases = {"aus": dict(proactive_switch=False), "notaus": dict(emergency=True), "weg": dict(away=True),
                 "ruhe": dict(quiet_until=DAY.timestamp() + 60), "gaeste": dict(guests=True),
                 "telefonat": dict(call=True)}
        for code, kw in cases.items():
            self.assertEqual(qr.holds(ctx(**kw)), [code], code)
        self.assertEqual(qr.holds(ctx(quiet_switch=True)), ["ruhe"])
        self.assertEqual(qr.holds(ctx(NIGHT)), ["ruhezeit"])

    def test_expired_quiet_is_no_hold(self):
        self.assertEqual(qr.holds(ctx(quiet_until=DAY.timestamp() - 1)), [])

    def test_morning_ends_the_night(self):
        self.assertEqual(qr.holds(ctx(EARLY)), ["ruhezeit"])
        self.assertEqual(qr.holds(ctx(EARLY, night_ended=True)), [])


class Decide(unittest.TestCase):
    """Every hold against every level and mode: the full table."""
    HOLDS = {None: {}, "aus": dict(proactive_switch=False), "notaus": dict(emergency=True), "weg": dict(away=True),
             "ruhe": dict(quiet_until=DAY.timestamp() + 3600), "gaeste": dict(guests=True),
             "telefonat": dict(call=True), "ruhezeit": {}}

    def expected(self, level, hold, mode):
        if level == "stumm":
            return "show"
        if mode == "requested":
            return "speak"
        if level == "kritisch":
            return "speak"
        if hold:
            return "defer" if level == "wichtig" else "show"
        if mode == "instant" and level == "nett":
            return "show"
        return "speak"

    def test_table(self):
        for level, hold, mode in itertools.product(qr.LEVELS, self.HOLDS, ("instant", "briefing", "requested")):
            now = NIGHT if hold == "ruhezeit" else DAY
            d = qr.decide(level, ctx(now, **self.HOLDS[hold]), mode)
            self.assertEqual(d.action, self.expected(level, hold, mode), (level, hold, mode, d))

    def test_retry_only_after_holds_that_end_during_the_day(self):
        for hold in ("weg", "ruhe", "gaeste", "telefonat"):
            self.assertTrue(qr.decide("wichtig", ctx(**self.HOLDS[hold])).retry, hold)
        for hold in ("aus", "notaus"):
            self.assertFalse(qr.decide("wichtig", ctx(**self.HOLDS[hold])).retry, hold)
        self.assertFalse(qr.decide("wichtig", ctx(NIGHT)).retry)            # quiet hours: wait for the morning
        self.assertFalse(qr.decide("wichtig", ctx(NIGHT, call=True)).retry)  # a call at night: still the night

    def test_reason_is_the_first_hold(self):
        d = qr.decide("wichtig", ctx(NIGHT, guests=True))
        self.assertEqual((d.action, d.reason), ("defer", "gaeste"))
        self.assertEqual(qr.decide("kritisch", ctx(NIGHT)).reason, "kritisch")

    def test_critical_can_be_held_if_configured(self):
        self.assertEqual(qr.decide("kritisch", ctx(NIGHT), critical_always=False).action, "show")
        self.assertEqual(qr.decide("kritisch", ctx(), critical_always=False).action, "speak")

    def test_briefing_allowed(self):
        self.assertTrue(qr.briefing_allowed(ctx()))
        self.assertFalse(qr.briefing_allowed(ctx(guests=True)))
        self.assertFalse(qr.briefing_allowed(ctx(NIGHT)))
        self.assertTrue(qr.briefing_allowed(ctx(EARLY, night_ended=True)))


class QuietCommands(unittest.TestCase):
    def test_durations(self):
        cases = {"ruhe für eine stunde": 60, "jarvis ruhe für 2 stunden": 120, "ruhe für zwei stunden": 120,
                 "ruhe für eine halbe stunde": 30, "sei still für 20 minuten": 20,
                 "keine ansagen für anderthalb stunden": 90, "bitte nicht stören für drei stunden": 180,
                 "ruhe für eine viertelstunde": 15}
        for text, minutes in cases.items():
            self.assertEqual(qr.parse_quiet(text, DAY), ("set", DAY + dt.timedelta(minutes=minutes)), text)

    def test_today(self):
        tomorrow5 = dt.datetime(2026, 11, 6, 5, 0)
        for text in ("heute keine ansagen", "heute keine ansagen mehr", "keine ansagen mehr für heute",
                     "für heute keine meldungen", "ruhe bis morgen"):
            self.assertEqual(qr.parse_quiet(text, DAY), ("set", tomorrow5), text)
        self.assertEqual(qr.parse_quiet("heute keine ansagen", dt.datetime(2026, 11, 6, 1, 0)),
                         ("set", tomorrow5))                       # after midnight: this morning

    def test_end(self):
        for text in ("ruhe aufheben", "ruhe beenden", "ansagen wieder an", "du darfst wieder sprechen"):
            self.assertEqual(qr.parse_quiet(text, DAY), ("end", None), text)

    def test_not_a_quiet_command(self):
        for text in ("ruhe", "musik pause", "wie ruhig ist es im wohnzimmer", "ruhe für immer",
                     "pause aufheben", "mach das licht aus für eine stunde", "ruhe für 30 stunden"):
            self.assertIsNone(qr.parse_quiet(text, DAY), text)


class Config(unittest.TestCase):
    def test_defaults_off_and_levels(self):
        cfg = pc.make()
        self.assertFalse(cfg.enabled)
        self.assertEqual(pc.level(cfg, "safety"), "kritisch")
        self.assertEqual(pc.level(cfg, "unbekannt"), "nett")
        self.assertFalse(pc.source_on(cfg, "noah"))
        self.assertTrue(pc.source_on(pc.make({"sources": {"noah": {"enabled": True}}}), "noah"))

    def test_file_and_env_flag(self):
        path = TMP / "proactive_test.yaml"
        path.write_text("enabled: true\nsources:\n  weather: {enabled: false}\n", encoding="utf-8")
        self.assertTrue(pc.load(path).enabled)
        self.assertFalse(pc.source_on(pc.load(path), "weather"))
        self.assertTrue(pc.source_on(pc.load(path), "calendar"))     # untouched defaults stay
        os.environ["JARVIS_PROACTIVE_MODE"] = "0"
        try:
            self.assertFalse(pc.load(path).enabled)
        finally:
            del os.environ["JARVIS_PROACTIVE_MODE"]

    def test_broken_file_gives_defaults(self):
        path = TMP / "broken.yaml"
        path.write_text("enabled: [unclosed\n", encoding="utf-8")
        self.assertFalse(pc.load(path).enabled)

    def test_example_is_valid_and_complete(self):
        example = pc.ROOT / "config" / "proactive.example.yaml"
        import yaml
        data = yaml.safe_load(example.read_text(encoding="utf-8"))
        self.assertEqual(set(data) - {"enabled"}, set(pc.DEFAULTS) - {"enabled"})
        for name in pc.DEFAULTS["sources"]:
            self.assertIn(name, data["sources"], name)
        for kind, lv in data["priorities"].items():
            self.assertIn(lv, pc.LEVELS, kind)


if __name__ == "__main__":
    unittest.main()
