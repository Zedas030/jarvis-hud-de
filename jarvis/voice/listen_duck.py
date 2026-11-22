"""
While a voice satellite listens, the room's media goes quiet (Nutzer, 19.11.2026).

With YouTube on the PC or the TV running, the Voice PE heard the programme together with "Okay Jarvis …":
its voice detection never found the pause after his sentence, so it listened until Home Assistant's
15 s limit and Whisper wrote down the TV. The satellite cannot tell his voice from the TV's, so the
TV has to step aside for a moment, like Alexa does with her own music:

  - every sound session of this PC except Jarvis' own processes is turned down to DUCK_PC of its volume
  - the soundbar (Samsung multiroom API on :55001, TV sound) is muted, if it was not muted already

When the satellite stops listening, the room stays quiet until Jarvis has answered (like Alexa with her
own music), then everything goes back to exactly where it was; at the latest after MAX_S. Only satellites in one of the rooms WHERE duck
(their Home Assistant area, or the word in their entity id) — the PC and the TV are in the living room.

  JARVIS_LISTEN_DUCK          1 (default) / 0 = off
  JARVIS_LISTEN_DUCK_PC       0.1 = factor for the PC's sound sessions, 0 = leave the PC alone
  JARVIS_LISTEN_DUCK_WHERE    wohnzimmer (comma list: the satellite's area in Home Assistant, or part of its entity id)
  JARVIS_LISTEN_DUCK_MAX_S    60
  JARVIS_SOUNDBAR_HOST        e.g. 10.0.0.184; empty = no soundbar
  JARVIS_LISTEN_DUCK_STATE    logs/listen_duck.json — what is turned down right now, so a Jarvis that
                              restarts in between (code change, crash) puts it back on the next start
"""
import os
import queue
import threading
import time
import urllib.parse
import urllib.request

import json
import pathlib

import jarvis_log

log = jarvis_log.get(__name__)

ENABLED = os.environ.get("JARVIS_LISTEN_DUCK", "1").strip().lower() not in ("0", "false", "no", "off", "aus")
DUCK_PC = max(0.0, min(1.0, float(os.environ.get("JARVIS_LISTEN_DUCK_PC", "0.1") or 0)))
WHERE = [w.strip().lower() for w in os.environ.get("JARVIS_LISTEN_DUCK_WHERE", "wohnzimmer").split(",") if w.strip()]
MAX_S = float(os.environ.get("JARVIS_LISTEN_DUCK_MAX_S", "60"))
GRACE_S = 1.5        # after the satellite stops listening, before asking whether Jarvis answers
QUIET_S = 0.8        # Jarvis silent this long -> the answer is over
POLL_S = 0.25
SOUNDBAR = os.environ.get("JARVIS_SOUNDBAR_HOST", "").strip()
ROOT = pathlib.Path(__file__).resolve().parents[2]
STATE_FILE = pathlib.Path(os.environ.get("JARVIS_LISTEN_DUCK_STATE", ROOT / "logs" / "listen_duck.json"))
# Jarvis' own voice and system sounds are not the room's media
IGNORE = {p.strip().lower() for p in os.environ.get("JARVIS_PC_AUDIO_IGNORE", "python.exe,pythonw.exe").split(",")
          if p.strip()}

_LOCK = threading.Lock()
_STATE = {"who": set(), "pc": {}, "bar_muted": False, "timer": None, "cycle": 0}


_AREA = {}               # satellite -> (asked at, its Home Assistant area in lower case)
AREA_TTL_S = 600


