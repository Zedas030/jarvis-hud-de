"""
Room-aware commands: which room a command without a room name belongs to, and how sure Jarvis is of it.

The room itself still comes from Home Assistant (whereabouts.py reads the helper the automation "Jarvis: Wo ist
Nutzer" keeps; that automation owns the hold times against radar ghosts). This module adds what a command needs:

  devices of a room   HA areas (Settings → Areas); config/home.json → room_scope.areas maps an area onto a room
                      when HA's areas are finer than the presence rooms ({"Bad": "Schlafzimmer"})
  how sure            the presence sensors right now (room_scope.sensors): the helper's room counts as sure unless
                      its sensors are free while another room's sensor is busy — then the user is probably walking
                      and a command that would hit different devices in the two rooms is asked back
  offline             Home Assistant not reachable: commands behave as before, answers come from the device

  enabled()           feature flag JARVIS_ROOM_SCOPE (default on) and a presence helper configured
  current()           {"room", "sure", "candidates", "status"} — status "ok", "offline" or "off"
  eids(room)          entity ids in that room ("" -> empty set)
  room_of(eid)        the room an entity is in, "" if HA has no area for it
  line()              the <kontext> line: room, how sure, the devices there

Switched off (JARVIS_ROOM_SCOPE=0) every function answers as if nothing were known, and router.py behaves
exactly as before this module existed.
"""
import json
import os
import re
import time
import urllib.request

import jarvis_profile as profile
import whereabouts

ENABLED = os.environ.get("JARVIS_ROOM_SCOPE", "1").strip().lower() not in ("0", "false", "nein", "off", "")
AREA_TTL_S = float(os.environ.get("JARVIS_ROOM_AREA_TTL_S", "600"))
SENSOR_TTL_S = float(os.environ.get("JARVIS_ROOM_SENSOR_TTL_S", "5"))
CONTEXT_DEVICES = 8
# Kinds of things a person switches in a room; config switches and sockets that keep things alive are left out.
_DOMAINS = ("light", "switch", "fan", "climate", "media_player")
_NOT_A_DEVICE = ("_internet_access", "_led", "_kindersicherung", "_automatische_abschaltung", "_automatisches_update",
                 "_zone_", "_uberblenden", "_loudness", "_tv_autoplay", "_gruppierung", "_display", "sonos_alarm")
_KEEPS_ALIVE = re.compile(r"router|\bpc\b|raspi|\bpi\b|kühlschrank|kuhlschrank|smart.?hub|\bnas\b|fritz", re.I)
_AREAS = {"at": 0.0, "map": {}, "names": {}}
_SENSORS = {"at": 0.0, "busy": None}


def enabled():
    return ENABLED and whereabouts.configured()


def _cfg():
    c = profile.ha("room_scope", {}) or {}
    return c if isinstance(c, dict) else {}


