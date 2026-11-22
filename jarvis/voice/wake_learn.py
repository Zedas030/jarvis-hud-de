"""
Learning from false wake-ups: a "Stopp" right after a satellite woke up, with nothing to stop (Nutzer, 22.11.2026).

  "Ich merke ja immer, wenn sie ausgelöst wird, und dann sage ich Stopp."

The satellites (Voice PE, ReSpeaker) detect "Jarvis" on the device (microWakeWord): no score and no audio of
the moment itself ever reaches Home Assistant, only "the satellite is listening" and then what it heard after.
So a wake-up is judged by what follows it:

  stopp     the whole request is a stop word ("Stopp", "Hör auf", "Schon gut") and Jarvis was saying nothing
            that the wake-up could have stopped -> a confirmed false wake-up
  silent    nobody said anything until the satellite gave up (5 s)        -> suspected
  ignored   words not meant for Jarvis (the model answered IGNORIEREN)    -> suspected
  A "Stopp" that cut off an answer (satellite_quiet stopped something) is a real stop and is not recorded.
  A microphone Jarvis opened himself for a question back (satellite_followup) was no wake-up at all.

What is kept per false wake-up (JARVIS_WAKE_LEARN_LOG, one JSON line; never the words):
  time, satellite, room, the wake word sensitivity set in HA, what was playing (TV/music/PC sound), seconds
  until the stop, and for "stopp"/"silent" the audio as WAV in JARVIS_WAKE_LEARN_DIR:
    *-after.wav    what the satellite heard after it woke (the room going on, then "Stopp")
    *-trigger.wav  the last seconds before the wake-up from the PC's microphone, when the satellite stands in
                   the PC's room — the sound that set it off, through another microphone
  Nothing is stored for "ignored" (it may be a conversation).

What is learned from it:
  1. Sensitivity: JARVIS_WAKE_LEARN_PROPOSE confirmed false wake-ups of one satellite within a week (counted
     since the sensitivity was last changed) -> a proposal to set that satellite one step less sensitive. A
     background job never changes anything itself (CLAUDE.md): the user accepts it on a card (proposals.py).
  2. The model: scripts/wake_negatives.py copies the clips into the wake word training set as counter-examples.

  JARVIS_WAKE_LEARN            1 (default) / 0 = off
  JARVIS_WAKE_LEARN_WINDOW_S   30 — what follows a wake-up within this time belongs to it
  JARVIS_WAKE_LEARN_PROPOSE    3 — confirmed false wake-ups per satellite and week before the proposal
  JARVIS_WAKE_LEARN_PC_ROOM    wohnzimmer — the room the PC's microphone hears
  JARVIS_WAKE_LEARN_KEEP       300 — newest clips kept
"""
import json
import os
import pathlib
import re
import threading
import time

import jarvis_log

