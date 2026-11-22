"""wake_learn: a "Stopp" right after a satellite woke up, with nothing to stop, is a false wake-up — and only that."""
import json
import os
import sys
import time
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolate import TMP  # noqa: E402,F401  (must come before any Jarvis module)

import ha_events  # noqa: E402
import proposals  # noqa: E402
import remote_ask  # noqa: E402
import router  # noqa: E402
import satellite_followup  # noqa: E402
import satellite_quiet  # noqa: E402
import wake_learn  # noqa: E402

SAT = "assist_satellite.voice_pe_wohnzimmer_assist_satellit"
BED = "assist_satellite.respeaker_schlafzimmer_assist_satellit"
SELECT = "select.voice_pe_wohnzimmer_wake_word_sensitivity"
SECOND = b"\x10\x00" * 16000          # one second of 16 kHz PCM


def change(eid, old, new):
    return {"entity_id": eid, "old_state": {"state": old}, "new_state": {"state": new}}


class FakeRecognizer:
    available = True

    def __init__(self, text):
        self.text = text

    def final(self, pcm):
        return self.text


class Base(unittest.TestCase):
    def setUp(self):
        wake_learn.reset()
        satellite_followup.reset()
        for f in (wake_learn.LOG, proposals.PATH):
            if f.exists():
                f.unlink()
        if wake_learn.CLIPS.exists():
            for f in wake_learn.CLIPS.glob("*.wav"):
                f.unlink()
        self.states = {SELECT: {"entity_id": SELECT, "state": "Very sensitive", "last_changed": "2026-11-01T10:00:00+00:00"}}
        self.ring = b""
        for name, value in (("_states", lambda: self.states), ("_pc_ring", lambda: self.ring),
                            ("_room", lambda sat: "schlafzimmer" if "schlafzimmer" in sat else "wohnzimmer"),
                            ("HUSH_WAIT_S", 0.5)):
            p = unittest.mock.patch.object(wake_learn, name, value)
            p.start()
            self.addCleanup(p.stop)

    def wake(self, sat=SAT, stopped=()):
        """The satellite starts listening, exactly as Home Assistant reports it."""
        with unittest.mock.patch.object(satellite_quiet, "hush", return_value=list(stopped)):
            ha_events._satellite(change(sat, "idle", "listening"))
            p = wake_learn._PENDING.get(sat)
            if p:
                self.assertTrue(p["hush"].wait(2))

    def hear(self, text, pcm=SECOND * 3):
        with unittest.mock.patch.dict(remote_ask._HEAR, {"rec": FakeRecognizer(text)}):
            return remote_ask.hear(pcm)

    def ask(self, text, status):
        with unittest.mock.patch.object(remote_ask, "_ask", return_value={"text": "", "status": status}):
            return remote_ask.ask(text, 0, "")

    def log(self):
        if not wake_learn.LOG.exists():
            return []
        return [json.loads(line) for line in wake_learn.LOG.read_text(encoding="utf-8").splitlines()]


