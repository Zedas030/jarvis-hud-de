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
import os
import re
import threading
import time

CHARS_PER_S = float(os.environ.get("JARVIS_ALEXA_CHARS_PER_S", "15"))
# In the quiet hours (JARVIS_QUIET_HOURS) the Echo speaks at this volume and goes back afterwards:
# Alexa reads text at the Echo's own volume, and at 00:17 that was far too loud in bed (04.11.2026).
NIGHT_VOLUME = float(os.environ.get("JARVIS_ALEXA_NIGHT_VOLUME", "0.2"))
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


def _quiet():
    try:
        import events
        return events.quiet_now()
    except Exception:  # noqa: BLE001
        return False


def night_down(eid, seconds_left=0.0):
    """In the quiet hours: the Echo's volume down to NIGHT_VOLUME before Alexa speaks, back to what it
    was RESTORE_AFTER_S after the text should be done. Turned by hand in between: stays as turned."""
    if not _quiet():
        return
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
            if not isinstance(vol, (int, float)) or vol <= NIGHT_VOLUME + 0.01:
                return
            j._ha("/api/services/media_player/volume_set", {"entity_id": mp, "volume_level": NIGHT_VOLUME})
        except Exception as e:  # noqa: BLE001 — better spoken than not at all
            print(f"  Echo leiser: {str(e)[:120]}", flush=True)
            return
        held = {"volume": vol}
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
    if not held:
        return
    try:
        st = j._ha(f"/api/states/{mp}") or {}
        vol = (st.get("attributes") or {}).get("volume_level")
        if isinstance(vol, (int, float)) and abs(vol - NIGHT_VOLUME) > 0.02:
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
    try:
        j._ha(f"/api/services/notify/{eid.split('.', 1)[1]}", {"message": text, "data": {"type": "tts"}})
    except Exception as e:  # noqa: BLE001 — the HUD already shows the text
        print(f"  Echo: {str(e)[:160]}", flush=True)


def say(eid, text, now=None):
    """Queue the text on the Echo; seconds until it should be spoken (the HUD waits that long)."""
    now = time.time() if now is None else now
    parts = pieces(text)
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


def stop(eid, hard=False):
    """Talked over: sentences not yet sent are dropped; hard also stops the one Alexa is speaking."""
    with _LOCK:
        for t in _TIMERS.pop(eid, []):
            t.cancel()
        _BUSY.pop(eid, None)
        _PENDING.pop(eid, None)
    if hard:
        import jarvis_mcp as j
        try:
            j._ha("/api/services/media_player/media_stop", {"entity_id": player(eid)})
        except Exception as e:  # noqa: BLE001
            print(f"  Echo stoppen: {str(e)[:120]}", flush=True)