log = jarvis_log.get(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENABLED = os.environ.get("JARVIS_WAKE_LEARN", "1").strip().lower() not in ("0", "false", "no", "off", "aus")
LOG = pathlib.Path(os.environ.get("JARVIS_WAKE_LEARN_LOG", ROOT / "logs" / "wake_false.jsonl"))
CLIPS = pathlib.Path(os.environ.get("JARVIS_WAKE_LEARN_DIR", ROOT / "logs" / "wake_false"))
WINDOW_S = float(os.environ.get("JARVIS_WAKE_LEARN_WINDOW_S", "30"))
PROPOSE_N = int(os.environ.get("JARVIS_WAKE_LEARN_PROPOSE", "3") or 0)
PC_ROOMS = [w.strip().lower() for w in os.environ.get("JARVIS_WAKE_LEARN_PC_ROOM", "wohnzimmer").split(",") if w.strip()]
KEEP = int(os.environ.get("JARVIS_WAKE_LEARN_KEEP", "300"))
DAYS = 7
RING_S = 6               # the PC microphone's last seconds, kept in memory only (voice_engine.Session.recent)
OPENED_S = 8.0           # listening this soon after Jarvis opened the microphone himself is no wake-up
HUSH_WAIT_S = 3.0        # satellite_quiet's thread says what it stopped; the stop word arrives later than that
RATE = 16000
LEVELS = ["Very sensitive", "Moderately sensitive", "Slightly sensitive"]
LEVEL_DE = {"Very sensitive": "sehr empfindlich", "Moderately sensitive": "mittel", "Slightly sensitive": "wenig empfindlich"}
SENSITIVITY = re.compile(r"^select\.[a-z0-9_]+_wake_word_sensitivity$")
KIND_DE = {"stopp": "Stopp", "silent": "nichts gesagt", "ignored": "nicht an mich gerichtet"}

_LOCK = threading.Lock()
_PENDING = {}            # satellite -> the wake-up still waiting for what follows
_OPENED = {}             # satellite -> when Jarvis opened its microphone himself


# ── what the hooks report ─────────────────────────────────────
def opened(satellite, now=None):
    """satellite_followup: Jarvis opens this microphone himself, without a wake word."""
    with _LOCK:
        _OPENED[satellite] = time.time() if now is None else now
        _PENDING.pop(satellite, None)


def woke(satellite, now=None):
    """satellite_quiet: this satellite just started listening. Called in the HA event loop: no waiting here."""
    if not ENABLED or not satellite:
        return None
    now = time.time() if now is None else now
    with _LOCK:
        if now - _OPENED.get(satellite, 0.0) < OPENED_S:
            _PENDING.pop(satellite, None)
            return None
    p = {"satellite": satellite, "at": now, "stopped": None, "hush": threading.Event(), "pcm": b"",
         "heard_at": None, "ring": _safe(_pc_ring, b""), "context": _safe(lambda: _context(satellite), {})}
    with _LOCK:
        _PENDING[satellite] = p
    return p


def hushed(satellite, stopped):
    """satellite_quiet: what the wake-up stopped ([] = Jarvis was silent)."""
    with _LOCK:
        p = _PENDING.get(satellite)
    if p:
        p["stopped"] = list(stopped or [])
        p["hush"].set()


def _newest(now):
    with _LOCK:
        live = [p for p in _PENDING.values() if now - p["at"] <= WINDOW_S]
        for sat in [s for s, p in _PENDING.items() if now - p["at"] > WINDOW_S]:
            _PENDING.pop(sat, None)
    return max(live, key=lambda p: p["at"]) if live else None


def heard(pcm, text, now=None):
    """remote_ask.hear: the audio a satellite sent and what Whisper made of it."""
    if not ENABLED:
        return None
    now = time.time() if now is None else now
    p = _newest(now)
    if not p or p["heard_at"] is not None:
        return None
    p["pcm"], p["heard_at"] = bytes(pcm or b""), now
    if str(text or "").strip():
        return None
    with _LOCK:
        _PENDING.pop(p["satellite"], None)
    if _real_stop(p):
        return None                      # he woke Jarvis to shut him up and needed no word for it
    return record(p, "silent", False, now)


def asked(text, status, now=None):
    """remote_ask.ask: the request that followed a wake-up and how it ended (quiet, ignored, ok, ...)."""
    if not ENABLED:
        return None
    now = time.time() if now is None else now
    p = _newest(now)
    if not p:
        return None
    with _LOCK:
        _PENDING.pop(p["satellite"], None)
    if status not in ("quiet", "ignored"):
        return None                      # a real request: the wake-up was wanted
    if is_stop(text):
        if _real_stop(p):
            return None
        return record(p, "stopp", p["stopped"] is not None, now)
    if status == "ignored":
        return record(p, "ignored", False, now)
    return None


def _real_stop(p):
    """The wake-up stopped something Jarvis was saying: he was interrupted on purpose."""
    p["hush"].wait(HUSH_WAIT_S if p["stopped"] is None else 0)
    return bool(p["stopped"])


def is_stop(text):
    """The utterance, or its last sentence after the TV's words, is nothing but a stop word."""
    import router
    text = str(text or "").strip()
    last = [s for s in re.split(r"[.!?]+", text) if s.strip()][-1:] or [""]
    return any(bool(router.STOP_TALK.match(router._norm(t))) for t in (text, last[0]))


# ── context ───────────────────────────────────────────────────
def _safe(fn, default):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — learning never gets in the way of listening
        log.debug("wake_learn: %s", str(e)[:120])
        return default


def _pc_ring():
    """The PC microphone's last seconds (raw 16 kHz PCM), b'' when the engine is off or muted."""
    import voice_ws
    for s in voice_ws.sessions():
        if getattr(s, "device", "") == "pc" and getattr(s, "alive", False) and not getattr(s, "muted", False):
            return bytes(getattr(s, "recent", b""))
    return b""


def _states():
    import ha_events
    return {s.get("entity_id"): s for s in ha_events.states() or []}


def sensitivity_entity(satellite):
    return "select." + re.sub(r"_assist_satellite?$", "", satellite.split(".", 1)[-1]) + "_wake_word_sensitivity"


def _context(satellite):
    """What can be read without asking anybody: playing media, the PC's own sound, the sensitivity."""
    import ambience
    import pc_audio
    amb = ambience.status()
    states = _states()
    sens = states.get(sensitivity_entity(satellite)) or {}
    return {"ambience": amb.get("kind") or "", "players": list(amb.get("players") or [])[:4],
            "pc_audio": bool(_safe(pc_audio.playing, False)),
            "sensitivity": sens.get("state") or "", "sensitivity_since": sens.get("last_changed") or ""}


def _room(satellite):
    """The satellite's room in lower case: its area in Home Assistant, else what its name says."""
    import listen_duck
    return _safe(lambda: listen_duck._area(satellite), "") or next(
        (w for w in ("wohnzimmer", "schlafzimmer", "küche", "kueche", "bad", "flur", "büro") if w in satellite.lower()), "")


# ── recording ─────────────────────────────────────────────────
def _short(satellite):
    return re.sub(r"_assist_satellite?$", "", satellite.split(".", 1)[-1])[:40]


def _wav(path, pcm):
    import voice_dsp
    path.write_bytes(voice_dsp.wav_bytes(pcm, RATE))
    return path.name


def record(p, kind, confirmed, now=None):
    """Writes the log line and the clips; -> the entry."""
    now = time.time() if now is None else now
    room = _room(p["satellite"])
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(p["at"]))
    clips = []
    if kind in ("stopp", "silent"):
        try:
            CLIPS.mkdir(parents=True, exist_ok=True)
            base = f"{stamp}-{_short(p['satellite'])}-{kind}"
            if len(p["pcm"]) >= RATE:                       # half a second and more
                clips.append(_wav(CLIPS / f"{base}-after.wav", p["pcm"]))
            near_pc = any(w in f"{p['satellite'].lower()} {room}" for w in PC_ROOMS)
            if kind == "stopp" and near_pc and len(p["ring"]) >= RATE:
                clips.append(_wav(CLIPS / f"{base}-trigger.wav", p["ring"]))
            _prune()
        except OSError as e:
            log.warning("Fehlauslösung: Clip nicht gespeichert (%s)", str(e)[:120])
    ctx = p.get("context") or {}
    entry = {"at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p["at"])), "ts": round(p["at"], 1),
             "kind": kind, "confirmed": bool(confirmed), "satellite": p["satellite"], "room": room,
             "after_s": round((p["heard_at"] or now) - p["at"], 1), "clips": clips,
             "sensitivity": ctx.get("sensitivity", ""), "sensitivity_since": ctx.get("sensitivity_since", ""),
             "ambience": ctx.get("ambience", ""), "players": ctx.get("players", []), "pc_audio": ctx.get("pc_audio", False)}
    with _LOCK:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log.info("Fehlauslösung %s (%s) am %s%s", "bestätigt" if confirmed else "vermutet", KIND_DE.get(kind, kind),
             _short(p["satellite"]), f", lief: {entry['ambience'] or 'PC-Ton'}" if entry["ambience"] or entry["pc_audio"] else "")
    if confirmed:
        _safe(lambda: propose(p["satellite"], now), None)
    return entry


