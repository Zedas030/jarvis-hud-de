"""
Jarvis' answers spoken by an Echo — the device in the same room as the PC types or talks, Alexa speaks.

An Echo cannot play a clip Jarvis made (Alexa Media Player only lets Alexa read text aloud), so the
answer is spoken with Alexa's voice: notify.alexa_media_* with type "tts", without the announcement
chime. The HUD sends every finished sentence on its own. Each call to Alexa costs a cloud round trip
and the timing is only an estimate, so a call per sentence left a pause after each one: the first
sentence goes out at once, everything that arrives while Alexa speaks is gathered into one block
that goes out when she should be done.

Which Echo: config/home.json → "speakers" entries with "notify"; picked per device like a Sonos
(sonos.py → speech_outputs), on the HUD's output button.
"""
import json
import os
import pathlib
import re
import threading
import time

CHARS_PER_S = float(os.environ.get("JARVIS_ALEXA_CHARS_PER_S", "15"))
# In the quiet hours (JARVIS_QUIET_HOURS) the Echo speaks at this volume and goes back afterwards:
# Alexa reads text at the Echo's own volume, and at 00:17 that was far too loud in bed (04.11.2026).
NIGHT_VOLUME = float(os.environ.get("JARVIS_ALEXA_NIGHT_VOLUME", "0.2"))
# Late in the evening, before "Gute Nacht": the Echo speaks at this volume, whatever it stood at, and goes
# back afterwards (Nutzer, 20.11.2026: 6 of 10 in the living room from 22 h). 0 = the Echo's own volume.
LATE_VOLUME = float(os.environ.get("JARVIS_ALEXA_LATE_VOLUME", "0") or 0)
LATE_FROM = int(os.environ.get("JARVIS_ALEXA_LATE_FROM", "22"))
LATE_UNTIL = 7
RESTORE_AFTER_S = 3.0       # after the last sentence should be done
LAG_S = 1.0                 # cloud round trip before Alexa starts a block
MAX_CHARS = 250             # longer texts fail in Alexa Media Player; split at sentence ends
_TAG = re.compile(r"\[[^\][]{1,90}\]")          # tone tags for the TTS providers ("[ruhig]")
_MARK = re.compile(r"[*_#`>|]+|https?://\S+")
_EMOJI = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F\u200D]+")   # Alexa reads them out by name
_LOCK = threading.Lock()
_BUSY = {}                  # entity -> epoch seconds when the last queued sentence should be done
_TIMERS = {}                # entity -> [threading.Timer] not yet fired
_PENDING = {}               # entity -> {"text"} the next block, not sent yet: new sentences join it
_NIGHT = {}                 # media player -> {"volume": before, "timer": Timer} while turned down for the night
_SAYING = {}                # entity -> when the block last handed to Alexa should be done
_MUTED = {}                 # media player -> {"volume": before, "timer": Timer} while cut off by stop(hard)
# Alexa starts a block late (up to ~5 s after the call, measured 18.11.2026), so a block sent may still be
# playing this long after its estimated end.
LATE_S = 4.0
MUTE_MAX_S = 40.0
# 19.11.2026: the Echo sat at 0.1 for a day — a mute whose way back was lost (restart, the 0.1 misreading
# of 18.11.) is never undone by itself. So the volume Jarvis turned down is written down until it is back,
# a new start puts back what an earlier Jarvis left, and by day an Echo still at the mute level before an
# answer gets its last normal volume again.
ROOT = pathlib.Path(__file__).resolve().parents[2]
STATE_FILE = pathlib.Path(os.environ.get("JARVIS_ALEXA_VOLUME_STATE", ROOT / "logs" / "alexa_volume.json"))
DAY_VOLUME = float(os.environ.get("JARVIS_ALEXA_DAY_VOLUME", "0.5"))     # if no normal volume is known yet
_FILE_LOCK = threading.Lock()


def _state(change=None):
    """Read the state file; with `change(d)`, update and write it. Never raises."""
    with _FILE_LOCK:
        try:
            d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        d.setdefault("good", {})
        d.setdefault("held", {})
        if change:
            change(d)
            try:
                STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                STATE_FILE.write_text(json.dumps(d), encoding="utf-8")
            except OSError as e:
                print(f"  Echo-Lautstärke merken: {str(e)[:120]}", flush=True)
        return d


