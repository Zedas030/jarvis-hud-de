"""
Questions from Home Assistant's voice satellite (the Voice PE): it is only the ear, the PC does the rest.

  /api/hear   the satellite's audio -> text, on the same GPU Whisper the voice engine uses (hear())
  /api/ask    the text -> Jarvis' answer (ask()). It goes to this server's own /api/run on the loopback, the
              way the voice engine does it, so router, tools, cards, taint and audit stay exactly as they are.
              With speak=True the answer is said on the room's speaker sentence by sentence while Claude is
              still writing, as at the PC (RoomVoice); Home Assistant only gets back what was not said.

Both live on the remote port (behind `tailscale serve`) and take a key from devices.py (kind "ask", handed
out by pair.html?schluessel). A card cannot be confirmed from the satellite: it is denied at once and the
answer says so, instead of waiting for a HUD nobody is looking at.
"""
import http.client
import json
import os
import queue
import re
import threading
import time

import jarvis_log

log = jarvis_log.get("remote_ask")

# Home Assistant's agent gives up after 55 s (ha/custom_components/jarvis/const.py); answer before that.
TIMEOUT_S = float(os.environ.get("JARVIS_ASK_TIMEOUT_S", "50"))
MAX_TEXT = 500

HINT = ("Die Frage kam über den Sprach-Satelliten von Home Assistant{room}. Die Antwort wird vorgelesen: "
        "kurz, ganze Sätze, keine Listen, keine Formatierung. Das Mikrofon hört auch Fernseher und Hintergrund: "
        "Ist der Text offensichtlich nicht an dich gerichtet (Fernseh- oder Hintergrundgespräch, Wortsalat ohne "
        "erkennbare Frage oder Bitte), antworte nur mit IGNORIEREN und nichts sonst. Im Zweifel normal antworten. "
        "Eine Rückfrage stellst du als letzten Satz; danach hört der Satellit ohne Aktivierungswort zu. Beginnt "
        "die Antwort auf deine Rückfrage mit Ja oder Nein, gilt das als Zustimmung oder Ablehnung, auch wenn der "
        "Rest unverständlich ist. Der Text kommt aus der Spracherkennung: Verhörer deutest du stillschweigend "
        "nach dem naheliegenden Sinn, ohne darauf hinzuweisen.")
# Like Alexa's check whether speech was meant for her (19.11.2026): what the model marks as not addressed to
# Jarvis is dropped silently — nothing said, nothing in the diary (memory.log_turn skips it too).
IGNORE = "IGNORIEREN"
CARD_NOTE = "Das braucht eine Bestätigung auf einer Karte, das geht nur am PC oder im HUD."
LATE_NOTE = "Das dauert länger als gedacht. Die Antwort steht gleich im Verlauf."
NO_ANSWER = "Dazu habe ich keine Antwort bekommen."

_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MARK = re.compile(r"[*`#>|]+")
_URL = re.compile(r"https?://\S+")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def speakable(text):
    """Markdown and emoji read aloud sound silly ("Sternchen Sternchen"): plain sentences only."""
    text = _LINK.sub(r"\1", str(text or ""))
    text = _URL.sub("", text)
    text = _EMOJI.sub("", _MARK.sub("", text))
    lines = [ln.strip(" -•\t") for ln in text.splitlines()]
    out = []
    for ln in lines:
        if not ln:
            continue
        out.append(ln if ln[-1] in ".!?:;," else ln + ".")
    return re.sub(r"\s+([.,!?;:])", r"\1", " ".join(" ".join(out).split()))


def hint_for(room=""):
    return HINT.format(room=f" im Raum {room}" if room else "")[:1000]


def ignored(text, partial=False):
    """The answer is (or, while it streams, may still become) the IGNORE marker."""
    t = re.sub(r"[^A-ZÄÖÜ]", "", str(text or "").upper())
    if partial:
        return bool(t) and IGNORE.startswith(t[:len(IGNORE)]) if len(t) <= len(IGNORE) else t.startswith(IGNORE)
    return t == IGNORE