def _prune():
    files = sorted(CLIPS.glob("*.wav"), key=lambda f: f.name)
    for f in files[:max(0, len(files) - KEEP)]:
        try:
            f.unlink()
        except OSError:
            pass


def entries(days=DAYS, now=None):
    now = time.time() if now is None else now
    out = []
    try:
        for line in LOG.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if now - float(e.get("ts") or 0) <= days * 86400:
                out.append(e)
    except OSError:
        pass
    return out


# ── learning: the sensitivity ─────────────────────────────────
def _changed_ts(iso):
    """HA's last_changed ("2026-11-20T12:29:43.123+00:00") -> epoch seconds, 0 when unreadable."""
    import datetime
    try:
        return datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def propose(satellite, now=None):
    """Enough confirmed false wake-ups since the sensitivity was last touched -> a proposal, one step down."""
    now = time.time() if now is None else now
    if not PROPOSE_N:
        return None
    entity = sensitivity_entity(satellite)
    state = _states().get(entity) or {}
    level = state.get("state") or ""
    if level not in LEVELS or level == LEVELS[-1]:
        return None                       # unknown, or already the least sensitive: only a new model helps
    since = _changed_ts(state.get("last_changed"))
    mine = [e for e in entries(DAYS, now) if e.get("satellite") == satellite and e.get("confirmed")
            and float(e.get("ts") or 0) > since]
    if len(mine) < PROPOSE_N:
        return None
    import proposals
    source = f"wake_learn:{satellite}"
    for old in proposals._load():
        if old.get("source") == source and old.get("status") in ("open", "declined") \
                and now - float(old.get("created") or 0) < DAYS * 86400:
            return None                   # already asked this week
    lower = LEVELS[LEVELS.index(level) + 1]
    room = _room(satellite).capitalize() or _short(satellite)
    playing = sorted({e.get("ambience") or ("PC-Ton" if e.get("pc_audio") else "") for e in mine} - {""})
    why = (f"{len(mine)} Fehlauslösungen in 7 Tagen, jeweils mit „Stopp“ abgebrochen"
           + (f"; dabei lief: {', '.join(playing)}" if playing else "")
           + f". Eine Stufe unempfindlicher ({LEVEL_DE[lower]}) löst seltener falsch aus, überhört „Jarvis“ aus der Ferne aber eher.")
    return proposals.add("wake_sensitivity", f"Satellit {room}: Aktivierungswort unempfindlicher stellen", why,
                         {"entity": entity, "option": lower, "from": level, "satellite": satellite, "room": room}, source)


