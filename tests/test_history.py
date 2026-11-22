"""
Conversation window: exchanges that left it stay as one short line each while the conversation
goes on. No network, no Claude.

    .venv\\Scripts\\python.exe -m unittest tests.test_history -v
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import commands  # noqa: E402


def talk(n, gap_s=60, end=None, pause_before=None, pause_s=3600):
    """n exchanges, the last one at `end`; `pause_before`=k puts a long pause in front of exchange k."""
    end = end or time.time() - 5
    out, at = [], end
    for i in range(n, 0, -1):
        out.append({"u": f"Frage {i} zum Thema Heizung im Schlafzimmer", "a": f"Antwort {i}. Mit einem zweiten Satz.", "at": at})
        at -= pause_s if pause_before == i else gap_s
    return out[::-1]


class Earlier(unittest.TestCase):
    def setUp(self):
        self.addCleanup(commands.update_state, lambda d: d.__setitem__("window", []))

    def block(self, entries):
        return commands.earlier_block(entries, entries[-commands.HISTORY_TURNS]["at"])

    def test_short_conversation_has_no_block(self):
        entries = talk(commands.HISTORY_TURNS)
        self.assertEqual(commands.earlier_block(entries, entries[0]["at"]), "")

    def test_older_exchanges_become_one_line_each(self):
        entries = talk(commands.HISTORY_TURNS + 3)
        lines = self.block(entries).splitlines()
        self.assertTrue(lines[0].startswith("Früher in diesem Gespräch"))
        self.assertEqual(len(lines), 4)
        self.assertRegex(lines[1], r"^- \d\d:\d\d Frage 1 zum Thema Heizung im Schlafzimmer → Antwort 1\.$")
        self.assertIn("Frage 3", lines[3])                      # oldest first, like the window
        self.assertNotIn("zweiten Satz", "\n".join(lines))      # only the first sentence of an answer

    def test_never_more_than_the_limit(self):
        entries = talk(commands.HISTORY_TURNS + commands.HISTORY_EARLIER + 5)
        lines = self.block(entries).splitlines()[1:]
        self.assertEqual(len(lines), commands.HISTORY_EARLIER)
        self.assertIn("Frage 6 ", lines[0])                     # the nearest ones are kept: 6 … 11 of 15

    def test_a_pause_ends_the_conversation(self):
        entries = talk(commands.HISTORY_TURNS + 4, pause_before=3)
        block = self.block(entries)
        self.assertIn("Frage 3", block)
        self.assertNotIn("Frage 2", block)
        self.assertNotIn("Frage 1 ", block)

    def test_long_text_is_cut_at_a_word(self):
        entries = talk(commands.HISTORY_TURNS + 1)
        entries[0]["u"] = "wort " * 60
        entries[0]["a"] = "silbe " * 80
        line = self.block(entries).splitlines()[1]
        self.assertLess(len(line), 215)
        self.assertEqual(line.count("…"), 2)
        self.assertNotIn("wor …", line)

    def test_window_keeps_enough_and_prefix_shows_both(self):
        commands.update_state(lambda d: d.__setitem__("window", []))
        total = commands.HISTORY_TURNS + commands.HISTORY_EARLIER + 6
        for i in range(1, total + 1):
            commands.remember_turn(f"Frage {i} bitte", f"Antwort {i}.")
        kept = commands.load()["window"]
        self.assertEqual(len(kept), commands.HISTORY_TURNS + commands.HISTORY_EARLIER + 3)
        prefix = commands.lean_prefix("und jetzt?")
        self.assertLess(prefix.index("Früher in diesem Gespräch"), prefix.index("Letzte Wortwechsel"))
        shown = prefix.split("Letzte Wortwechsel")[1]
        self.assertIn(f"Frage {total} bitte", shown)
        earlier = prefix.split("Letzte Wortwechsel")[0]
        self.assertEqual(earlier.count("\n- "), commands.HISTORY_EARLIER)
        self.assertNotIn(f"Frage {total - commands.HISTORY_TURNS + 1} bitte", earlier)   # nothing twice

    def test_off(self):
        commands.update_state(lambda d: d.__setitem__("window", talk(commands.HISTORY_TURNS + 3)))
        with mock.patch.object(commands, "HISTORY_EARLIER", 0):
            prefix = commands.lean_prefix("und jetzt?")
        self.assertNotIn("Früher in diesem Gespräch", prefix)
        self.assertIn("Letzte Wortwechsel", prefix)

    def test_old_conversation_shows_nothing(self):
        old = time.time() - 3 * 3600
        commands.update_state(lambda d: d.__setitem__("window", talk(commands.HISTORY_TURNS + 3, end=old)))
        prefix = commands.lean_prefix("hallo")
        self.assertNotIn("Früher in diesem Gespräch", prefix)
        self.assertNotIn("Letzte Wortwechsel", prefix)


if __name__ == "__main__":
    unittest.main()