# ── the room's speaker is the mouth ───────────────────────────
# The Voice PE is the ear, the room's speaker the mouth (Nutzer, 17.11.2026): its own little speaker sounds
# poor next to the Sonos. Off (or the speaker fails): the text goes back to Home Assistant, and since the
# pipeline has no text-to-speech (scripts/ha_voice_setup.py) the satellite stays silent; the HUD has it.
ROOM_SPEAKER = os.environ.get("JARVIS_ASK_ROOM_SPEAKER", "1").strip().lower() not in ("0", "off", "false", "no", "aus")


def room_speaker(room):
    """The entity of `room`'s speaker (config/home.json -> speakers) if Jarvis can talk there now, else ""."""
    if not (ROOM_SPEAKER and room):
        return ""
    import sonos
    import whereabouts
    target = whereabouts.speaker(room)
    if not target or not sonos.label(target) or not sonos._available(target) or sonos.hushed(target):
        return ""
    return target


def _tts_cfg():
    try:
        import voice_config
        return voice_config.load().tts
    except Exception:  # noqa: BLE001 — the splitter's defaults are fine
        return {}


class RoomVoice:
    """Says an answer on the room's speaker while it is still being written, as the voice engine does at the
    PC: the first sentence (or clause) goes out as soon as it is complete. On a Sonos every further sentence
    joins the same live announcement (sonos.say), an Echo queues them (alexa_voice.say). The first sentence
    that fails stops it — that one and all after it are left for the satellite to read."""

    def __init__(self, target):
        import voice_tts
        self.target = target
        self.splitter = voice_tts.Splitter(_tts_cfg())
        self.q = queue.Queue()
        self.lock = threading.Lock()
        self.spoken, self.left = [], []
        self.failed = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="room-voice")
        self.thread.start()

    def feed(self, full, final=False):
        """`full` is the answer so far; sentences completed since the last call are spoken."""
        for chunk in self.splitter.feed(full, final=final):
            self.add(chunk)

    def add(self, text):
        text = speakable(text)
        if text:
            self.q.put(text)

    def cancel(self):
        """A satellite started listening (satellite_quiet.py): stop now, nothing more is said."""
        import alexa_voice
        import sonos
        with self.lock:
            self.failed = True
        try:
            if alexa_voice.is_alexa(self.target):
                alexa_voice.stop(self.target, hard=True)
            else:
                sonos.stop(self.target)
        except Exception as e:  # noqa: BLE001
            log.warning("room speaker %s stop: %s", self.target, str(e)[:120])

    def finish(self, timeout):
        """-> the sentences not handed to the speaker (failed, or still waiting after `timeout`)."""
        self.q.put(None)
        self.thread.join(timeout)
        with self.lock:
            self.failed = self.failed or self.thread.is_alive()
            while True:
                try:
                    text = self.q.get_nowait()
                except queue.Empty:
                    break
                if text is not None:
                    self.left.append(text)
            return list(self.left)

    def _say(self, text):
        import alexa_voice
        import events
        import sonos
        import voice
        if alexa_voice.is_alexa(self.target):
            alexa_voice.say(self.target, text)
        else:
            chunks, mime, _ = voice.speak_stream(text)
            sonos.say(self.target, chunks, mime, text, quiet=events.sleeping())

    def _run(self):
        while True:
            text = self.q.get()
            if text is None:
                return
            with self.lock:
                skip = self.failed
            if not skip:
                try:
                    self._say(text)
                    with self.lock:
                        self.spoken.append(text)
                    continue
                except Exception as e:  # noqa: BLE001 — sonos.Fallback, HA down, no voice credits
                    log.warning("room speaker %s: %s", self.target, str(e)[:160])
            with self.lock:
                self.failed = True
                self.left.append(text)


# ── asking ────────────────────────────────────────────────────
def _post(port, token, path, body, timeout):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    c.request("POST", path, body=json.dumps(body), headers={
        "Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}",
        "Content-Type": "application/json", "X-Jarvis-Token": token})
    return c, c.getresponse()


