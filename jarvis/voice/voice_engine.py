"""
The full-duplex voice engine: one Session per connected microphone (the PC's browser, the iPad,
later a satellite in the room). 16-kHz frames come in, PCM for the speaker and JSON events go out.

    IDLE → LISTENING → THINKING → SPEAKING → (INTERRUPTED → LISTENING | RESUMING → SPEAKING) → IDLE

Per frame (20 ms, from the WebSocket thread, ~0.1 ms of work):
  LISTENING    Silero VAD starts an utterance; partials steer the adaptive endpoint; the final
               transcript goes through the wake word check and then to /api/run on this server
               (loopback, NDJSON) — router, cache, Claude and the stand-ins stay as they are.
  THINKING     speech again shortly after the turn was sent: the run is cancelled, both halves
               are glued, the endpoint learns to wait longer.
  SPEAKING     the model's deltas become sentences (voice_tts), each synthesised once and kept.
               The player streams them to the client (or a Sonos / Echo). Speech over his voice:
               duck after `duck_ms`, pause after `confirm_ms` → INTERRUPTED.
  INTERRUPTED  the interruption clip is transcribed and classified: noise / his own echo →
               RESUMING; backchannel → RESUMING; stop word → done; words → a new turn, with the
               spoken part (not the rest) in the conversation window; nothing said for
               `resume_after_ms` → RESUMING.
  RESUMING     the interrupted sentence plays again from its start, then the rest.

Several microphones: each is its own Session. Hear two the same words, the device named for the user's room
in config/home.json → mic_rooms (the iPad in the bedroom) takes them, the other drops them (_other_mic).

The client protocol lives in voice_ws.py; this module only calls `send(dict)` and
`send_audio(bytes)`. Everything that waits (STT, HTTP, synthesis) runs on worker threads; the frame
path never blocks.
"""
import json
import os
import pathlib
import re
import threading
import time
import urllib.error
import urllib.request

import pc_audio
import voice_config
import voice_dsp
import voice_endpoint
import voice_metrics
import voice_state as st
import voice_stt
import voice_tts
import voice_vad
import voice_wake
import jarvis_log