class FalseWake(Base):
    def test_stop_after_a_wake_with_nothing_to_stop(self):
        self.ring = SECOND * 4
        with unittest.mock.patch.object(wake_learn, "_context", return_value={
                "ambience": "tv", "players": ["Fire TV"], "pc_audio": True, "sensitivity": "Very sensitive"}):
            self.wake()
        self.hear("Stopp.")
        self.ask("Stopp.", "quiet")
        (e,) = self.log()
        self.assertEqual((e["kind"], e["confirmed"], e["satellite"], e["room"]), ("stopp", True, SAT, "wohnzimmer"))
        self.assertEqual((e["ambience"], e["players"], e["pc_audio"], e["sensitivity"]),
                         ("tv", ["Fire TV"], True, "Very sensitive"))
        self.assertNotIn("Stopp", json.dumps({k: v for k, v in e.items() if k != "kind"}))   # never the words
        after, trigger = sorted(e["clips"])
        self.assertTrue(after.endswith("-stopp-after.wav") and trigger.endswith("-stopp-trigger.wav"))
        self.assertEqual((wake_learn.CLIPS / after).stat().st_size, 44 + len(SECOND) * 3)
        self.assertEqual((wake_learn.CLIPS / trigger).stat().st_size, 44 + len(SECOND) * 4)

    def test_every_stop_phrase_and_a_stop_behind_tv_words(self):
        for text, status in (("Hör auf!", "quiet"), ("Schon gut, Jarvis.", "quiet"),
                             ("And he runs it in for the touchdown. Stopp!", "ignored")):
            self.wake()
            self.hear(text)
            self.ask(text, status)
        self.assertEqual([(e["kind"], e["confirmed"]) for e in self.log()], [("stopp", True)] * 3)

    def test_a_stop_that_cut_off_an_answer_is_a_real_stop(self):
        self.wake(stopped=["media_player.schlafzimmer"])
        self.hear("Stopp.")
        self.ask("Stopp.", "quiet")
        self.assertEqual(self.log(), [])

    def test_a_real_request_is_no_false_wake(self):
        self.wake()
        self.hear("Wie wird das Wetter morgen?")
        self.ask("Wie wird das Wetter morgen?", "ok")
        self.ask("Stopp.", "quiet")          # a later stop without a new wake-up belongs to nothing
        self.assertEqual(self.log(), [])

    def test_a_request_between_wake_and_stop(self):
        self.wake()
        self.hear("Stopp die Musik.")
        self.ask("Stopp die Musik.", "ok")
        self.assertEqual(self.log(), [])

    def test_a_microphone_jarvis_opened_himself_is_no_wake(self):
        with unittest.mock.patch("jarvis_mcp._ha"):
            satellite_followup._open(SAT)     # the question back: "Meinen Sie ...?"
        self.wake()
        self.hear("Nein, stopp.")
        self.ask("Nein, stopp.", "quiet")
        self.assertEqual(self.log(), [])

    def test_stop_too_late(self):
        self.wake()
        p = wake_learn._PENDING[SAT]
        p["at"] -= wake_learn.WINDOW_S + 1
        self.ask("Stopp.", "quiet")
        self.assertEqual(self.log(), [])

    def test_hush_never_reported_is_only_suspected(self):
        with unittest.mock.patch.object(satellite_quiet, "ENABLED", False):
            ha_events._satellite(change(SAT, "idle", "listening"))
        self.hear("Stopp.")
        self.ask("Stopp.", "quiet")
        (e,) = self.log()
        self.assertEqual((e["kind"], e["confirmed"]), ("stopp", False))

    def test_nobody_said_anything_is_suspected_with_audio(self):
        self.wake(BED)
        self.hear("", SECOND * 5)
        (e,) = self.log()
        self.assertEqual((e["kind"], e["confirmed"], e["room"]), ("silent", False, "schlafzimmer"))
        self.assertEqual(len(e["clips"]), 1)

    def test_ignored_words_are_suspected_without_audio(self):
        self.wake()
        self.hear("Und damit zurück ins Studio nach Köln.")
        self.ask("Und damit zurück ins Studio nach Köln.", "ignored")
        (e,) = self.log()
        self.assertEqual((e["kind"], e["confirmed"], e["clips"]), ("ignored", False, []))
        self.assertFalse(list(wake_learn.CLIPS.glob("*.wav")) if wake_learn.CLIPS.exists() else [])

    def test_trigger_clip_only_in_the_pc_room(self):
        self.ring = SECOND * 4
        self.wake(BED)
        self.hear("Stopp.")
        self.ask("Stopp.", "quiet")
        (e,) = self.log()
        self.assertEqual([c.rsplit("-", 1)[-1] for c in e["clips"]], ["after.wav"])

    def test_switched_off(self):
        with unittest.mock.patch.object(wake_learn, "ENABLED", False):
            self.wake()
            self.hear("Stopp.")
            self.ask("Stopp.", "quiet")
        self.assertEqual(self.log(), [])