def _run(text, port, token, room, deadline, timeout, on_text, flags=None):
    """/api/run on the loopback -> (answer so far, status, cards denied). `on_text(answer so far)` after each piece.
    `flags["quiet"]` is set when the answer is one to show, not say (router._Quiet: "Stopp", a music move)."""
    flags = {} if flags is None else flags
    reply, status, denied = [], "ok", 0
    try:
        conn, resp = _post(port, token, "/api/run",
                           {"message": text, "typed": False, "hint": hint_for(room)}, timeout)
    except OSError as e:
        log.warning("loopback /api/run: %s", str(e)[:120])
        return "Ich komme gerade nicht an mich selbst heran. Versuch es gleich noch einmal.", "error", 0
    try:
        if resp.status == 409:
            return "Ich beantworte gerade schon etwas anderes. Frag mich gleich noch einmal.", "busy", 0
        if resp.status != 200:
            return "Das hat nicht geklappt.", "error", 0
        while True:
            if time.time() > deadline:
                status = "timeout"
                break
            line = resp.readline()
            if not line:
                break
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            t = ev.get("t")
            if t == "quiet":
                flags["quiet"] = True
            elif t == "delta":
                reply.append(ev.get("text") or "")
                on_text("".join(reply))
            elif t == "confirm":
                # nobody can tap the card from here: say no right away, the run goes on without it
                denied += 1
                try:
                    c2, r2 = _post(port, token, "/api/permission/answer", {"id": ev.get("id"), "allow": False}, 5)
                    r2.read()
                    c2.close()
                except OSError:
                    pass
            elif t == "error":
                status = "error"
                reply.append(" " + str(ev.get("message") or ""))
            elif t == "complete":
                break
    except OSError as e:   # socket timeout while Claude is still thinking
        log.info("ask stopped: %s", str(e)[:120])
        status = "timeout"
    finally:
        conn.close()
    return "".join(reply), status, denied


_ACTIVE = {"asks": 0, "voices": []}       # running asks and their room voices, for hush()
_ACTIVE_LOCK = threading.Lock()


def hush():
    """A satellite started listening: the answer still being said for an earlier satellite question stops, and
    its run is cancelled — else the new question meets "Ich beantworte gerade schon etwas anderes". -> how many."""
    with _ACTIVE_LOCK:
        voices, asks = list(_ACTIVE["voices"]), _ACTIVE["asks"]
    for v in voices:
        v.cancel()
    if asks:
        import runtime
        runtime.cancel_active()
    return max(asks, len(voices))


def ask(text, port, token, room="", speak=False, timeout=None):
    with _ACTIVE_LOCK:
        _ACTIVE["asks"] += 1
    try:
        out = _ask(text, port, token, room, speak, timeout)
        try:
            import wake_learn             # "Stopp" with nothing to stop = the satellite woke up by mistake
            wake_learn.asked(text, out.get("status"))
        except Exception as e:  # noqa: BLE001
            log.warning("wake_learn: %s", str(e)[:120])
        return out
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE["asks"] -= 1


def _ask(text, port, token, room="", speak=False, timeout=None):
    """One question -> {"text": what the satellite still has to read out, "status": ok|busy|card|timeout|error,
    "spoken_on": the speaker's label when the room's speaker said all of it}. speak=True: say it on the
    room's speaker while it is written; whatever that speaker could not say comes back as "text"."""
    timeout = TIMEOUT_S if timeout is None else timeout
    deadline = time.time() + timeout
    if speak:
        # the satellite itself never answers: the room the user is in does (room_scope.answer_room) —
        # the satellite's own room only when several rooms or none see someone
        import room_scope
        heard_in, room = room, room_scope.answer_room(room)
        if room != heard_in:
            log.info("Satellit im Raum %s, Antwort im %s (Präsenz)", heard_in or "ohne Bereich", room or "-")
    target = room_speaker(room) if speak else ""
    voice_out = RoomVoice(target) if target else None
    if voice_out:
        with _ACTIVE_LOCK:
            _ACTIVE["voices"].append(voice_out)
    try:
        return _answer(text, port, token, room, deadline, timeout, voice_out, target)
    finally:
        if voice_out:
            with _ACTIVE_LOCK:
                _ACTIVE["voices"].remove(voice_out)


