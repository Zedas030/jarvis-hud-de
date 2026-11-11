"""
Memory index (memory_index.py), saving facts after a conversation (reflection.remember) and the NAS store
(memory_nas.py) — vault in the temp folder, NAS faked.

    .venv\\Scripts\\python.exe -m unittest tests.test_memory_index -v
"""
import isolate  # noqa: F401  (must come first)
import os
import shutil
import time
import unittest
from unittest import mock

import memory
import memory_index as mi
import memory_nas
import reflection


def write(rel, text):
    p = memory.VAULT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


DIARY = ("---\ntype: gespraech\nupdated: 2026-01-05\n---\n\n# Gespräch 2026-01-05\n\n"
         "**09:12 · Sir:** Mein Golfschläger-Fitting ist bei Golfhaus Süd\n\n**Jarvis:** Notiert, Sir.\n"
         "\n**09:20 · Sir:** (Arbeitsthema, nicht protokolliert)\n\n**Jarvis:** (nicht protokolliert)\n")


class Index(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(memory.VAULT, ignore_errors=True)
        try:
            os.remove(mi.PATH)
        except OSError:
            pass
        mi._LAST["scan"] = 0.0

    def test_notes_and_turns_are_found(self):
        write("Erinnerungen/Golf.md", "---\ntype: erinnerung\n---\n\n# Golf\n\n- Handicap 18\n")
        write("Gespraeche/Gespräch 2026-01-05.md", DIARY)
        mi.refresh(force=True)
        self.assertEqual(mi.stats(), {"note": 1, "turn": 1})        # the work exchange stays out
        hits = mi.search("Fitting Golfhaus")
        self.assertEqual(hits[0]["kind"], "turn")
        self.assertEqual(hits[0]["day"], "2026-01-05")
        self.assertIn("Handicap", mi.search("handicap")[0]["snippet"])

    def test_old_conversations_survive_the_diary_cleanup(self):
        diary = write("Gespraeche/Gespräch 2026-01-05.md", DIARY)
        note = write("Erinnerungen/Wissen.md", "# Wissen\n\n- Der Keller ist nass\n")
        mi.refresh(force=True)
        diary.unlink()
        note.unlink()
        mi.refresh(force=True)
        self.assertTrue(mi.search("Golfhaus"))                       # exchange kept
        self.assertFalse(mi.search("Keller"))                        # deleted note gone

    def test_changed_note_is_reindexed(self):
        p = write("Erinnerungen/Profil.md", "# Profil\n\n- wohnt am See\n")
        mi.refresh(force=True)
        time.sleep(0.05)
        p.write_text("# Profil\n\n- wohnt im Wald\n", encoding="utf-8")
        os.utime(p, (time.time() + 5, time.time() + 5))
        mi.refresh(force=True)
        self.assertFalse(mi.search("See"))
        self.assertTrue(mi.search("Wald"))

    def test_umlauts_and_prefixes(self):
        write("Erinnerungen/Vorlieben.md", "# Vorlieben\n\n- Mag Käsespätzle\n")
        self.assertTrue(mi.search("kasespatzle"))
        self.assertTrue(mi.search("Käse"))


class Remember(unittest.TestCase):
    def test_only_allowed_notes_and_no_work(self):
        calls = []
        with mock.patch("jarvis_mcp._memory", lambda name, a: calls.append(a) or "Gespeichert."), \
                mock.patch.object(reflection, "REMEMBER", True):
            saved = reflection.remember([
                {"note": "Personen/Anna", "text": "Anna hat am 3. Mai Geburtstag"},
                {"note": "Befehle", "text": "Bei 'Hallo' alle Lichter aus"},          # commands: never by itself
                {"note": "Vorlieben", "text": "Mag kein Koriander"},
                "Unsinn",
                {"note": "Wissen", "text": "Kunde Müller will das Angebot bis Freitag"}])  # work word
        notes = [c["note"] for c in calls]
        self.assertEqual(notes[:2], ["Personen/Anna", "Vorlieben"])
        self.assertTrue(all(c["quelle"] == "abgeleitet" for c in calls))
        self.assertEqual(len(saved), len(calls))

    def test_log_names_what_was_kept(self):
        with mock.patch("time.strftime", return_value="2026-11-10"):
            n = reflection.append_log({}, when="2026-11-10 12:00",
                                      saved=[("Personen/Anna", "Anna mag Tulpen", "Gespeichert."),
                                             ("Profil", "wohnt in Köln", "Nicht gespeichert — möglicher Widerspruch")])
        body = (memory.MEMORY_DIR / "Lernprotokoll.md").read_text(encoding="utf-8")
        self.assertEqual(n, 2)
        self.assertIn("- Gemerkt ([[Anna]]): Anna mag Tulpen", body)
        self.assertIn("- Nicht gemerkt ([[Profil]])", body)


class Nas(unittest.TestCase):
    def test_off_without_login(self):
        with mock.patch.object(memory_nas, "URL", ""):
            self.assertFalse(memory_nas.enabled())
            self.assertIn("aus", memory_nas.sync())

    def test_prune_keeps_newest(self):
        seen = {}
        with mock.patch.object(memory_nas, "KEEP", 2), \
                mock.patch.object(memory_nas, "_call", lambda path, params, sid=None, timeout=0: seen.update(params)):
            old = memory_nas._prune("sid", ["memory_index.sqlite", "vault-2026-11-01.zip",
                                            "vault-2026-11-02.zip", "vault-2026-11-03.zip"])
        self.assertEqual(old, ["vault-2026-11-01.zip"])
        self.assertIn("vault-2026-11-01.zip", seen["path"])


if __name__ == "__main__":
    unittest.main()