log = jarvis_log.get(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORD = re.compile(r"(?:[^\W\d_]|\d){2,}", re.U)

# Two microphones hear the same words (the PC through the wall, the iPad on the bedside table): the one in the
# room the presence sensors see the user in wins. config/home.json → mic_rooms maps a room to a paired
# device's name ({"Schlafzimmer": "iPad"}). Another session then waits MIC_HOLD_S after its own speech end
# for that device to end an utterance too (within MIC_SAME_S of its own) and drops the words if it did.
# Without a preferred device in the room, or with it not connected, everything stays as before.
MIC_HOLD_S = float(os.environ.get("JARVIS_MIC_HOLD_S", "1.5"))
MIC_SAME_S = float(os.environ.get("JARVIS_MIC_SAME_S", "3"))


def _words(text):
    """Words of two letters or more — "Wie" is one word, not a request."""
    return len(WORD.findall(text or ""))


def _norm(text):
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


def preferred_mic():
    """The paired device whose microphone counts in the room the user is in ("" = no preference)."""
    try:
        import jarvis_profile
        import whereabouts
        room = whereabouts.room()
        rooms = jarvis_profile.ha("mic_rooms", {}) or {}
        return str(rooms.get(room) or "").strip() if room else ""
    except Exception:  # noqa: BLE001 — no Home Assistant, no preference
        return ""


def device_name(device):
    """A paired device's name ("iPad") for its id; the PC and unknown ids keep what they sent."""
    try:
        import devices
        for d in devices.listing():
            if d["id"] == device:
                return d["name"] or device
    except Exception:  # noqa: BLE001
        pass
    return device


class Shared:
    """What every session shares: the recogniser, the config, the server's address."""
    def __init__(self, port, token, cfg=None):
        self.cfg = cfg or voice_config.load()
        self.port, self.token = port, token
        self.rec = voice_stt.Recognizer(self.cfg.stt)
        voice_metrics.configure(self.cfg.metrics.get("file"))
        self.lock = threading.Lock()
        self.sessions = {}
        self.heard = {}                         # device name → when its last utterance ended (mic_rooms)
        self.preferred = preferred_mic          # swapped in tests

    def mic_connected(self, name):
        return any(s.alive and s.device_name.lower() == name.lower() for s in list(self.sessions.values()))

    def run_url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"


class Session:
    def __init__(self, shared, send, send_audio, device="pc", name=None, clock=time.time):
        self.sh = shared
        self.cfg = shared.cfg
        self.send, self.send_audio = send, send_audio
        self.device = device
        self.device_name = device_name(device)
        self.clock = clock
        self.name = name or f"{device}-{int(clock()) % 100000}"
        e = self.cfg.engine
        self.rate, self.frame_ms, self.out_rate = int(e.sample_rate), int(e.frame_ms), int(e.out_rate)
        self.frame_bytes = int(self.rate * self.frame_ms / 1000) * 2
        self.machine = st.Machine(self.name, log_path=e.get("state_log"), clock=clock)
        self.machine.on_change(self._on_state)
        self.vad = voice_vad.Vad(self.cfg.vad)
        self.tracker = voice_vad.Tracker(self.cfg.vad.start_threshold, self.cfg.vad.stop_threshold,
                                         self.frame_ms, self.cfg.vad.min_speech_ms)
        self.endpoint = voice_endpoint.Endpointer(self.cfg.endpoint)
        self.guard = voice_dsp.EchoGuard(frame_ms=self.frame_ms, rate=self.rate)
        self.lock = threading.RLock()
        self.alive = True
        # microphone
        self.preroll = bytearray()
        self.preroll_max = int(self.rate * 2 * e.preroll_ms / 1000)
        self.utt = bytearray()                  # the utterance being recorded
        self.utt_gen = 0
        self.partial_text = None
        self.partial_gen = -1
        self.speech_end_at = None
        self.wait_ms = None
        self.ptt = False
        self.muted = False
        self.ambience = ""                     # "", "music", "tv", "sport", "noise" (from the HUD)
        # the runner exists whenever Whisper is there; whether partials run is decided per utterance
        # (auto = only once the model sits on the GPU, which it does after its first use)
        self.partials = None
        if self.sh.rec.available:
            self.partials = voice_stt.PartialRunner(self.sh.rec, self.cfg.endpoint.partial_every_ms, self._on_partial)
        # conversation
        self.awake_until = 0.0
        self.name_until = 0.0                   # the part of the window opened by "Jarvis" itself
        self.last_sent = None                   # {"text", "at", "glues"}: for the continue window
        self.glue = None
        self.glue_n = 0                         # pieces already glued onto the request in self.glue
        self.own_audio = False                  # this PC session is speaking (pc_audio.own)
        self.pending_confirm = None
        self.run = None                         # the active LLM run (dict)
        self.turn = None                        # voice_metrics.Turn
        # speaking
        self.splitter = voice_tts.Splitter(self.cfg.tts)
        self.synth = voice_tts.Synth(self.cfg.tts, self.out_rate, self._on_audio, self._on_synth_done)
        self.sentences = []                     # Sentence objects of the current answer, in order
        self.next_sid = 1
        self.play_index = 0                     # next sentence the player sends
        self.played = set()                     # ids the client finished
        self.playing_id = None                  # the id the client plays right now (from its reports)
        self.player_cond = threading.Condition(self.lock)
        self.paused = False
        self.ducked = False
        self.answer_text = ""
        self.answer_message = ""
        self.answer_done = False
        self.output = ""                        # "" = the device itself, else a media_player / notify entity
        self.room_speaker = False
        self.barge_run_ms = 0
        self.barge_first_at = None
        self.clip = None                        # bytearray of the interruption
        self.clip_tracker = voice_vad.Tracker(self.cfg.vad.barge_threshold, self.cfg.vad.stop_threshold, self.frame_ms, 0)
        self.clip_started_at = None
        self.paused_at = None
        self.resume_from = 0
        self.say_queue = []
        threading.Thread(target=self._player, daemon=True, name="voice-player").start()
        threading.Thread(target=self._ticker, daemon=True, name="voice-ticker").start()
        self._emit("ready", vad=self.vad.kind, partials=self.partials_on, stt=self.sh.rec.available,
                   config={"frame_ms": self.frame_ms, "sample_rate": self.rate, "out_rate": self.out_rate})

    # ── plumbing ────────────────────────────────────────────────
    def _emit(self, t, **kw):
        try:
            self.send(dict(kw, t=t))
        except Exception as e:  # noqa: BLE001 — the client is gone; close() follows
            log.warning("send failed: %s", str(e)[:80])

    def _on_state(self, old, new, why, at):
        speaking = new in (st.SPEAKING, st.INTERRUPTED, st.RESUMING)
        if self.device == "pc" and speaking != self.own_audio:
            self.own_audio = speaking           # his voice from the browser is not a video playing
            pc_audio.own(speaking)
        self._emit("state", state=new, prev=old, why=why, at=at)

    def log(self, text):
        self._emit("log", text=text)

    def close(self):
        self.alive = False
        self._cancel_run("session closed")
        self.synth.stop()
        if self.partials:
            self.partials.stop()
        with self.lock:
            self.player_cond.notify_all()
        self.machine.force(st.IDLE, "session closed")
        if self.own_audio:
            self.own_audio = False
            pc_audio.own(False)

    def awake(self, strict=False):
        """strict: TV, music or a video plays — then only a window opened by "Jarvis" counts,
        not the follow-up after an answer (the TV kept every answer going with the next line)."""
        if self.ptt or self.pending_confirm:
            return True
        conversation = not strict or self.cfg.wake.conversation_with_ambience
        return self.clock() < (self.awake_until if conversation else self.name_until)

    def ambient(self):
        """Something in the room talks that is not the user: TV, music (Home Assistant via the HUD)
        or sound from this PC itself (a YouTube video, pc_audio.py)."""
        if self.ambience in ("music", "tv", "sport"):
            return True
        return self.device == "pc" and bool(self.cfg.wake.pc_audio) and pc_audio.playing()

    def wake_mode(self):
        try:
            import wakeword
            return wakeword.MODE
        except Exception:  # noqa: BLE001
            return "off"

    def open_window(self, seconds, by_name=False):
        self.awake_until = max(self.awake_until, self.clock() + seconds)
        if by_name:
            self.name_until = max(self.name_until, self.clock() + seconds)
        self._emit("window", until=self.awake_until)

    # ── frames from the microphone ──────────────────────────────
    def feed(self, frame):
        if not self.alive or self.muted or len(frame) < self.frame_bytes:
            return
        frame = voice_dsp.denoise(frame)
        if self.room_speaker:
            frame = self.guard.cancel(frame)
        p = self.vad.probability(frame)
        level = voice_dsp.rms(frame)
        with self.lock:
            self.preroll += frame
            del self.preroll[:-self.preroll_max]
            state = self.machine.state
            if state in (st.IDLE, st.LISTENING):
                self._listen_frame(frame, p)
            elif state == st.THINKING:
                self._thinking_frame(frame, p)
            elif state == st.SPEAKING:
                self._speaking_frame(frame, p, level)
            elif state == st.INTERRUPTED:
                self._interrupted_frame(frame, p)
            # RESUMING is a moment long; the frame is just kept in the preroll
        self._emit_level(p, level)

    _last_level = 0.0

    def _emit_level(self, p, level):
        now = self.clock()
        if now - self._last_level >= 0.1:
            self._last_level = now
            self._emit("level", p=round(p, 2), rms=round(level, 4))

    def _listen_frame(self, frame, p):
        if self.machine.state == st.IDLE:
            self.machine.go(st.LISTENING, "Mikrofon")
        ev = self.tracker.feed(p)
        if ev == "start":
            self._begin_utterance()
        if not self.tracker.speaking:
            return
        self.utt += frame
        if self.tracker.silence_ms == 0:
            self.speech_end_at = None
        elif self.speech_end_at is None:
            self.speech_end_at = self.clock() - self.tracker.silence_ms / 1000.0
        if self.tracker.speech_ms > self.cfg.vad.max_speech_ms:
            self.log("Dauergeräusch verworfen (Musik?)")
            self._drop_utterance()
            return
        if self.partials_on and self.tracker.speech_ms >= 600 and self.tracker.speech_ms % 200 == 0:
            self.partials.update(self.utt, self.utt_gen)
        self._check_endpoint()

    @property
    def partials_on(self):
        return bool(self.partials) and self.sh.rec.partials_enabled

    def _begin_utterance(self):
        self.utt_gen += 1
        self.utt = bytearray(self.preroll)
        self.partial_text, self.partial_gen = None, -1
        self.speech_end_at = None
        self.wait_ms = None
        self.turn = voice_metrics.Turn("voice", clock=self.clock)
        self.turn.mark("speech_start")
        self.turn.info["awake"] = self.awake()
        self._emit("hearing", awake=self.awake())

    def _drop_utterance(self):
        self.tracker.reset()
        self.utt = bytearray()
        self.partial_text = None
        if self.partials:
            self.partials.clear()
        self._emit("listening")

    def _on_partial(self, text, gen):
        with self.lock:
            if gen != self.utt_gen:
                return
            self.partial_text, self.partial_gen = text, gen
            self.wait_ms = None                 # recompute with the new text
        self._emit("partial", text=text)

    def _check_endpoint(self):
        if not self.tracker.speaking or self.tracker.silence_ms == 0:
            return
        if self.ptt:
            return                              # the button decides
        if self.wait_ms is None:
            text = self.partial_text if self.partials_on else None
            if text is not None and voice_wake.is_bare(text):
                self.wait_ms = self.endpoint.base          # a bare "Jarvis" needs no long wait
            else:
                self.wait_ms = self.endpoint.wait_for(text)
        if self.tracker.silence_ms >= self.wait_ms:
            self._finish_utterance()

    def _finish_utterance(self, forced=False):
        speech_ms = self.tracker.speech_ms
        pcm = bytes(self.utt)
        reason = self.endpoint.reasons(self.partial_text if self.partials_on else None)
        self.tracker.reset()
        self.utt = bytearray()
        if self.partials:
            self.partials.clear()
        if speech_ms < self.cfg.vad.min_speech_ms and not forced:
            self._emit("listening")
            return
        turn = self.turn or voice_metrics.Turn("voice", clock=self.clock)
        ended = turn.mark("speech_end", self.speech_end_at or self.clock())
        self.sh.heard[self.device_name] = ended
        turn.mark("endpoint")
        turn.info["endpoint_reason"] = reason
        turn.info["wait_ms"] = self.wait_ms
        gen = self.utt_gen
        self.machine.go(st.THINKING, f"Satzende ({reason}, {self.wait_ms or 0} ms)")
        self._emit("transcribing")
        threading.Thread(target=self._transcribe_and_run, args=(pcm, gen, turn, bool(self.ptt or forced)),
                         daemon=True, name="voice-turn").start()

    def _transcribe_and_run(self, pcm, gen, turn, ptt):
        self._debug_wav("utterance", pcm)
        try:
            text = self.sh.rec.final(pcm) if self.sh.rec.available else ""
        except Exception as e:  # noqa: BLE001
            self.log(f"Spracherkennung fehlgeschlagen: {str(e)[:80]}")
            text = ""
        turn.mark("stt_done")
        other = self._other_mic(turn.t.get("speech_end")) if text else ""
        with self.lock:
            if not self.alive or self.machine.state != st.THINKING or gen != self.utt_gen:
                return                          # the user went on talking, or a newer turn exists
            if not text:
                self._back_to_listening("nichts verstanden")
                return
            if other:
                self.log(f"das {other} hört zu: „{text[:60]}“")
                self._back_to_listening(f"{other} im Raum")
                return
            self._emit("final", text=text)
            if self.pending_confirm:
                self._voice_confirm(text)
                self._back_to_listening("Freigabe per Stimme")
                return
            strict = self.wake_mode() != "off" and self.ambient()
            why = "" if ptt else self._not_for_me(text)
            if why:
                self.log(f"{why}: „{text[:60]}“")
                if self.glue:                   # the TV went on, not the user: his request alone
                    text, self.glue = self.glue, None
                    self.last_sent = {"text": text, "at": self.clock(), "glues": self.glue_n}
                    self._start_run(text, turn, typed=False)
                else:
                    self._back_to_listening(why)
                return
            awake = ptt or self.awake(strict) or bool(self.glue)
            ok, rest = voice_wake.check(text, awake, strict)
            if self.wake_mode() == "off":
                text = rest if ok and rest else text          # "Jarvis, ..." is stripped even without a wake word
            else:
                if not ok:
                    self.log(f"nicht für mich: „{text[:60]}“")
                    self._back_to_listening("ohne „Jarvis“")
                    return
                if voice_wake.is_bare(text):
                    self.open_window(self.cfg.wake.window_s, by_name=True)
                    self._emit("chime")
                    self.log("ich höre")
                    self._back_to_listening("nur „Jarvis“")
                    return
                text = rest or text
            glues = 0
            if self.glue:
                text = (self.glue + " " + text).strip()
                self.glue = None
                glues = self.glue_n + 1
                self.endpoint.learn(user_continued=True)
            else:
                self.endpoint.learn(user_continued=False)
            self.last_sent = {"text": text, "at": self.clock(), "glues": glues}
            self._start_run(text, turn, typed=False)

    def _not_for_me(self, text):
        """Why an utterance is not the user's, or "": English commentary or a foreign script
        (the TV — he is spoken to in German), or his own last answer heard back from the room."""
        if self.wake_mode() != "off" and self.cfg.wake.drop_foreign and voice_wake.foreign(text):
            return "fremde Sprache"
        if not voice_wake.START.match(text) and _words(text) >= 5 and                 self.guard.text_match(text, within_s=self.cfg.wake.echo_window_s) >=                 max(0.75, self.cfg.barge.echo_text_ratio):
            voice_metrics.event("echo_false_alarms")
            return "eigenes Echo"
        return ""

    def _other_mic(self, ended):
        """The preferred device's name when it heard this utterance too (or still may), else "".
        Waits up to MIC_HOLD_S past the end of speech for the other session's own speech end."""
        pref = self.sh.preferred() or ""
        if not pref or pref.lower() == self.device_name.lower() or not self.sh.mic_connected(pref):
            return ""
        ended = ended if ended is not None else self.clock()

        def heard_too():
            at = next((t for name, t in self.sh.heard.items() if name.lower() == pref.lower()), None)
            return at is not None and abs(at - ended) <= MIC_SAME_S

        deadline = time.monotonic() + max(0.0, MIC_HOLD_S - max(0.0, self.clock() - ended))
        while not heard_too():
            if time.monotonic() >= deadline or not self.alive:
                break
            time.sleep(0.05)
        return pref if heard_too() else ""

    def _back_to_listening(self, why):
        if self.machine.state in (st.THINKING, st.SPEAKING, st.INTERRUPTED, st.RESUMING):
            self.machine.go(st.LISTENING, why)
        self._emit("listening")

    def _voice_confirm(self, text):
        allow = None
        if re.search(r"best(ä|ae)tig", text, re.I) and not re.search(r"nicht|kein", text, re.I):
            allow = True
        elif re.search(r"\b(nein|stopp?|abbrechen|ablehnen|nicht)\b", text, re.I):
            allow = False
        if allow is None:
            self.log("sag „bestätigt“ oder „nein“")
            return
        try:
            self._post("/api/permission/answer", {"id": self.pending_confirm, "allow": allow})
        except Exception as e:  # noqa: BLE001
            self.log(f"Freigabe nicht gesendet: {str(e)[:60]}")

    # ── push-to-talk, typed text, notices ──────────────────────
    def ptt_on(self):
        with self.lock:
            self.ptt = True
            self.open_window(30)
            if self.machine.state in (st.SPEAKING, st.INTERRUPTED, st.RESUMING):
                self._stop_speaking("Push-to-Talk")
            if self.machine.state == st.THINKING:
                self._cancel_run("Push-to-Talk")
                self.machine.go(st.LISTENING, "Push-to-Talk")
            if self.machine.state == st.IDLE:
                self.machine.go(st.LISTENING, "Push-to-Talk")
            if not self.tracker.speaking:
                self.tracker.speaking = True
                self.tracker.speech_ms = self.frame_ms
                self._begin_utterance()

    def ptt_off(self):
        with self.lock:
            self.ptt = False
            if self.tracker.speaking:
                self.speech_end_at = self.clock()
                self._finish_utterance(forced=True)

    def text(self, message, typed=True, speak=True):
        """A typed question from the HUD: answered like a spoken one, spoken unless speak=False."""
        with self.lock:
            if self.machine.state in (st.SPEAKING, st.INTERRUPTED, st.RESUMING):
                self._stop_speaking("neue Frage")
            if self.machine.state == st.THINKING:
                self._cancel_run("neue Frage")
            turn = voice_metrics.Turn("typed", clock=self.clock)
            turn.mark("speech_end")
            turn.mark("endpoint")
            turn.mark("stt_done")
            if self.machine.state != st.THINKING:
                self.machine.go(st.THINKING, "getippt")
            self._start_run(message, turn, typed=typed, speak=speak)

    def say(self, text):
        """A notice or alarm text to read aloud, when nothing else is going on."""
        with self.lock:
            self.say_queue.append(text)
            self._drain_say()

    def _drain_say(self):
        if not self.say_queue or self.machine.state not in (st.IDLE, st.LISTENING) or self.run:
            return
        text = self.say_queue.pop(0)
        self._new_answer("(Meldung)")
        self.answer_done = True
        self.turn = voice_metrics.Turn("notice", clock=self.clock)
        self.turn.mark("speech_end")
        self.turn.mark("stt_done")
        self.turn.mark("llm_done")
        for chunk in self.splitter.feed(text, final=True):
            self._queue_sentence(chunk)
        self.machine.go(st.SPEAKING, "Meldung")
        self.player_cond.notify_all()

    def stop(self, why="Stopp-Knopf"):
        with self.lock:
            if self.machine.state in (st.SPEAKING, st.INTERRUPTED, st.RESUMING):
                self._stop_speaking(why)
                self._finish_turn(interrupted=True, why=why)
            elif self.machine.state == st.THINKING:
                self._cancel_run(why)
                self.machine.go(st.LISTENING, why)
            self.say_queue.clear()

    # ── the LLM run (loopback to /api/run) ──────────────────────
    def _post(self, path, body, stream=False, timeout=30):
        req = urllib.request.Request(self.sh.run_url(path), data=json.dumps(body).encode(), method="POST",
                                     headers={"content-type": "application/json", "X-Jarvis-Token": self.sh.token,
                                              "Origin": f"http://127.0.0.1:{self.sh.port}", "X-Jarvis-Voice": "1"})
        resp = urllib.request.urlopen(req, timeout=timeout)
        if stream:
            return resp
        try:
            return json.loads(resp.read() or b"{}")
        finally:
            resp.close()

    def _start_run(self, message, turn, typed=False, speak=True, hint=""):
        self._new_answer(message)
        self.turn = turn
        turn.info["text"] = message[:80]
        self.answer_done = False
        run = {"message": message, "typed": typed, "speak": speak, "hint": hint, "cancelled": False, "resp": None,
               "at": self.clock()}
        self.run = run
        self._emit("run_start", message=message, typed=typed)
        threading.Thread(target=self._run_llm, args=(run,), daemon=True, name="voice-llm").start()

    def _new_answer(self, message):
        self.synth.cancel()
        self.splitter.reset()
        self.sentences = []
        self.play_index = 0
        self.played = set()
        self.playing_id = None
        self.paused = False
        self.ducked = False
        self.answer_text = ""
        self.answer_message = message
        self.resume_from = 0
        self.output = self._output_for_device()
        self.room_speaker = bool(self.output)
        self._emit("flush")

    def _output_for_device(self):
        try:
            import sonos
            return sonos.output_for(self.device)
        except Exception:  # noqa: BLE001
            return ""

    def _run_llm(self, run):
        turn = self.turn
        try:
            resp = self._post("/api/run", {"message": run["message"], "typed": run["typed"], "hint": run.get("hint") or "",
                                           "fresh": bool(re.match(r"^/new\b", run["message"]))}, stream=True, timeout=600)
        except urllib.error.HTTPError as e:
            self._run_error(run, f"HTTP {e.code}")
            return
        except Exception as e:  # noqa: BLE001
            self._run_error(run, str(e)[:120])
            return
        run["resp"] = resp
        first = True
        try:
            for raw in resp:
                if run["cancelled"]:
                    break
                line = raw.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                self._run_event(run, ev, turn, first)
                first = False
        except (OSError, ValueError) as e:
            if not run["cancelled"]:
                self._run_error(run, str(e)[:120])
                return
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
        with self.lock:
            if run["cancelled"] or self.run is not run:
                return
            self.run = None
            turn.mark("llm_done")
            self.answer_done = True
            text = voice_tts.plain(self.answer_text)
            if run["speak"]:
                for chunk in self.splitter.feed(self.answer_text, final=True):
                    self._queue_sentence(chunk)
            if not self.sentences or not run["speak"]:
                self._finish_turn(interrupted=False, why="Antwort ohne Ton" if text else "keine Antwort")
            else:
                self.player_cond.notify_all()

    def _run_event(self, run, ev, turn, first):
        t = ev.get("t")
        if t == "delta":
            with self.lock:
                if run["cancelled"]:
                    return
                if first or not self.answer_text:
                    turn.mark("llm_first_token")
                self.answer_text += ev.get("text") or ""
                if run["speak"]:
                    for chunk in self.splitter.feed(self._speakable(run), final=False):
                        self._queue_sentence(chunk)
        elif t == "confirm":
            self.pending_confirm = str(ev.get("id") or "")
        elif t == "confirm_done":
            self.pending_confirm = None
        elif t == "route":
            turn.info["route"] = ev.get("model") or "?"
        elif t == "latency":
            turn.info["claude_first_ms"] = ev.get("ms")
        self._emit("run", ev=ev)

    def _speakable(self, run):
        """A typed question may get a long answer: only its first paragraph is read aloud."""
        if not run["typed"]:
            return self.answer_text
        cut = re.search(r"\n\s*\n", self.answer_text)
        return self.answer_text[:cut.start()] if cut else self.answer_text

    def _run_error(self, run, msg):
        with self.lock:
            if run["cancelled"] or self.run is not run:
                return
            self.run = None
            self.log(f"Anfrage fehlgeschlagen: {msg}")
            self._emit("run", ev={"t": "error", "message": f"Keine Verbindung zum Gehirn: {msg}"})
            self._new_answer(run["message"])
            self.answer_done = True
            for chunk in self.splitter.feed("Entschuldigung, Sir, ich komme gerade nicht an mein Gehirn.", final=True):
                self._queue_sentence(chunk)
            if self.machine.state == st.THINKING:
                self.machine.go(st.SPEAKING, "Fehlermeldung")
            self.player_cond.notify_all()

    def _cancel_run(self, why):
        run = self.run
        if not run:
            return False
        run["cancelled"] = True
        self.run = None
        resp = run.get("resp")
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=self._post_quiet, args=("/api/cancel", {}), daemon=True).start()
        self._emit("run", ev={"t": "note", "message": f"abgebrochen: {why}"})
        return True

    def _post_quiet(self, path, body):
        try:
            self._post(path, body)
        except Exception:  # noqa: BLE001
            pass

    def _thinking_frame(self, frame, p):
        """Speech again shortly after a turn went out: he was not finished."""
        ls = self.last_sent
        if not ls or self.clock() - ls["at"] > self.cfg.endpoint.continue_window_ms / 1000.0:
            return
        ev = self.tracker.feed(p)
        if ev == "start":
            limit = self.cfg.wake.max_glue_with_ambience if self.ambient() else self.cfg.wake.max_glue
            if ls.get("glues", 0) >= limit:
                return                          # a TV never stops talking: no endless request
            self.glue, self.glue_n = ls["text"], ls.get("glues", 0)
            self._cancel_run("Sie reden weiter")
            self.log("Sie reden weiter — warte auf den Rest")
            voice_metrics.event("user_continued")
            self.machine.go(st.LISTENING, "Nutzer spricht weiter")
            self._begin_utterance()
            self.utt += frame

    # ── speaking ───────────────────────────────────────────────
    def _queue_sentence(self, text):
        s = voice_tts.Sentence(self.next_sid, text)
        self.next_sid += 1
        self.sentences.append(s)
        if len(self.sentences) == 1 and self.turn:
            self.turn.mark("llm_first_sentence")
        self.synth.submit(s)
        self.guard.remember(voice_tts.plain(text))
        if self.machine.state == st.THINKING:
            self.machine.go(st.SPEAKING, "erster Satz")
        self.player_cond.notify_all()

    def _on_audio(self, s, pcm):
        with self.lock:
            if self.turn and self.sentences and s is self.sentences[0]:
                self.turn.mark("tts_first_audio")
            self.player_cond.notify_all()

    def _on_synth_done(self, s):
        with self.lock:
            self.player_cond.notify_all()

    def _player(self):
        """Walks the sentences in order and streams each to the output; waits while paused."""
        while self.alive:
            with self.lock:
                while self.alive and (self.paused or self.machine.state not in (st.SPEAKING, st.RESUMING)
                                      or self.play_index >= len(self.sentences)):
                    if (self.machine.state == st.SPEAKING and self.answer_done and not self.paused
                            and self.play_index >= len(self.sentences) and self._all_played()):
                        self._finish_turn(interrupted=False, why="fertig gesprochen")
                    self.player_cond.wait(0.1)
                if not self.alive:
                    return
                if self.machine.state == st.RESUMING:
                    self.machine.go(st.SPEAKING, "weiter am Satzanfang")
                s = self.sentences[self.play_index]
                self.play_index += 1
                output = self.output
            if output:
                self._play_room(s, output)
            else:
                self._play_client(s)

    def _all_played(self):
        return self.output or all(s.id in self.played for s in self.sentences) or not self.sentences

    def _play_client(self, s):
        sent = 0
        begun = False
        gap = voice_dsp.silence(self.cfg.tts.gap_ms, self.out_rate) if self.play_index > 1 else b""
        deadline = self.clock() + self.cfg.tts.provider_timeout_s
        while self.alive:
            with self.lock:
                if self.paused or self.machine.state not in (st.SPEAKING, st.RESUMING) or getattr(s, "cancelled", False):
                    return
                chunk = bytes(s.pcm[sent:])
                done = s.done
            if chunk:
                if not begun:
                    begun = True
                    self._emit("audio_begin", id=s.id, text=voice_tts.plain(s.text), again=getattr(s, "begun", False))
                    s.begun = True
                    if self.turn:
                        self.turn.mark("first_audio_out")
                    if gap:
                        self.send_audio(gap)
                self.send_audio(chunk)
                sent += len(chunk)
            if done:
                break
            if not chunk and self.clock() > deadline:
                self.log(f"Stimme antwortet nicht: „{voice_tts.plain(s.text)[:40]}“")
                break
            with self.lock:
                self.player_cond.wait(0.05)
        if begun:
            self._emit("audio_end", id=s.id, samples=len(s.pcm) // 2)
        elif s.error or s.done:
            with self.lock:
                self.played.add(s.id)            # nothing to play: counts as spoken
                self.player_cond.notify_all()

    def _play_room(self, s, eid):
        """A Sonos (announcement stream) or an Echo (Alexa reads the text). Timing is estimated."""
        import alexa_voice
        import sonos
        s.ev.wait(self.cfg.tts.provider_timeout_s)
        with self.lock:
            if self.paused or self.machine.state not in (st.SPEAKING, st.RESUMING):
                return
            self.playing_id = s.id
            if self.turn:
                self.turn.mark("first_audio_out")
        self.guard.start()
        try:
            if alexa_voice.is_alexa(eid):
                secs = alexa_voice.say(eid, voice_tts.plain(s.text))
            else:
                import events
                sonos.stop(eid)                   # clears a hold from an earlier pause
                chunks = self._raw_chunks(s)
                secs = sonos.say(eid, chunks, s.mime or "audio/mpeg", voice_tts.plain(s.text), quiet=events.quiet_now())
                threading.Thread(target=self._feed_reference, args=(s,), daemon=True).start()
        except Exception as e:  # noqa: BLE001 — the speaker failed: the device plays it itself
            self.log(f"Lautsprecher: {str(e)[:80]} — spreche selbst")
            with self.lock:
                self.output = ""
                self.room_speaker = False
                self.play_index -= 1
            return
        self._emit("room_audio", id=s.id, seconds=secs, speaker=sonos.label(eid))
        end = self.clock() + float(secs or 0)
        while self.alive and self.clock() < end:
            with self.lock:
                if self.paused or self.machine.state not in (st.SPEAKING, st.RESUMING):
                    return
            time.sleep(0.05)
        with self.lock:
            self.played.add(s.id)
            self.player_cond.notify_all()
        self.guard.stop()

    def _raw_chunks(self, s):
        if s.mime.startswith("audio/pcm"):
            s.ev_done.wait(self.cfg.tts.provider_timeout_s)
            rate = int(s.mime.split("rate=")[-1]) if "rate=" in s.mime else 22050
            s.mime = "audio/wav"
            yield voice_dsp.wav_bytes(bytes(s.raw), rate)
            return
        sent = 0
        while True:
            chunk = bytes(s.raw[sent:])
            if chunk:
                sent += len(chunk)
                yield chunk
            if s.done and sent >= len(s.raw):
                return
            time.sleep(0.02)

    def _feed_reference(self, s):
        """The echo canceller / level model gets what the Sonos is playing, as 16 kHz PCM."""
        sent = 0
        while self.alive and (not s.done or sent < len(s.pcm)):
            chunk = bytes(s.pcm[sent:])
            if chunk:
                sent += len(chunk)
                self.guard.reference(voice_dsp.resample(chunk, self.out_rate, self.rate))
            time.sleep(0.05)

    def client_event(self, ev):
        """Reports from the client's player."""
        t = ev.get("t")
        with self.lock:
            if t == "playing":
                self.playing_id = ev.get("id")
            elif t == "ended":
                self.played.add(ev.get("id"))
                if ev.get("id") == self.playing_id:
                    self.playing_id = None
                self.player_cond.notify_all()
            elif t == "idle":
                self.player_cond.notify_all()
            elif t == "ambience":
                self.ambience = str(ev.get("kind") or "")
            elif t == "mute":
                self.muted = bool(ev.get("on"))
            elif t == "wake_heard":
                self.open_window(self.cfg.wake.window_s, by_name=True)
                if self.machine.state in (st.SPEAKING, st.INTERRUPTED, st.RESUMING):
                    self._stop_speaking("Aktivierungswort")
                    self._finish_turn(interrupted=True, why="Aktivierungswort")

    def _speaking_frame(self, frame, p, level):
        if self.pending_confirm:
            return
        import alexa_voice
        if self.output and alexa_voice.is_alexa(self.output):
            # Alexa cannot be paused: only a named request is taken, as a normal utterance
            self._listen_frame(frame, p)
            return
        if self.room_speaker:
            warming = self.guard.observe(level)
            if warming or level <= self.guard.bar():
                p = 0.0
        if p >= self.cfg.vad.barge_threshold:
            if self.barge_run_ms == 0:
                self.barge_first_at = self.clock()
            self.barge_run_ms += self.frame_ms
            if self.barge_run_ms >= self.cfg.barge.duck_ms and not self.ducked:
                self.ducked = True
                self._emit("gain", value=self.cfg.barge.duck_gain, ms=self.cfg.barge.fade_ms)
            if self.barge_run_ms >= self.cfg.barge.confirm_ms:
                self._pause_for_user()
        else:
            if self.ducked and self.barge_run_ms > 0:
                self.ducked = False
                self._emit("gain", value=1.0, ms=self.cfg.barge.fade_ms)
                voice_metrics.event("barge_ins", outcome="blip")
            self.barge_run_ms = 0

    def _pause_for_user(self):
        self.turn = self.turn or voice_metrics.Turn("voice", clock=self.clock)
        self.turn.mark("barge_detect", self.barge_first_at or self.clock())
        self.paused = True
        self.paused_at = self.clock()
        self.barge_run_ms = 0
        self.resume_from = self._current_index()
        self._emit("flush")
        self.turn.mark("output_paused")
        if self.output:
            threading.Thread(target=self._room_pause, daemon=True).start()
        self.machine.go(st.INTERRUPTED, "Sie sprechen")
        self.clip = bytearray(self.preroll)
        self.clip_tracker.reset()
        self.clip_tracker.speaking = True
        self.clip_tracker.speech_ms = self.cfg.barge.confirm_ms
        self.clip_started_at = self.clock()
        voice_metrics.observe("barge_react_ms", (self.clock() - (self.barge_first_at or self.clock())) * 1000)
        self.log("Pause — ich höre")

    def _room_pause(self):
        import alexa_voice
        import sonos
        try:
            if alexa_voice.is_alexa(self.output):
                alexa_voice.stop(self.output, hard=True)
            else:
                sonos.stop(self.output, hold=True)
                if self.cfg.barge.sonos_duck_via_ha:
                    sonos.hold(self.output)
        except Exception as e:  # noqa: BLE001
            self.log(f"Lautsprecher-Pause: {str(e)[:60]}")

    def _current_index(self):
        """Index of the sentence the listener was in when we paused."""
        if self.output:
            pid = self.playing_id
        else:
            pid = self.playing_id
            if pid is None:
                pid = max(self.played) + 1 if self.played else (self.sentences[0].id if self.sentences else None)
        for i, s in enumerate(self.sentences):
            if s.id == pid:
                return i
        return max(0, min(self.play_index - 1, len(self.sentences) - 1))

    def _interrupted_frame(self, frame, p):
        if self.clip is None:
            return                              # the clip is being classified; what follows is the next turn's
        self.clip += frame
        self.clip_tracker.feed(p)
        since = (self.clock() - self.clip_started_at) * 1000
        if self.clip_tracker.silence_ms >= self.cfg.barge.clip_silence_ms or since >= self.cfg.barge.clip_max_ms:
            clip = bytes(self.clip)
            self.clip = None
            speech_ms = self.clip_tracker.speech_ms
            self.clip_tracker.reset()
            if speech_ms < self.cfg.barge.confirm_ms + self.frame_ms * 2:
                self._resume("kurzer Laut")
                voice_metrics.event("barge_ins", outcome="noise")
                return
            spoke_until = self.clock() - (self.cfg.barge.clip_silence_ms / 1000.0 if not since >= self.cfg.barge.clip_max_ms else 0)
            threading.Thread(target=self._classify_interruption, args=(clip, self.paused_at, spoke_until), daemon=True,
                             name="voice-barge").start()
        elif self.clip_tracker.speech_ms <= self.cfg.barge.confirm_ms + self.frame_ms and \
                self.clock() - self.paused_at > self.cfg.barge.resume_after_ms / 1000.0:
            self._resume("nichts gesagt")
            voice_metrics.event("barge_ins", outcome="resume")

    def _classify_interruption(self, clip, paused_at, spoke_until=None):
        self._debug_wav("barge", clip)
        try:
            text = self.sh.rec.final(clip) if self.sh.rec.available else ""
        except Exception as e:  # noqa: BLE001
            self.log(f"Spracherkennung fehlgeschlagen: {str(e)[:60]}")
            text = ""
        with self.lock:
            if not self.alive or self.machine.state != st.INTERRUPTED or self.paused_at != paused_at:
                return
            norm = _norm(text)
            named = voice_wake.names(text)
            self._emit("barge_text", text=text)
            rest = voice_wake.check(text, True)[1] if named else text
            strict = self.cfg.wake.barge_needs_name_with_ambience and self.ambient()
            import alexa_voice
            if self.output and alexa_voice.is_alexa(self.output):
                strict = True
            if not norm:
                self._resume("Geräusch")
                voice_metrics.event("barge_ins", outcome="noise")
            elif self.room_speaker and self.guard.text_match(text) >= self.cfg.barge.echo_text_ratio and not named:
                self._resume("eigenes Echo")
                voice_metrics.event("echo_false_alarms")
                voice_metrics.event("barge_ins", outcome="echo")
            elif norm in {_norm(w) for w in self.cfg.barge.backchannel} or re.fullmatch(r"(m+h*m*|h+m+|ä+h*m*)", norm):
                self._resume(f"Rückmeldung „{text}“")
                voice_metrics.event("barge_ins", outcome="backchannel")
            elif _norm(rest) in {_norm(w) for w in self.cfg.barge.stop_words} or \
                    re.match(r"^(stopp?|halt)\b", _norm(rest)):
                self.log(f"„{text}“ — ich höre auf")
                self._stop_speaking("Stopp-Wort")
                self._finish_turn(interrupted=True, why="Stopp-Wort")
                voice_metrics.event("barge_ins", outcome="stop")
            elif strict and not named:
                self._resume("ohne „Jarvis“ bei Fernsehen/Musik")
                voice_metrics.event("barge_ins", outcome="ignored_ambience")
            elif _words(rest) < 2:
                if named:                        # a bare "Jarvis": stop and listen
                    self._stop_speaking("„Jarvis“")
                    self._finish_turn(interrupted=True, why="„Jarvis“")
                    self.open_window(self.cfg.wake.window_s, by_name=True)
                    self._emit("chime")
                    voice_metrics.event("barge_ins", outcome="name")
                else:
                    self._resume("zu kurz")
                    voice_metrics.event("barge_ins", outcome="noise")
            else:
                self.log(f"gehört: „{text}“")
                self._emit("final", text=text)
                spoken_hint = self._spoken_hint()
                self._stop_speaking("echte Unterbrechung")
                self._finish_turn(interrupted=True, why="Unterbrechung mit Frage")
                voice_metrics.event("barge_ins", outcome="utterance")
                turn = voice_metrics.Turn("voice", clock=self.clock)
                turn.mark("speech_end", spoke_until or self.clock())
                turn.mark("endpoint")
                turn.mark("stt_done")
                self.last_sent = {"text": rest, "at": self.clock()}
                self.machine.go(st.THINKING, "Unterbrechung")
                self._start_run(rest, turn, typed=False, hint=spoken_hint)

    def _spoken_hint(self):
        """Tells the model where it was cut off; the window keeps only the spoken part (see _finish_turn)."""
        n = self.resume_from
        total = len(self.sentences)
        if total and n < total:
            return f"Hinweis: Du wurdest nach Satz {n} von {total} deiner letzten Antwort unterbrochen; antworte auf das Neue."
        return ""

    def _resume(self, why):
        self.machine.go(st.RESUMING, why)
        self.paused = False
        self.ducked = False
        self.clip = None
        self.play_index = self.resume_from
        self.playing_id = None
        self._emit("gain", value=1.0, ms=self.cfg.barge.fade_ms)
        self.log(f"weiter ({why})")
        if self.output:
            threading.Thread(target=self._room_resume, daemon=True).start()
        self.player_cond.notify_all()

    def _room_resume(self):
        import sonos
        try:
            if not __import__("alexa_voice").is_alexa(self.output):
                sonos.stop(self.output)                 # lifts the hold
                sonos.release(self.output)
        except Exception as e:  # noqa: BLE001
            self.log(f"Lautsprecher weiter: {str(e)[:60]}")

    def _stop_speaking(self, why):
        self.paused = True
        self.synth.cancel()
        for s in self.sentences:
            s.cancelled = True
        self._emit("flush")
        if self.output:
            threading.Thread(target=self._room_stop, daemon=True).start()
        self.log(f"unterbrochen ({why})")

    def _room_stop(self):
        import alexa_voice
        import sonos
        try:
            if alexa_voice.is_alexa(self.output):
                alexa_voice.stop(self.output, hard=True)
            else:
                sonos.stop(self.output)
                sonos.release(self.output)
        except Exception:  # noqa: BLE001
            pass

    def _finish_turn(self, interrupted, why):
        """End of SPEAKING: metrics, the spoken part into the conversation window, the follow-up window."""
        turn = self.turn
        if turn:
            turn.mark("speak_done")
            turn.info["interrupted"] = interrupted
            turn.info["sentences"] = len(self.sentences)
            turn.info["spoken"] = len(self.played) if not self.output else self.resume_from if interrupted else len(self.sentences)
            voice_metrics.record(turn, self.cfg.llm.first_word_target_ms)
            self.turn = None
        if interrupted:
            self._remember_spoken()
        self.paused = False
        self.ducked = False
        self.resume_from = 0
        self.playing_id = None
        if self.answer_text and self.answer_message not in ("(Meldung)", "") and not interrupted:
            self.open_window(self.cfg.wake.conversation_s * (1.6 if self.answer_text.rstrip().endswith("?") else 1.0))
        if self.machine.state != st.LISTENING:
            self.machine.go(st.LISTENING, why)
        self._emit("speak_done", interrupted=interrupted, spoken=self._spoken_text())
        self._drain_say()

    def _spoken_text(self):
        if self.output:
            idx = self.resume_from
            return " ".join(voice_tts.plain(s.text) for s in self.sentences[:idx])
        return " ".join(voice_tts.plain(s.text) for s in self.sentences if s.id in self.played)

    def _remember_spoken(self):
        """The conversation window must hold what he said, not what he had planned to say."""
        spoken = self._spoken_text().strip()
        msg = self.answer_message
        if msg in ("", "(Meldung)"):
            return
        try:
            import commands

            def patch(d):
                win = d.get("window") or []
                for w in reversed(win):
                    if w.get("u", "")[:240] == " ".join(msg.split())[:240]:
                        w["a"] = (spoken[:300] + " [unterbrochen]") if spoken else "[unterbrochen, bevor etwas gesagt wurde]"
                        break
                d["window"] = win
            commands.update_state(patch)
        except Exception as e:  # noqa: BLE001
            self.log(f"Gedächtnis nicht angepasst: {str(e)[:60]}")

    # ── timers ─────────────────────────────────────────────────
    def _ticker(self):
        cue_sent = None
        while self.alive:
            time.sleep(0.1)
            try:
                with self.lock:
                    state = self.machine.state
                    if state == st.LISTENING and self.tracker.speaking and self.tracker.silence_ms and not self.ptt:
                        # the client stopped sending frames (muted mid-sentence): close the turn anyway
                        if self.clock() - (self.speech_end_at or self.clock()) > (self.cfg.endpoint.max_ms + 500) / 1000.0:
                            self._finish_utterance()
                    if state == st.INTERRUPTED and self.clip is not None and \
                            self.clip_tracker.speech_ms <= self.cfg.barge.confirm_ms + self.frame_ms and \
                            self.clock() - self.paused_at > self.cfg.barge.resume_after_ms / 1000.0:
                        self._resume("nichts gesagt")
                        voice_metrics.event("barge_ins", outcome="resume")
                    cue_ms = self.cfg.llm.thinking_cue_after_ms
                    if state == st.THINKING and self.run and cue_ms and cue_sent != self.run["at"] \
                            and self.clock() - self.run["at"] > cue_ms / 1000.0:
                        cue_sent = self.run["at"]
                        self._emit("cue", kind="thinking")
                    if state in (st.IDLE, st.LISTENING):
                        self._drain_say()
            except Exception as e:  # noqa: BLE001
                log.warning("ticker: %s", str(e)[:100], exc_info=True)

    # ── debug clips ────────────────────────────────────────────
    def _debug_wav(self, kind, pcm):
        if not self.cfg.engine.debug_audio:
            return
        try:
            d = ROOT / "logs" / "voice_debug"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}.wav").write_bytes(voice_dsp.wav_bytes(pcm, self.rate))
        except OSError:
            pass
