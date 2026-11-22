"""
Push channel from the server to the HUD (Server-Sent Events on /api/events).

Timers, reminders, the wake word and the watchers publish here; the HUD keeps
one EventSource open and speaks what arrives. Nothing here costs tokens.

Event kinds:
  say     something Jarvis says by himself ("Die Waschmaschine ist fertig")
  alarm   a timer, alarm clock or reminder — rings, then speaks
  wake    the wake word was heard (wakeword.py)
  timers  the current list of timers, for the countdown in the top bar

Quiet hours (JARVIS_QUIET_HOURS, e.g. 22-7): `say` events are shown but not
spoken. Alarms always ring — that is what they are for.

Events published while no HUD is connected wait a while and are delivered on
the next connect, so an alarm does not vanish because the tab was reloading.

Every `say` and `alarm` is also appended to a small notice log (JARVIS_NOTICES,
logs/notices.jsonl, the last NOTICES_KEEP), because the event list itself only
lasts half an hour — "Willkommen zurück" sums up what came while the user was out.
"""
import datetime as dt
import itertools
import json
import os
import pathlib
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
NOTICES = pathlib.Path(os.environ.get("JARVIS_NOTICES", ROOT / "logs" / "notices.jsonl"))
NOTICES_KEEP = 300
QUIET_HOURS = os.environ.get("JARVIS_QUIET_HOURS", "22-7").strip()
KEEP_S = 1800                       # how long an undelivered event waits for a HUD
DEDUP_S = int(os.environ.get("JARVIS_DEDUP_SECONDS", "1800"))       # same message again within: dropped
SAY_PER_HOUR = int(os.environ.get("JARVIS_SAY_PER_HOUR", "6"))       # more spoken notices: shown, not spoken
_SPOKEN = []                        # times of spoken (non-silent) notices, for the hourly limit
FRESH_S = {"wake": 3, "timers": 0}  # these are only worth something right away

_COND = threading.Condition()
_LOG = []
_SEQ = itertools.count(1)
CLIENTS = [0]


def quiet_now(now=None):
    """True inside the quiet hours. '22-7' wraps over midnight, '' or 'off' = never."""
    spec = QUIET_HOURS.lower()
    if not spec or spec in ("0", "off", "aus", "none"):
        return False
    try:
        start, end = (int(x) for x in spec.split("-", 1))
    except ValueError:
        return False
    hour = (now or dt.datetime.now()).hour
    if start == end:
        return False
    return start <= hour < end if start < end else hour >= start or hour < end


# How loud Jarvis speaks (Sonos/Echo night volume, whispering) follows the user's "Gute Nacht", not the
# clock: at 0:30 he was still awake and the Sonos at its night volume was far too quiet (17.11.2026).
# JARVIS_SLEEP_ENTITY is a Home Assistant on/off entity that is on from "Gute Nacht" until the morning
# (here input_boolean.jarvis_gute_nacht). Empty, or HA not answering: the quiet hours as before.
SLEEP_ENTITY = os.environ.get("JARVIS_SLEEP_ENTITY", "").strip()
SLEEP_TTL_S = 15
_SLEEP = {"at": 0.0, "state": None}


def _sleep_state():
    """'on' / 'off' of SLEEP_ENTITY, None when unknown."""
    if time.time() - _SLEEP["at"] < SLEEP_TTL_S:
        return _SLEEP["state"]
    state = None
    try:
        import ha_events
        for s in ha_events.states() or []:
            if s.get("entity_id") == SLEEP_ENTITY:
                state = s.get("state")
                break
        if state is None:
            import urllib.request
            url = os.environ.get("HA_URL", "").strip().rstrip("/") + "/api/states/" + SLEEP_ENTITY
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + os.environ.get("HA_TOKEN", "").strip()})
            with urllib.request.urlopen(req, timeout=4) as r:
                state = (json.loads(r.read() or b"{}") or {}).get("state")
    except Exception:  # noqa: BLE001 — unknown: the clock decides
        state = None
    state = state if state in ("on", "off") else None
    _SLEEP.update(at=time.time(), state=state)
    return state


def sleeping(now=None):
    """True when Jarvis should speak softly: after "Gute Nacht" (SLEEP_ENTITY on). Without that entity,
    or when Home Assistant does not know it, the quiet hours decide (quiet_now). An explicit `now`
    (tests, previews) always asks the clock."""
    if SLEEP_ENTITY and now is None:
        state = _sleep_state()
        if state is not None:
            return state == "on"
    return quiet_now(now)