def _hold(mp, vol, kind, target=None):
    def ch(d):
        d["good"][mp] = vol
        d["held"][mp] = {"volume": vol, "kind": kind}
        if target is not None:
            d["held"][mp]["target"] = target
    _state(ch)


def _release(mp):
    if mp in _state()["held"]:
        _state(lambda d: d["held"].pop(mp, None))


def heal(mp):
    """Before an answer: an Echo left at the mute level gets its normal volume back (20.11.2026: also after
    "Gute Nacht" — it sat at 0.1 all evening because the check only ran by day)."""
    import jarvis_mcp as j
    with _LOCK:
        if mp in _MUTED or mp in _NIGHT:
            return None
    try:
        st = j._ha(f"/api/states/{mp}") or {}
        vol = (st.get("attributes") or {}).get("volume_level")
        if not isinstance(vol, (int, float)):
            return None
        good = _state()["good"]
        if vol > MUTE_FLOOR:
            if abs(good.get(mp, -1) - vol) > 0.02:
                _state(lambda d: d["good"].__setitem__(mp, vol))
            return None
        back = good.get(mp) if good.get(mp, 0) > MUTE_FLOOR else DAY_VOLUME
        j._ha("/api/services/media_player/volume_set", {"entity_id": mp, "volume_level": back})
        print(f"  Echo stand auf {vol:.2f}, zurück auf {back:.2f}", flush=True)
        return back
    except Exception as e:  # noqa: BLE001 — better spoken quietly than not at all
        print(f"  Echo-Lautstärke prüfen: {str(e)[:120]}", flush=True)
        return None


def recover():
    """At start: what an earlier Jarvis left turned down goes back. Never raises."""
    import jarvis_mcp as j
    held = dict(_state()["held"])
    for mp, h in held.items():
        try:
            st = j._ha(f"/api/states/{mp}") or {}
            vol = (st.get("attributes") or {}).get("volume_level")
            ours = (vol <= MUTE_FLOOR if h.get("kind") == "mute"
                    else abs(vol - h.get("target", NIGHT_VOLUME)) <= 0.02) if isinstance(vol, (int, float)) else False
            if ours and not _quiet():
                j._ha("/api/services/media_player/volume_set", {"entity_id": mp, "volume_level": h["volume"]})
                print(f"  Echo-Lautstärke nach Neustart zurück: {mp} {h['volume']}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  Echo-Lautstärke nach Neustart: {str(e)[:120]}", flush=True)
            continue
        _release(mp)
    return bool(held)
# An Echo set to 0 reports 0.1 right after (18.11.2026) — at or below this it still counts as muted by us,
# not as turned by hand; that misreading left the Echo quiet for the next answer.
MUTE_FLOOR = 0.12


def _quiet():
    try:
        import events
        return events.sleeping()          # after "Gute Nacht", not by the clock (17.11.2026)
    except Exception:  # noqa: BLE001
        return False


def _late(now=None):
    hour = time.localtime(now).tm_hour
    return hour >= LATE_FROM or hour < LATE_UNTIL


def voice_volume(now=None):
    """(volume, lower_only) Jarvis speaks at on an Echo right now, or None for the Echo's own volume:
    after "Gute Nacht" NIGHT_VOLUME (only ever turned down), late in the evening LATE_VOLUME (set either way)."""
    if _quiet():
        return NIGHT_VOLUME, True
    if LATE_VOLUME > 0 and _late(now):
        return LATE_VOLUME, False
    return None