def _answer(text, port, token, room, deadline, timeout, voice_out, target):
    flags = {}

    def on_text(full):
        if voice_out and not flags.get("quiet") and not ignored(full, partial=True):   # never say "IGNORIEREN"
            voice_out.feed(full)
    raw, status, denied = _run(text, port, token, room, deadline, timeout, on_text, flags)
    if status == "ok" and (flags.get("quiet") or ignored(raw)) and voice_out:
        voice_out.finish(timeout=0.5)             # nothing to say: let its thread end
    if status == "ok" and ignored(raw):
        log.info("Satellit: nicht an mich gerichtet, verworfen: %s", text[:80])
        return {"text": "", "status": "ignored"}
    if status == "ok" and flags.get("quiet"):
        # "Jarvis, stopp" (19.11.2026): saying "Gestoppt" would be the very thing he was told to stop
        return {"text": "", "status": "quiet"}
    notes = []
    if denied:
        status = "card" if status == "ok" else status
        notes.append(CARD_NOTE)
    if status == "timeout":
        notes.append(LATE_NOTE)
    answer = speakable(raw)
    if not answer and not notes:
        notes.append(NO_ANSWER)
    out = {"text": " ".join([answer] + notes).strip(), "status": status}
    if voice_out is None:
        return out
    voice_out.feed(raw, final=True)
    for n in notes:
        voice_out.add(n)
    # the last sentences are still being voiced; Home Assistant gives up 5 s after `timeout`
    left = voice_out.finish(timeout=max(2.0, min(10.0, deadline + 4 - time.time())))
    if voice_out.spoken and not left:
        import satellite_followup
        import sonos
        out["spoken_on"] = sonos.label(target)    # the satellite stays silent, the room's speaker said it
        try:
            satellite_followup.after_answer(raw, target)   # a question back: the satellite listens again
        except Exception as e:  # noqa: BLE001
            log.warning("follow-up: %s", str(e)[:120])
    elif voice_out.spoken:
        out["text"] = " ".join(left)              # said in part: the satellite reads the rest
    return out


# ── hearing ───────────────────────────────────────────────────
# The Voice PE's ear on the PC: Home Assistant sends what the satellite heard to /api/hear and gets the text
# back from the same GPU Whisper the voice engine uses — measured 17.11.2026 on the same five sentences:
# 0.16–0.29 s and fewer slips over HA and Tailscale, 7–17 s on the Pi's Whisper, which stays only as the
# fallback for a PC that is off.
HEAR_RATE = 16000
HEAR_MAX_S = 30
_HEAR = {"rec": None}


def _recognizer():
    if _HEAR["rec"] is None:
        import voice_config
        import voice_stt
        _HEAR["rec"] = voice_stt.Recognizer(voice_config.load().stt)
    return _HEAR["rec"]


def warm():
    """Home Assistant calls this when the satellite starts listening: Whisper may have been freed while idle
    (voice.unload_stt), and loading it takes ~3 s — better while the user is still speaking."""
    import voice
    if voice._WHISPER.get("model") is None and voice.local_stt_available():
        threading.Thread(target=voice._fw_model, daemon=True, name="hear-warm").start()


def hear(pcm):
    """16 kHz 16-bit mono PCM -> {"text", "status": ok|empty|error, "ms"}. Same beam, vocabulary and ghost
    filter as the voice engine (voice_stt.Recognizer.final)."""
    t = time.time()
    try:
        rec = _recognizer()
        if not rec.available:
            return {"text": "", "status": "error", "ms": 0}
        text = rec.final(pcm)
    except Exception as e:  # noqa: BLE001 — CUDA trouble, model download: HA falls back to the Pi
        log.warning("hear: %s", str(e)[:160])
        return {"text": "", "status": "error", "ms": int((time.time() - t) * 1000)}
    try:
        import wake_learn                 # keeps the audio of a wake-up nobody wanted
        wake_learn.heard(pcm, text)
    except Exception as e:  # noqa: BLE001
        log.warning("wake_learn: %s", str(e)[:120])
    return {"text": text, "status": "ok" if text else "empty", "ms": int((time.time() - t) * 1000)}