def publish(etype, text="", priority="normal", **extra):
    """etype: say | alarm | wake | timers. Extra fields (kind, items, ...) travel along."""
    if etype == "say" and priority != "alarm":
        try:
            import emergency
            if emergency.paused():
                return None                 # emergency stop: Jarvis stays quiet (alarms still ring)
        except Exception:  # noqa: BLE001
            pass
    ev = dict(extra, t=etype, text=text, priority=priority, at=time.time())
    if etype == "say" and priority != "alarm" and quiet_now():
        ev["silent"] = True
    if etype == "say" and priority not in ("alarm", "critical"):
        try:                                    # a focus session holds notices; a bad night keeps the soft ones quiet
            import focus
            import wellbeing
            if focus.hold(text):
                ev.update(silent=True, held=True)
            elif wellbeing.soft(ev):
                ev["silent"] = True
        except Exception:  # noqa: BLE001
            pass
    if etype == "say" and priority != "alarm":
        same = extra.get("key") or _norm(text)
        with _COND:
            if any(e["t"] == "say" and (e.get("key") or _norm(e.get("text", ""))) == same
                   and ev["at"] - e["at"] < DEDUP_S for e in _LOG):
                return None                 # the same notice from another source or a second time
            del _SPOKEN[:max(0, len([t for t in _SPOKEN if ev["at"] - t > 3600]))]
            if not ev.get("silent") and priority != "critical":
                if len(_SPOKEN) >= SAY_PER_HOUR:
                    ev.update(silent=True, throttled=True)      # too many this hour: shown, not spoken
                else:
                    _SPOKEN.append(ev["at"])
    with _COND:
        ev["id"] = next(_SEQ)
        _LOG.append(ev)
        cutoff = time.time() - KEEP_S
        del _LOG[:max(0, len(_LOG) - 200)]
        while _LOG and _LOG[0]["at"] < cutoff:
            _LOG.pop(0)
        _COND.notify_all()
        if etype in ("say", "alarm") and text:
            _note(ev)
    return ev


def _note(ev):
    try:
        lines = NOTICES.read_text(encoding="utf-8").splitlines() if NOTICES.exists() else []
        lines = (lines + [json.dumps({"at": ev["at"], "kind": ev.get("kind") or ev["t"], "text": ev["text"]},
                                     ensure_ascii=False)])[-NOTICES_KEEP:]
        NOTICES.parent.mkdir(parents=True, exist_ok=True)
        NOTICES.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass                                   # the log is a convenience, never a reason to lose the event


def notices_since(ts):
    """[{at, kind, text}] of what Jarvis announced since `ts` (epoch seconds), oldest first."""
    try:
        rows = [json.loads(ln) for ln in NOTICES.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except (OSError, ValueError):
        return []
    return [r for r in rows if r.get("at", 0) >= ts]


def _norm(text):
    """Same notice regardless of numbers that tick ('seit 5' vs 'seit 10 Minuten') and case."""
    return " ".join("".join(c for c in (text or "").lower() if not c.isdigit()).split())


def _pending(last_id, fresh_connect):
    now = time.time()
    out = []
    for ev in _LOG:
        if ev["id"] <= last_id:
            continue
        if fresh_connect:
            # A new page gets what nobody has heard yet — but no stale wake-ups
            # and no old timer lists (it reads the current one from /api/status).
            if ev.get("delivered") or now - ev["at"] > FRESH_S.get(ev["t"], KEEP_S):
                continue
        out.append(ev)
    return out


def wait(last_id, timeout=15.0):
    """Events after last_id; blocks up to `timeout` seconds when there are none."""
    with _COND:
        evs = _pending(last_id, False)
        if not evs:
            _COND.wait(timeout)
            evs = _pending(last_id, False)
        for ev in evs:
            ev["delivered"] = True
        return [dict(ev) for ev in evs]


def connect():
    """For a new page: (what it should still hear, cursor to continue from)."""
    with _COND:
        evs = _pending(0, True)
        for ev in evs:
            ev["delivered"] = True
        return [dict(ev) for ev in evs], (_LOG[-1]["id"] if _LOG else 0)
