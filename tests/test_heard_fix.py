"""
Known mishearings are replaced in the finished transcript (heard_fix.py, config/heard.json).

    .venv\\Scripts\\python.exe -m unittest tests.test_heard_fix -v
"""
import json
import os
import unittest

import tests.isolate  # noqa: F401
import heard_fix
import voice_stt


class HeardFix(unittest.TestCase):
    def setUp(self):
        heard_fix.PATH.write_text(json.dumps({"fixes": {
            "milz": "Mails", "sunnails": "sind Mails", "aufgelesen": "auf gelesen", "guten acht": "Gute Nacht"}}),
            encoding="utf-8")
        heard_fix._TABLE.update(mtime=None, rules=[])

    def tearDown(self):
        heard_fix.PATH.unlink(missing_ok=True)

    def test_what_he_said_on_20_11(self):
        self.assertEqual(heard_fix.fix("Ja, ich meine die Milz. Was kam heute rein?"),
                         "Ja, ich meine die Mails. Was kam heute rein?")
        self.assertEqual(heard_fix.fix("Was? Sunnails heute gekommen?"), "Was? sind Mails heute gekommen?")
        self.assertEqual(heard_fix.fix("Jarvis, jetzt alle aufgelesen."), "Jarvis, jetzt alle auf gelesen.")

    def test_whole_words_and_phrases_only(self):
        self.assertEqual(heard_fix.fix("Die Milzbrand-Doku"), "Die Milzbrand-Doku")
        self.assertEqual(heard_fix.fix("guten  Acht, Jarvis"), "Gute Nacht, Jarvis")

    def test_no_file_or_a_broken_one_changes_nothing(self):
        heard_fix.PATH.write_text("{kaputt", encoding="utf-8")
        heard_fix._TABLE.update(mtime=None, rules=[])
        self.assertEqual(heard_fix.fix("die Milz"), "die Milz")
        heard_fix.PATH.unlink()
        self.assertEqual(heard_fix.fix("die Milz"), "die Milz")
        self.assertEqual(heard_fix.fix(""), "")

    def test_an_edited_file_is_read_again(self):
        self.assertEqual(heard_fix.fix("die Milz"), "die Mails")
        heard_fix.PATH.write_text(json.dumps({"fixes": {}}), encoding="utf-8")
        os.utime(heard_fix.PATH, (1, 1))
        self.assertEqual(heard_fix.fix("die Milz"), "die Milz")

    def test_the_recognizer_applies_it_to_the_final_transcript(self):
        rec = voice_stt.Recognizer({})
        rec._run = lambda pcm, beam: "Lies mir die Milz vor."
        self.assertEqual(rec.final(b"\0" * 32000), "Lies mir die Mails vor.")


if __name__ == "__main__":
    unittest.main()
