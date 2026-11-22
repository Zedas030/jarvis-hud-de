"""
Jarvis asks back -> the satellite listens again without a wake word, once the room's speaker is done.
No network, no house.

    .venv\\Scripts\\python.exe -m unittest tests.test_satellite_followup -v
"""
import isolate  # noqa: F401  (must come first)
import time
import unittest
from unittest import mock

import alexa_voice
import satellite_followup as sf
import sonos

PE = "assist_satellite.wohnzimmer_home_assistant_voice_09fdba_assist_satellit"
ECHO = "notify.alexa_media_nutzer_echo_dot"


class AsksBack(unittest.TestCase):
    def test_question_at_the_end_or_just_before(self):
        self.assertTrue(sf.asks_back("Soll ich die wichtigen vorlesen?"))
        self.assertTrue(sf.asks_back("Meinen Sie Ihre Mails, Sir? Falls ja, sage ich Ihnen gern, was neu ist."))
        self.assertFalse(sf.asks_back("Das Licht ist an, Sir."))
        self.assertFalse(sf.asks_back("Wollten Sie das? Gut. Dann ist alles erledigt. Schönen Abend."))


class FollowUp(unittest.TestCase):
    def setUp(self):
        sf.reset()
        self.calls = []
        self.p = [mock.patch.object(sf, "ENABLED", True), mock.patch.object(sf, "ECHO_TAIL_S", 0.1),
                  mock.patch.object(sf, "SONOS_TAIL_S", 0.05), mock.patch.object(sf, "MEDIA", "http://ha/local/stille.wav"),
                  mock.patch("jarvis_mcp._ha", side_effect=lambda path, body=None: self.calls.append((path, body)))]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        sf.reset()
        alexa_voice._SAYING.clear()
        alexa_voice._BUSY.clear()

    def test_after_the_echo_is_done_the_mic_opens_without_sound(self):
        sf.heard(PE)
        alexa_voice._SAYING[ECHO] = time.time() + 0.3          # Alexa still reads the question
        t = sf.after_answer("Meinen Sie Ihre Mails, Sir?", ECHO)
        self.assertTrue(sf.pending())
        time.sleep(0.2)
        self.assertEqual(self.calls, [], "not while the Echo still speaks")
        t.join(2)
        self.assertEqual(self.calls, [("/api/services/assist_satellite/start_conversation",
                                       {"entity_id": PE, "start_media_id": "http://ha/local/stille.wav",
                                        "preannounce": False})])
        self.assertFalse(sf.pending())

    def test_sonos_waits_for_its_stream(self):
        sf.heard(PE)
        speaking = [True]
        with mock.patch.object(sonos, "speaking", side_effect=lambda eid: speaking[0]):
            t = sf.after_answer("Soll ich?", "media_player.sonos")
            time.sleep(0.3)
            self.assertEqual(self.calls, [])
            speaking[0] = False
            t.join(2)
        self.assertEqual(len(self.calls), 1)

    def test_no_question_no_satellite_or_off(self):
        sf.heard(PE)
        self.assertIsNone(sf.after_answer("Erledigt, Sir.", ECHO))
        sf.reset()
        self.assertIsNone(sf.after_answer("Soll ich?", ECHO), "no satellite listened lately")
        sf.heard(PE)
        with mock.patch.object(sf, "ENABLED", False):
            self.assertIsNone(sf.after_answer("Soll ich?", ECHO))
        self.assertEqual(self.calls, [])

    def test_too_old_satellite_is_not_reopened(self):
        sf.heard(PE)
        sf._ST["last_at"] -= sf.LAST_MAX_AGE_S + 1
        self.assertEqual(sf.last_satellite(), "")


if __name__ == "__main__":
    unittest.main()