def _area(satellite):
    """The satellite's area in Home Assistant, '' when unknown. Never raises."""
    at, area = _AREA.get(satellite, (0.0, ""))
    if time.time() - at < AREA_TTL_S:
        return area
    try:
        import jarvis_mcp as j
        req = urllib.request.Request(
            j.HA_URL + "/api/template", method="POST",
            data=json.dumps({"template": "{{ area_name('%s') or '' }}" % satellite.replace("'", "")}).encode(),
            headers={"Authorization": f"Bearer {j.HA_TOKEN}", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            area = r.read(200).decode("utf-8", "replace").strip().lower()
    except Exception as e:  # noqa: BLE001 — HA not answering: what was known last stays
        log.debug("area: %s", str(e)[:120])
    _AREA[satellite] = (time.time(), area)
    return area


def applies(satellite):
    # 20.11.2026: the re-flashed Voice PE came back as assist_satellite.home_assistant_voice_0ae557_… — no
    # room in its id any more, and nothing was turned down all evening. Its area in HA says where it stands.
    if not ENABLED or not satellite:
        return False
    sat = satellite.lower()
    if any(w in sat for w in WHERE):
        return True
    area = _area(satellite)
    return bool(area) and any(w in area for w in WHERE)


# ---- the PC: per-app volume (pycaw), so his master volume is never touched

def _pc_sessions():
    """[(key, name, get(), set(v))] for every sound session of this PC."""
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:  # noqa: BLE001 — already initialised in this thread
        pass
    from pycaw.pycaw import AudioUtilities
    out = []
    for s in AudioUtilities.GetAllSessions():
        name = (s.Process.name() if s.Process else "").lower()
        vol = s.SimpleAudioVolume
        out.append((s.ProcessId, name, vol.GetMasterVolume, lambda v, vol=vol: vol.SetMasterVolume(v, None)))
    return out


def _pc_down():
    if not DUCK_PC or DUCK_PC >= 1:
        return 0
    n = 0
    for key, name, get, put in _pc_sessions():
        if not name or name in IGNORE or key in _STATE["pc"]:
            continue
        was = get()
        _STATE["pc"][key] = was
        put(was * DUCK_PC)
        n += 1
    return n


def _pc_up():
    back = _STATE["pc"]
    _STATE["pc"] = {}
    if not back:
        return
    for key, _name, _get, put in _pc_sessions():
        if key in back:
            put(back[key])


# ---- the soundbar: Samsung multiroom API (HW-J8500), the TV plays through it

def _bar(cmd, *params):
    """One UIC command -> response text ('' when the soundbar is off or answers nothing)."""
    xml = f"<name>{cmd}</name>" + "".join(f'<p type="str" name="{k}" val="{v}"/>' for k, v in params)
    url = f"http://{SOUNDBAR}:55001/UIC?cmd=" + urllib.parse.quote(xml)
    with urllib.request.urlopen(url, timeout=1.5) as r:
        return r.read(4000).decode("utf-8", "replace")


def _bar_down():
    if not SOUNDBAR:
        return False
    if "<mute>on</mute>" in _bar("GetMute"):
        return False                     # muted by him: leave it, and leave it muted afterwards
    _bar("SetMute", ("mute", "on"))
    _STATE["bar_muted"] = True
    return True


def _bar_up():
    if _STATE["bar_muted"]:
        _STATE["bar_muted"] = False
        _bar("SetMute", ("mute", "off"))


def _save():
    # 19.11.2026 09:41: Jarvis restarted while the PC was turned down, and Opera stayed at 10 %
    try:
        if _STATE["pc"] or _STATE["bar_muted"]:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps({"pc": {str(k): v for k, v in _STATE["pc"].items()},
                                              "bar_muted": _STATE["bar_muted"], "bar": SOUNDBAR}), encoding="utf-8")
        else:
            STATE_FILE.unlink(missing_ok=True)
    except OSError as e:
        log.warning("state: %s", str(e)[:120])


def recover():
    """At start: whatever an earlier Jarvis left turned down goes back. Never raises."""
    try:
        left = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    with _LOCK:
        if _STATE["who"]:
            return False
        _STATE["pc"] = {int(k): float(v) for k, v in (left.get("pc") or {}).items()}
        _STATE["bar_muted"] = bool(left.get("bar_muted")) and left.get("bar") == SOUNDBAR
        try:
            _pc_up()
        except Exception as e:  # noqa: BLE001
            log.warning("pc zurück: %s", str(e)[:120])
        try:
            _bar_up()
        except Exception as e:  # noqa: BLE001
            log.warning("soundbar zurück: %s", str(e)[:120])
        _STATE["pc"], _STATE["bar_muted"] = {}, False
        _save()
    log.info("Lautstärke nach Neustart zurückgestellt")
    return True


# ---- start / end

def _jarvis_talking():
    """A satellite question is still being answered, or Jarvis' voice still plays on a room speaker."""
    import remote_ask
    import satellite_followup
    if satellite_followup.pending():     # Jarvis asked back: the mic opens again in a moment
        return True
    with remote_ask._ACTIVE_LOCK:
        if remote_ask._ACTIVE["asks"] or remote_ask._ACTIVE["voices"]:
            return True
    import alexa_voice
    import sonos
    for out in sonos.outputs():
        eid = out["id"]
        if (alexa_voice.speaking(eid) if alexa_voice.is_alexa(eid) else sonos.speaking(eid)):
            return True
    return False


def start(satellite):
    """The satellite began listening. Never raises."""
    if not applies(satellite):
        return []
    done = []
    with _LOCK:
        _STATE["cycle"] += 1
        _STATE["who"].add(satellite)
        try:
            n = _pc_down()               # sessions already turned down (still waiting for the answer) stay as they are
            if n:
                done.append(f"PC x{n}")
        except Exception as e:  # noqa: BLE001 — no pycaw / not Windows: nothing to turn down
            log.debug("pc: %s", str(e)[:120])
        try:
            if not _STATE["bar_muted"] and _bar_down():
                done.append("Soundbar stumm")
        except Exception as e:  # noqa: BLE001 — soundbar off or not on the network
            log.debug("soundbar: %s", str(e)[:120])
        _save()
        if _STATE["timer"]:
            _STATE["timer"].cancel()
        t = threading.Timer(MAX_S, lambda: _restore("Sicherheitsgrenze"))
        t.daemon = True
        _STATE["timer"] = t
        t.start()
    if done:
        log.info("%s hört zu: leiser %s", satellite, ", ".join(done))
    return done


def _restore(why=""):
    with _LOCK:
        _STATE["who"].clear()
        _STATE["cycle"] += 1             # a release still waiting for the answer gives up
        t, _STATE["timer"] = _STATE["timer"], None
        if t and t is not threading.current_thread():
            t.cancel()
        try:
            _pc_up()
        except Exception as e:  # noqa: BLE001
            log.warning("pc zurück: %s", str(e)[:120])
        try:
            _bar_up()
        except Exception as e:  # noqa: BLE001
            log.warning("soundbar zurück: %s", str(e)[:120])
        _STATE["pc"], _STATE["bar_muted"] = {}, False
        _save()
    if why:
        log.info("Lautstärke zurück (%s)", why)
    return True


def _release(cycle):
    # Alexa keeps her music down until she has answered: the room stays quiet while Jarvis thinks and speaks
    # (19.11.2026, Nutzer' test: YouTube came back the moment he stopped talking and drowned the Echo)
    time.sleep(GRACE_S)                  # the question reaches /api/ask a moment after the satellite stops
    quiet_since = None
    while True:
        with _LOCK:
            if _STATE["cycle"] != cycle:
                return False             # listening again, or already restored
        try:
            talking = _jarvis_talking()
        except Exception as e:  # noqa: BLE001 — no speaker info: don't hold the room hostage
            log.debug("talking: %s", str(e)[:120])
            talking = False
        now = time.time()
        if talking:
            quiet_since = None
        elif quiet_since is None:
            quiet_since = now
        elif now - quiet_since >= QUIET_S:
            with _LOCK:
                if _STATE["cycle"] != cycle:
                    return False
            return _restore()
        time.sleep(POLL_S)


def end(satellite):
    """The satellite stopped listening: everything back once Jarvis has answered. Never raises."""
    with _LOCK:
        if satellite not in _STATE["who"]:
            return False
        _STATE["who"].discard(satellite)
        if _STATE["who"]:
            return False
        cycle = _STATE["cycle"]
    threading.Thread(target=_release, args=(cycle,), daemon=True, name="listen-duck-release").start()
    return True


def on_change(data):
    """ha_events hands over every assist_satellite.* state change (via satellite_quiet)."""
    eid = str((data or {}).get("entity_id") or "")
    if not eid.startswith("assist_satellite."):
        return
    new = ((data.get("new_state") or {}).get("state")) or ""
    old = ((data.get("old_state") or {}).get("state")) or ""
    if new == "listening" and old != "listening":
        _queue(start, eid)
    elif old == "listening" and new != "listening":
        _queue(end, eid)


_JOBS = queue.Queue()


def _worker():
    while True:
        fn, eid = _JOBS.get()
        try:
            fn(eid)
        except Exception as e:  # noqa: BLE001 — start/end never raise, but the worker must survive anyway
            log.warning("listen_duck: %s", str(e)[:120])
        finally:
            _JOBS.task_done()


def _queue(fn, eid):
    # one worker in arrival order: a short "listening" must never be ended before it was started
    if not _STATE.get("worker"):
        _STATE["worker"] = threading.Thread(target=_worker, daemon=True, name="listen-duck")
        _STATE["worker"].start()
    _JOBS.put((fn, eid))


def reset():
    """For the tests."""
    with _LOCK:
        if _STATE["timer"]:
            _STATE["timer"].cancel()
        _STATE.update(who=set(), pc={}, bar_muted=False, timer=None, cycle=_STATE["cycle"] + 1)