def night_down(eid, seconds_left=0.0):
    """At night (voice_volume): the Echo's volume to the night's level before Alexa speaks, back to what it
    was RESTORE_AFTER_S after the text should be done. Turned by hand in between: stays as turned."""
    want = voice_volume()
    if not want:
        return
    target, lower_only = want
    import jarvis_mcp as j
    mp = player(eid)
    with _LOCK:
        held = _NIGHT.get(mp)
        if held:
            held["timer"].cancel()
    if not held:
        try:
            st = j._ha(f"/api/states/{mp}") or {}
            vol = (st.get("attributes") or {}).get("volume_level")
            if not isinstance(vol, (int, float)):
                return
            if vol <= target + 0.01 if lower_only else abs(vol - target) <= 0.01:
                return
            j._ha("/api/services/media_player/volume_set", {"entity_id": mp, "volume_level": target})
        except Exception as e:  # noqa: BLE001 — better spoken than not at all
            print(f"  Echo-Nachtlautstärke: {str(e)[:120]}", flush=True)
            return
        held = {"volume": vol, "target": target}
        _hold(mp, vol, "night", target)
    t = threading.Timer(max(0.0, seconds_left) + RESTORE_AFTER_S, night_up, args=(mp,))
    t.daemon = True
    held["timer"] = t
    with _LOCK:
        _NIGHT[mp] = held
    t.start()


def night_up(mp):
    import jarvis_mcp as j
    with _LOCK:
        held = _NIGHT.pop(mp, None)
        muted = _MUTED.get(mp) if held else None
        if muted:
            # 20.11.2026: cut off (volume 0) while turned down for the night — this found "turned by hand",
            # the mute then went back to the night level and the Echo stayed there. The mute now owns the way back.
            muted["volume"] = held["volume"]
    if not held:
        return
    if muted:
        _hold(mp, held["volume"], "mute")
        return
    _release(mp)
    try:
        st = j._ha(f"/api/states/{mp}") or {}
        vol = (st.get("attributes") or {}).get("volume_level")
        if isinstance(vol, (int, float)) and abs(vol - held["target"]) > 0.02:
            return           # turned by hand meanwhile: that is the volume wanted now
        j._ha("/api/services/media_player/volume_set", {"entity_id": mp, "volume_level": held["volume"]})
    except Exception as e:  # noqa: BLE001
        print(f"  Echo Lautstärke zurück: {str(e)[:120]}", flush=True)


def outputs():
    """[{"id": notify entity, "label": "Echo Wohnzimmer"}] from config/home.json → speakers."""
    import jarvis_profile as profile
    out = []
    for room, sp in sorted((profile.ha("speakers", {}) or {}).items()):
        eid = (sp or {}).get("notify", "") if isinstance(sp, dict) else ""
        if re.fullmatch(r"notify\.alexa_media_[a-z0-9_]+", eid):
            out.append({"id": eid, "label": f"Echo {room}"})
    return out


def is_alexa(eid):
    return str(eid or "").startswith("notify.alexa_media_")


def clean(text):
    text = _EMOJI.sub(" ", _MARK.sub(" ", _TAG.sub(" ", str(text or ""))))
    return " ".join(text.replace("…", ".").replace(" · ", ", ").split())


def pieces(text):
    """The text in parts Alexa accepts, cut after a sentence (or a comma, a space) where needed."""
    text = clean(text)
    out = []
    while len(text) > MAX_CHARS:
        head = text[:MAX_CHARS]
        cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
        if cut < MAX_CHARS // 3:
            cut = max(head.rfind(", "), head.rfind(" "))
        cut = cut + 1 if cut > 0 else MAX_CHARS
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    return out + ([text] if text else [])


def seconds(text):
    return len(text) / CHARS_PER_S + LAG_S


def _send(eid, block):
    import jarvis_mcp as j
    with _LOCK:
        _TIMERS[eid] = [t for t in _TIMERS.get(eid, []) if t.is_alive() and t is not threading.current_thread()]
        if _PENDING.get(eid) is block:
            del _PENDING[eid]
        text = block["text"]
        _SAYING[eid] = time.time() + seconds(text)
    try:
        j._ha(f"/api/services/notify/{eid.split('.', 1)[1]}", {"message": text, "data": {"type": "tts"}})
    except Exception as e:  # noqa: BLE001 — the HUD already shows the text
        print(f"  Echo: {str(e)[:160]}", flush=True)