def _post_template(template):
    req = urllib.request.Request(f"{whereabouts.HA_URL}/api/template", method="POST",
                                 data=json.dumps({"template": template}).encode(),
                                 headers={"Authorization": f"Bearer {whereabouts.HA_TOKEN}",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.read().decode("utf-8", "replace")


# One line per entity: "entity_id|area name|friendly name". Areas change rarely, so this is asked every AREA_TTL_S.
_AREA_TEMPLATE = ("{% for a in areas() %}{% for e in area_entities(a) %}"
                  "{{ e }}|{{ area_name(a) }}|{{ state_attr(e, 'friendly_name') or e }}\n"
                  "{% endfor %}{% endfor %}")


def _areas(now=None):
    now = time.time() if now is None else now
    if now - _AREAS["at"] < AREA_TTL_S:
        return _AREAS
    try:
        text = _post_template(_AREA_TEMPLATE)
        m, names = {}, {}
        for row in text.splitlines():
            parts = row.split("|", 2)
            if len(parts) == 3 and "." in parts[0]:
                m[parts[0].strip()] = parts[1].strip()
                names[parts[0].strip()] = parts[2].strip()
        _AREAS.update(at=now, map=m, names=names)
    except Exception:  # noqa: BLE001 — keep what was known; try again in a minute, not on every command
        _AREAS["at"] = now - AREA_TTL_S + 60
    return _AREAS


def _room_for_area(area):
    if not area:
        return ""
    mapped = (_cfg().get("areas") or {}).get(area)
    return str(mapped or area)


def room_of(eid):
    if not enabled():
        return ""
    return _room_for_area(_areas()["map"].get(eid, ""))


def eids(room):
    if not (enabled() and room):
        return set()
    r = room.strip().lower()
    return {e for e, a in _areas()["map"].items() if _room_for_area(a).lower() == r}


def rooms():
    """Rooms a command can mean: those with presence sensors, else those with a speaker."""
    names = list((_cfg().get("sensors") or {}).keys()) or list((profile.ha("speakers", {}) or {}).keys())
    return [str(n) for n in names]


def _busy(now=None):
    """Rooms whose presence sensor sees someone right now; None when unknown (no sensors, HA down)."""
    sensors = _cfg().get("sensors") or {}
    if not sensors:
        return None
    now = time.time() if now is None else now
    if now - _SENSORS["at"] < SENSOR_TTL_S:
        return _SENSORS["busy"]
    ids = [e for es in sensors.values() for e in (es if isinstance(es, list) else [es])]
    template = "{% for e in " + json.dumps(ids) + " %}{{ e }}={{ states(e) }}\n{% endfor %}"
    try:
        states = dict(row.split("=", 1) for row in _post_template(template).splitlines() if "=" in row)
        busy = {str(room) for room, es in sensors.items()
                if any(states.get(e) == "on" for e in (es if isinstance(es, list) else [es]))}
    except Exception:  # noqa: BLE001
        busy = None
    _SENSORS.update(at=now, busy=busy)
    return busy


def current(now=None):
    if not enabled():
        return {"room": "", "sure": False, "candidates": [], "status": "off"}
    room = whereabouts.room(now)
    if whereabouts.offline():
        return {"room": "", "sure": False, "candidates": [], "status": "offline"}
    busy = _busy(now)
    if not room:
        cands = sorted(busy) if busy else rooms()
        return {"room": "", "sure": False, "candidates": cands, "status": "ok"}
    lower = {b.lower() for b in busy or ()}
    sure = not busy or room.lower() in lower      # its own sensor busy, or nobody seen at all (asleep, still)
    cands = [room] + sorted(b for b in busy or () if b.lower() != room.lower())
    return {"room": room, "sure": sure, "candidates": [room] if sure else cands, "status": "ok"}


def forget():
    _AREAS.update(at=0.0, map={}, names={})
    _SENSORS.update(at=0.0, busy=None)


def _device_list(room):
    names = _areas()["names"]
    out = []
    for e in sorted(eids(room)):
        dom = e.split(".", 1)[0]
        if dom not in _DOMAINS or any(bad in e for bad in _NOT_A_DEVICE):
            continue
        name = names.get(e) or e
        if dom == "switch" and _KEEPS_ALIVE.search(name + " " + e):
            continue                                   # keeps things alive: never meant by "aus"
        out.append(f"{name} ({e})")
    return out[:CONTEXT_DEVICES]


def line():
    """One line for the model: where the user is, how sure, what is there. "" when nothing is known."""
    try:
        cur = current()
    except Exception:  # noqa: BLE001
        return whereabouts.line()
    if cur["status"] == "off":
        return whereabouts.line()
    if cur["status"] == "offline":
        return "Aufenthaltsort: unbekannt (Home Assistant gerade nicht erreichbar). Bei Befehlen ohne Raum kurz nachfragen."
    who = profile.USER_NAME or "Der User"
    if not cur["room"]:
        return ""
    r = cur["room"]
    article = "in der" if r.lower().endswith("e") else "im"
    if not cur["sure"]:
        return (f"Aufenthaltsort: unsicher – zuletzt {r}, gerade ist {' oder '.join(cur['candidates'][1:])} belegt "
                f"(Präsenzmelder). Bei Befehlen ohne Raum kurz fragen: „{' oder '.join(cur['candidates'])}?“")
    devs = _device_list(r)
    there = f" Geräte dort: {', '.join(devs)}." if devs else ""
    return (f"Aufenthaltsort: {who} ist gerade {article} {r} (Präsenzmelder; Bad und Schlafzimmer zählen als ein "
            f"Raum).{there} Befehle ohne Raumangabe („Licht an“, „wärmer“, „Sag durch“) gelten für diesen Raum.")