def apply(payload):
    """An accepted proposal: sets the satellite's sensitivity select in Home Assistant, nothing else."""
    entity, option = str(payload.get("entity") or ""), str(payload.get("option") or "")
    if not SENSITIVITY.match(entity) or option not in LEVELS:
        return "Das ist keine Empfindlichkeits-Einstellung eines Satelliten."
    import jarvis_mcp
    try:
        jarvis_mcp._ha("/api/services/select/select_option", {"entity_id": entity, "option": option})
    except Exception as e:  # noqa: BLE001
        return f"Home Assistant hat die Einstellung nicht angenommen ({str(e)[:80]})."
    return f"Aktivierungswort im {payload.get('room') or 'Raum'} steht jetzt auf {LEVEL_DE[option]}."


# ── telling ───────────────────────────────────────────────────
ASK = re.compile(r"fehlausl|falsch (?:ausgel|aktiviert|angesprungen|losgegangen)|grundlos (?:ausgel|angesprungen|losgegangen)")


def summary_text(days=DAYS, now=None):
    es = entries(days, now)
    sure = [e for e in es if e.get("confirmed")]
    unsure = [e for e in es if not e.get("confirmed")]
    if not es:
        return f"In den letzten {days} Tagen habe ich keine Fehlauslösung bemerkt, Sir."
    rooms = {}
    for e in sure:
        r = (e.get("room") or "").capitalize() or _short(e.get("satellite") or "?")
        rooms[r] = rooms.get(r, 0) + 1
    parts = [f"In den letzten {days} Tagen {len(sure)} sichere Fehlauslösung{'en' if len(sure) != 1 else ''}"
             + (" (" + ", ".join(f"{r} {n}" for r, n in sorted(rooms.items())) + ")" if rooms else "")
             + f" und {len(unsure)} vermutete."]
    playing = [e.get("ambience") or ("PC-Ton" if e.get("pc_audio") else "") for e in sure]
    loud = sum(1 for x in playing if x)
    if sure and loud:
        parts.append(f"Bei {loud} davon lief Fernseher, Musik oder Ton am PC.")
    clips = sum(len(e.get("clips") or []) for e in es)
    if clips:
        parts.append(f"{clips} Aufnahmen liegen als Gegenbeispiele für das nächste Training bereit.")
    return " ".join(parts)


def handle(n):
    """Router: 'Wie oft wurdest du falsch ausgelöst?' -> the week's count, locally."""
    return summary_text() if ASK.search(n or "") else None


def reset():
    """For the tests."""
    with _LOCK:
        _PENDING.clear()
        _OPENED.clear()
