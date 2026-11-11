"""
Home Assistant in real time: one WebSocket subscription instead of fetching /api/states everywhere.

Until 10.11.2026 every watcher round (30 s) and about twenty other places fetched the full state list
over REST — 1–2 MB per call, and a door, a water sensor or a finished print was noticed up to 30 s late.
Now one thread holds a WebSocket to HA: `get_states` once, then `state_changed` events keep a local copy
current. `states()` hands that copy out (briefing._ha_states uses it first), and a change that matters
sets WAKE so watchers.py runs its state checks at once instead of at the next round.

  states()          list of state dicts like /api/states, or None while the connection is down
                    (callers then fall back to REST — nothing gets worse when HA restarts)
  WAKE              threading.Event, set on a state change worth an immediate look
  status()          for /api/status and diagnosis

  JARVIS_HA_WS      1 (default) / 0 = off, everything polls as before
  JARVIS_HA_WS_WAKE regex of entity ids that wake the watchers; the default covers doors, windows,
                    water, smoke, motion, presence, persons, the printer and the Jarvis switches
"""
import json
import os
import re
import threading
import time

import jarvis_log

log = jarvis_log.get(__name__)

HA_URL = os.environ.get("HA_URL", "").strip().rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "").strip()
ENABLED = os.environ.get("JARVIS_HA_WS", "1").strip().lower() not in ("0", "false", "no", "off")
WAKE_RE = re.compile(os.environ.get(
    "JARVIS_HA_WS_WAKE",
    r"^(binary_sensor|person|device_tracker|input_boolean|lock|cover|alarm_control_panel|event)\."
    r"|^sensor\..*_(druckstatus|print_status)$"), re.I)
STALE_S = 120             # no message at all for this long (HA pings every 30 s) = treat as down
# A sensor that flips more often than this per hour (the presence sensors with their ghost pulses) stops
# waking the watchers for an hour; its state still lands in the copy and the 30-s round still sees it.
NOISY_PER_H = int(os.environ.get("JARVIS_HA_WS_NOISY", "30") or 0)
NOISY_PAUSE_S = 3600

WAKE = threading.Event()
_LOCK = threading.Lock()
_CACHE = {}
_FLIPS = {}               # entity_id -> deque of change times (last hour)
_MUTED = {}               # entity_id -> muted until
_ST = {"live": False, "since": 0.0, "last_msg": 0.0, "events": 0, "wakes": 0, "error": "",
       "reconnects": 0, "thread": None}


def states():
    """A copy of all states, or None when the live view cannot be trusted."""
    with _LOCK:
        if not _ST["live"] or time.time() - _ST["last_msg"] > STALE_S or not _CACHE:
            return None
        return list(_CACHE.values())


def status():
    with _LOCK:
        return {k: v for k, v in _ST.items() if k != "thread"} | {"entities": len(_CACHE), "noisy": noisy()}


def worth_waking(entity_id, old, new, now=None):
    """A real state change (not just attributes) of an entity the watchers react to — unless it is noisy."""
    if not WAKE_RE.search(entity_id or ""):
        return False
    before = (old or {}).get("state")
    after = (new or {}).get("state")
    if before == after:
        return False
    return not _noisy(entity_id, now or time.time())


def _noisy(entity_id, now):
    if not NOISY_PER_H:
        return False
    if _MUTED.get(entity_id, 0) > now:
        return True
    from collections import deque
    flips = _FLIPS.setdefault(entity_id, deque())
    flips.append(now)
    while flips and now - flips[0] > 3600:
        flips.popleft()
    if len(flips) > NOISY_PER_H:
        _MUTED[entity_id] = now + NOISY_PAUSE_S
        flips.clear()
        log.info("%s schaltet %d-mal pro Stunde — weckt die Wächter eine Stunde lang nicht", entity_id, NOISY_PER_H)
        return True
    return False


def noisy():
    """Entities muted right now, for status and diagnosis."""
    now = time.time()
    return sorted(e for e, until in _MUTED.items() if until > now)


def apply_event(data):
    """Applies one state_changed event to the cache. Returns True when the watchers should look now."""
    eid = data.get("entity_id") or ""
    new = data.get("new_state")
    old = data.get("old_state")
    with _LOCK:
        if new is None:
            _CACHE.pop(eid, None)
        else:
            _CACHE[eid] = new
        _ST["events"] += 1
    return worth_waking(eid, old, new)


def _ws_url():
    return HA_URL.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"


def _session():
    from websockets.sync.client import connect
    with connect(_ws_url(), open_timeout=10, close_timeout=2, max_size=64 * 1024 * 1024) as ws:
        json.loads(ws.recv(timeout=10))
        ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
        if json.loads(ws.recv(timeout=10)).get("type") != "auth_ok":
            raise PermissionError("Anmeldung an Home Assistant abgelehnt")
        # subscribe first, then read the full list: nothing that changes in between is lost
        ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "state_changed"}))
        ws.send(json.dumps({"id": 2, "type": "get_states"}))
        # alarms HA forwards for Jarvis (Uptime Kuma's webhook, kuma_alerts.py)
        ws.send(json.dumps({"id": 3, "type": "subscribe_events", "event_type": "jarvis_alert"}))
        # alarms HA forwards for Jarvis (Uptime Kuma's webhook, kuma_alerts.py)
        ws.send(json.dumps({"id": 3, "type": "subscribe_events", "event_type": "jarvis_alert"}))
        while True:
            try:
                msg = json.loads(ws.recv(timeout=STALE_S))
            except TimeoutError:
                raise ConnectionError(f"seit {STALE_S} s keine Nachricht von Home Assistant")
            now = time.time()
            with _LOCK:
                _ST["last_msg"] = now
            if msg.get("type") == "result" and msg.get("id") == 2:
                if not msg.get("success"):
                    raise ConnectionError("get_states abgelehnt")
                with _LOCK:
                    _CACHE.clear()
                    _CACHE.update({s["entity_id"]: s for s in msg.get("result") or []})
                    _ST.update(live=True, since=now, error="")
                log.info("Home Assistant live verbunden (%d Entitäten)", len(_CACHE))
                WAKE.set()
            elif msg.get("type") == "event" and (msg.get("event") or {}).get("event_type") == "jarvis_alert":
                import kuma_alerts
                kuma_alerts.on_event((msg.get("event") or {}).get("data") or {})
            elif msg.get("type") == "event":
                if apply_event((msg.get("event") or {}).get("data") or {}):
                    _ST["wakes"] += 1
                    WAKE.set()


def _run():
    pause = 2
    while True:
        try:
            _session()
        except Exception as e:  # noqa: BLE001 — HA restarting, network gone, token wrong
            with _LOCK:
                was_live = _ST["live"]
                _ST.update(live=False, error=str(e)[:160])
                _ST["reconnects"] += 1
            if was_live:
                log.warning("Home Assistant live getrennt (%s) — bis zur Rückkehr per Abfrage", str(e)[:120])
            pause = 2 if was_live else min(pause * 2, 120)
        time.sleep(pause)


def start():
    if not (ENABLED and HA_URL and HA_TOKEN) or _ST["thread"] is not None:
        return False
    try:
        import websockets.sync.client  # noqa: F401
    except ImportError:
        _ST["error"] = "websockets fehlt"
        return False
    t = threading.Thread(target=_run, daemon=True, name="ha-events")
    _ST["thread"] = t
    t.start()
    return True