class Sensitivity(Base):
    def false_wake(self):
        self.wake()
        self.hear("Stopp.")
        self.ask("Stopp.", "quiet")

    def test_proposal_after_three_in_a_week_and_only_once(self):
        self.false_wake()
        self.false_wake()
        self.assertEqual(proposals.pending(), [])
        self.false_wake()
        self.false_wake()
        (p,) = proposals.pending()
        self.assertEqual((p["kind"], p["payload"]["entity"], p["payload"]["option"]),
                         ("wake_sensitivity", SELECT, "Moderately sensitive"))
        self.assertIn("3 Fehlauslösungen", p["why"])
        self.assertIn("mittel", proposals.describe(p))

    def test_accept_sets_the_select_and_nothing_else(self):
        for _ in range(3):
            self.false_wake()
        (p,) = proposals.pending()
        with unittest.mock.patch("jarvis_mcp._ha") as ha:
            out = proposals.accept(p["id"])
        ha.assert_called_once_with("/api/services/select/select_option",
                                   {"entity_id": SELECT, "option": "Moderately sensitive"})
        self.assertIn("mittel", out)
        with unittest.mock.patch("jarvis_mcp._ha") as ha:
            self.assertIn("keine", wake_learn.apply({"entity": "switch.steckdose", "option": "Slightly sensitive"}))
            self.assertIn("keine", wake_learn.apply({"entity": SELECT, "option": "off"}))
        ha.assert_not_called()

    def test_counted_since_the_last_change_and_never_below_the_lowest(self):
        self.states[SELECT]["last_changed"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + 60))
        for _ in range(3):
            self.false_wake()
        self.assertEqual(proposals.pending(), [])      # all three happened before the setting was changed
        self.states[SELECT].update(state="Slightly sensitive", last_changed="2026-11-01T10:00:00+00:00")
        self.false_wake()
        self.assertEqual(proposals.pending(), [])

    def test_declined_is_not_asked_again_this_week(self):
        for _ in range(3):
            self.false_wake()
        proposals.decline(proposals.pending()[0]["id"])
        self.false_wake()
        self.assertEqual(proposals.pending(), [])

    def test_suspected_ones_do_not_count(self):
        for _ in range(4):
            self.wake()
            self.hear("")
        self.assertEqual(proposals.pending(), [])


class Telling(Base):
    def test_router_answers_locally(self):
        self.assertIn("keine Fehlauslösung", router.run_local("Wie oft wurdest du falsch ausgelöst?"))
        self.wake()
        self.hear("Stopp.")
        self.ask("Stopp.", "quiet")
        self.wake()
        self.hear("")
        out = router.run_local("Wie viele Fehlauslösungen gab es?")
        self.assertIn("1 sichere Fehlauslösung (Wohnzimmer 1) und 1 vermutete", out)
        self.assertIn("2 Aufnahmen", out)

    def test_stop_itself_still_stops(self):
        with unittest.mock.patch.object(satellite_quiet, "hush", return_value=[]) as hush:
            self.assertTrue(router.run_local("Stopp"))
        hush.assert_called_once()

    def test_export_for_the_training(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
        import wake_negatives
        self.ring = SECOND * 4
        for _ in range(2):
            self.wake()
            self.hear("Stopp.")
            self.ask("Stopp.", "quiet")
            time.sleep(1.1)                    # clip names carry the second
        self.wake()
        self.hear("")
        first = sorted(wake_learn.CLIPS.glob("*-trigger.wav"))[0]
        copies, held = wake_negatives.plan(wake_learn.entries(), wake_learn.CLIPS, lambda f: f == first)
        self.assertEqual(held, [first])        # "Jarvis" was really said there: never a counter-example
        self.assertEqual(sorted(s for _, s in copies), ["jwfalse_room_1"] * 3 + ["jwfalse_trig_1"])

    def test_clips_are_pruned(self):
        wake_learn.CLIPS.mkdir(parents=True, exist_ok=True)
        with unittest.mock.patch.object(wake_learn, "KEEP", 2):
            for i in range(4):
                (wake_learn.CLIPS / f"2026010{i}-000000-x-stopp-after.wav").write_bytes(b"x")
            wake_learn._prune()
        self.assertEqual(sorted(f.name[:8] for f in wake_learn.CLIPS.glob("*.wav")), ["20260102", "20260103"])


if __name__ == "__main__":
    unittest.main()