def say(eid, text, now=None):
    """Queue the text on the Echo; seconds until it should be spoken (the HUD waits that long)."""
    now = time.time() if now is None else now
    parts = pieces(text)
    unmute(player(eid))                   # cut off a moment ago: the new answer must be heard
    heal(player(eid))                     # a mute whose way back was lost earlier
    with _LOCK:
        ahead = max(0.0, _BUSY.get(eid, now) - now)
    night_down(eid, ahead + sum(seconds(p) for p in parts))     # before the first word goes out
    with _LOCK:
        for part in parts:
            block = _PENDING.get(eid)
            if block and len(block["text"]) + 1 + len(part) <= MAX_CHARS:
                block["text"] += " " + part           # same call to Alexa: no new round trip
                _BUSY[eid] += len(part) / CHARS_PER_S
                continue
            start = max(now, _BUSY.get(eid, 0.0))
            block = {"text": part}
            if start > now:
                _PENDING[eid] = block                 # waits for the one before: later sentences join it
            t = threading.Timer(start - now, _send, args=(eid, block))
            t.daemon = True
            _TIMERS.setdefault(eid, []).append(t)
            t.start()
            _BUSY[eid] = start + seconds(part)
    return max(0.0, _BUSY.get(eid, now) - now)


def player(eid):
    """The Echo's media player: config "player", else from the notify name (alexa_media_x -> media_player.x)."""
    import jarvis_profile as profile
    for sp in (profile.ha("speakers", {}) or {}).values():
        if isinstance(sp, dict) and sp.get("notify") == eid and sp.get("player"):
            return sp["player"]
    return "media_player." + eid.split(".", 1)[1].removeprefix("alexa_media_")


def speaking(eid, now=None):
    """Jarvis has a sentence on this Echo that is queued or should still be playing."""
    now = time.time() if now is None else now
    with _LOCK:
        queued = bool(_PENDING.get(eid)) or any(t.is_alive() for t in _TIMERS.get(eid, []))
        return queued or _BUSY.get(eid, 0.0) + LAG_S > now or _SAYING.get(eid, 0.0) + LATE_S > now


def stop(eid, hard=False, now=None):
    """Talked over: sentences not yet sent are dropped; hard also silences the one Alexa is speaking.

    media_stop and a "Stopp" command do not end text Alexa is reading (tested 18.11.2026: she read on to
    the end), volume 0 does within about a second. So the Echo goes to 0 until that block should be over,
    then back to its volume; a new answer of Jarvis turns it back up at once (say)."""
    now = time.time() if now is None else now
    with _LOCK:
        for t in _TIMERS.pop(eid, []):
            t.cancel()
        _BUSY.pop(eid, None)
        _PENDING.pop(eid, None)
        left = _SAYING.pop(eid, 0.0) + LATE_S - now
    if hard and left > 0:
        mute(player(eid), min(left, MUTE_MAX_S))


def mute(mp, seconds_):
    import jarvis_mcp as j
    with _LOCK:
        held = _MUTED.get(mp)
        if held:
            held["timer"].cancel()
    if not held:
        try:
            st = j._ha(f"/api/states/{mp}") or {}
            vol = (st.get("attributes") or {}).get("volume_level")
            if not isinstance(vol, (int, float)) or vol <= MUTE_FLOOR:
                return
            j._ha("/api/services/media_player/volume_set", {"entity_id": mp, "volume_level": 0})
        except Exception as e:  # noqa: BLE001
            print(f"  Echo stumm: {str(e)[:120]}", flush=True)
            return
        held = {"volume": vol}
        _hold(mp, vol, "mute")
    t = threading.Timer(seconds_, unmute, args=(mp,))
    t.daemon = True
    held["timer"] = t
    with _LOCK:
        _MUTED[mp] = held
    t.start()


def unmute(mp):
    """Back to the volume before mute(); turned by hand in between: stays as turned."""
    import jarvis_mcp as j
    with _LOCK:
        held = _MUTED.pop(mp, None)
    if not held:
        return
    held["timer"].cancel()
    _release(mp)
    try:
        st = j._ha(f"/api/states/{mp}") or {}
        vol = (st.get("attributes") or {}).get("volume_level")
        if isinstance(vol, (int, float)) and vol > MUTE_FLOOR:
            return
        j._ha("/api/services/media_player/volume_set", {"entity_id": mp, "volume_level": held["volume"]})
    except Exception as e:  # noqa: BLE001
        print(f"  Echo Lautstärke zurück: {str(e)[:120]}", flush=True)
