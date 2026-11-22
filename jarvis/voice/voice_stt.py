"""
Speech to text for the engine, on the faster-whisper model voice.py already holds.

  Recognizer(cfg.stt)
    .final(pcm16k)                  the transcript of a finished utterance (beam search, ghost filter)
    .partial(pcm16k)                a quick greedy transcript of what is there so far (GPU only)
    .partials_enabled               auto: True when Whisper sits on the GPU
    .vocabulary()                   the prompt Whisper is primed with: config words, WHISPER_LOCAL_PROMPT,
                                    room, device and speaker names from config/home.json

  PartialRunner(recognizer, every_ms, callback)
    .update(pcm16k)                 hand in the growing utterance; a worker transcribes the newest snapshot
                                    at most every `every_ms`, skipping snapshots while busy
    .stop()

Whisper is not a streaming model: a partial is a full pass over the audio so far (0.2 s on the GPU
for 8 s of audio, see VOICE-ANALYSE.md). On the CPU that would take longer than the gap between
partials, so partials stay off there and the endpoint falls back to its no-text wait.
"""
import json
import pathlib
import re
import threading
import time

import voice
import jarvis_log

log = jarvis_log.get(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[2]
GHOSTS = re.compile(
    r"untertitel|amara\.org|vielen dank (fürs|für's|für das) zuschauen|bis zum nächsten mal|swr \d{4}|zdf|"
    r"copyright|^(danke|tschüss|ja|äh|ähm|hm+)\W*$", re.I)
# Room noise sent as speech: the Voice PE handed over 15 s without a word and Whisper wrote "Ja, ja, ja, ja."
# (18.11.2026). A run of filler words only, however often repeated, is not a question.
REPEATS = re.compile(r"^(\W*\b(ja|danke|äh|ähm|hm+|mhm)\b)+\W*$", re.I)   # not "nein": that answers a question
# Whisper's own signs of a segment without speech (the thresholds openai-whisper uses for silence and for
# looping text): very likely no speech and a weak guess, or text that repeats itself.
NO_SPEECH_P = 0.6
WEAK_LOGPROB = -1.0
LOOPING = 2.4


def _ghost_segment(s):
    try:
        if s.no_speech_prob > NO_SPEECH_P and s.avg_logprob < WEAK_LOGPROB:
            return True
        return s.compression_ratio > LOOPING
    except (AttributeError, TypeError):      # a stand-in segment without the numbers: keep it
        return False


NOISE = re.compile(r"^\W*[\[(*♪]?\s*(musik|music|applaus|lachen|gelächter|♪)", re.I)
MARKERS = re.compile(r"[\[(][^\])]{0,40}[\])]")


def home_names():
    """Names a user says at home: rooms, aliases, appliances, speakers, TV (config/home.json)."""
    out = []
    try:
        home = json.loads((ROOT / "config" / "home.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    for pair in home.get("rooms") or []:
        if isinstance(pair, (list, tuple)) and len(pair) > 1:
            out.append(str(pair[1]))
    for names in (home.get("device_aliases") or {}).values():
        if isinstance(names, list):
            out.extend(str(n) for n in names[:3])
    for a in home.get("appliances") or []:
        if isinstance(a, dict) and a.get("name"):
            out.append(str(a["name"]))
    out.extend(str(k) for k in (home.get("speakers") or {}))
    seen, uniq = set(), []
    for n in out:
        if n.lower() not in seen and 2 <= len(n) <= 30:
            seen.add(n.lower())
            uniq.append(n)
    return uniq[:40]


class Recognizer:
    def __init__(self, cfg=None):
        c = cfg or {}
        self.final_beam = int(c.get("final_beam", 5))
        self.partial_beam = int(c.get("partial_beam", 1))
        self.min_chars = int(c.get("min_chars", 2))
        self.words = list(c.get("vocabulary") or [])
        self.partials_cfg = c.get("partials", "auto")
        self._prompt = None
        self._prompt_at = 0
        self.lock = threading.Lock()

    @property
    def available(self):
        return voice.local_stt_available()

    @property
    def on_gpu(self):
        return "Grafikkarte" in (voice._WHISPER.get("name") or "")

    @property
    def partials_enabled(self):
        if self.partials_cfg in (True, "true", "on", "1", 1):
            return True
        if self.partials_cfg in (False, "false", "off", "0", 0):
            return False
        return self.on_gpu

    def vocabulary(self):
        if self._prompt is None or time.time() - self._prompt_at > 300:
            words = list(self.words)
            words += [w.strip(" .,") for w in (voice.LOCAL_STT_PROMPT or "").split(",") if w.strip(" .,")]
            words += home_names()
            seen, uniq = set(), []
            for w in words:
                if w.lower() not in seen:
                    seen.add(w.lower())
                    uniq.append(w)
            self._prompt = ", ".join(uniq[:60]) + "."
            self._prompt_at = time.time()
        return self._prompt

    def _run(self, pcm, beam):
        import numpy as np
        audio = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32) / 32768.0
        if audio.size < 16000 * 0.25:
            return ""
        voice._WHISPER["busy"] += 1
        try:
            segs, _ = voice._fw_model().transcribe(
                audio, language=voice.LOCAL_STT_LANG, beam_size=beam, vad_filter=beam > 1,
                condition_on_previous_text=False, initial_prompt=self.vocabulary(),
                hotwords=voice.LOCAL_STT_HOTWORDS or None)
            text = " ".join(s.text.strip() for s in segs if not _ghost_segment(s)).strip()
        finally:
            voice._WHISPER["busy"] -= 1
            voice._WHISPER["last_use"] = time.time()
        return text

    def clean(self, text):
        text = " ".join(MARKERS.sub(" ", text or "").split()).strip()
        if len(re.sub(r"[^\w]", "", text)) < self.min_chars:
            return ""
        if NOISE.match(text):
            return ""
        if GHOSTS.search(text) and len(text) < 60:
            return ""
        if REPEATS.match(text):
            return ""
        return text

    def final(self, pcm):
        import heard_fix
        with self.lock:
            return heard_fix.fix(self.clean(self._run(pcm, self.final_beam)))   # his usual mishearings put right

    def partial(self, pcm):
        if not self.lock.acquire(blocking=False):
            return None                     # a final run is on: the partial can wait
        try:
            return " ".join(MARKERS.sub(" ", self._run(pcm, self.partial_beam)).split())
        finally:
            self.lock.release()


class PartialRunner:
    """One worker thread per session; transcribes the newest snapshot of the utterance."""
    def __init__(self, recognizer, every_ms, callback):
        self.rec, self.every, self.cb = recognizer, every_ms / 1000.0, callback
        self.cond = threading.Condition()
        self.snapshot = None
        self.generation = 0
        self.alive = True
        self.last_run = 0.0
        threading.Thread(target=self._loop, daemon=True, name="voice-partials").start()

    def update(self, pcm, generation):
        with self.cond:
            self.snapshot = bytes(pcm)
            self.generation = generation
            self.cond.notify()

    def clear(self):
        with self.cond:
            self.snapshot = None

    def stop(self):
        with self.cond:
            self.alive = False
            self.cond.notify()

    def _loop(self):
        while True:
            with self.cond:
                while self.alive and (self.snapshot is None or time.time() - self.last_run < self.every):
                    self.cond.wait(0.1)
                if not self.alive:
                    return
                pcm, gen = self.snapshot, self.generation
                self.snapshot = None
            self.last_run = time.time()
            try:
                text = self.rec.partial(pcm)
            except Exception as e:  # noqa: BLE001 — a failing partial must not stop the session
                log.warning("partial failed: %s", str(e)[:100])
                continue
            if text is not None:
                try:
                    self.cb(text, gen)
                except Exception as e:  # noqa: BLE001
                    log.warning("partial callback failed: %s", str(e)[:100])
