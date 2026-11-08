"""
Which room the user is in — from the presence sensors, through Home Assistant.

Home Assistant keeps a helper up to date from the occupancy sensors (automation "Jarvis: Wo ist Nutzer");
there the bathroom and the bedroom count as one room, and "Unbekannt" means no sensor sees anyone. Its
entity is `presence_room` in config/home.json; its state is a room name from "speakers" (or any name).
Jarvis only reads it, at most every TTL_S seconds; without the entity or Home Assistant: "".

  room()          the room's name, "" when unknown or not configured
  speaker(room)   the output Jarvis answers through in that room: the Echo's notify.* or the Sonos player
  line()          one line for the <kontext> block, so the model knows where the user is
"""
import json
import os
import time
import urllib.request

import jarvis_profile as profile

HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123").strip().rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "").strip()
TTL_S = float(os.environ.get("JARVIS_PRESENCE_TTL_S", "10"))
# After this long without an answer from Home Assistant the last room is no longer trusted.
STALE_S = float(os.environ.get("JARVIS_PRESENCE_STALE_S", "300"))
UNKNOWN = {"", "unbekannt", "unknown", "unavailable", "none", "niemand"}
_CACHE = {"at": 0.0, "room": "", "ok_at": 0.0, "down": False}


def entity():
    return str(profile.ha("presence_room") or "").strip()


def configured():
    return bool(entity() and HA_TOKEN)


def room(now=None):
    """The room the user is in right now, "" if nobody is seen or the feature is off."""
    if not configured():
        return ""
    now = time.time() if now is None else now
    if now - _CACHE["at"] < TTL_S:
        return _CACHE["room"]
    try:
        req = urllib.request.Request(f"{HA_URL}/api/states/{entity()}",
                                     headers={"Authorization": f"Bearer {HA_TOKEN}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            state = str((json.loads(r.read() or b"{}") or {}).get("state") or "")
    except Exception:  # noqa: BLE001 — a hiccup keeps the last room, but only for STALE_S
        last = _CACHE["room"] if now - _CACHE["ok_at"] < STALE_S else ""
        _CACHE.update(at=now, room=last, down=True)
        return last
    # "unavailable": HA answers, but the helper (or its automation) is broken — as good as no sensors
    down = state.strip().lower() == "unavailable"
    _CACHE.update(at=now, room="" if state.strip().lower() in UNKNOWN else state.strip(), down=down,
                  ok_at=_CACHE["ok_at"] if down else now)
    return _CACHE["room"]


def offline():
    """True while the last look failed: Home Assistant unreachable or the presence helper unavailable."""
    return bool(_CACHE.get("down"))


def forget():
    _CACHE.update(at=0.0, room="", ok_at=0.0, down=False)


def speaker(name):
    """The speaker entity for a room from config/home.json → speakers: the Echo (notify.alexa_media_*) if
    one is there, else the room's player (a Sonos). "" when the room has none."""
    name = (name or "").strip().lower()
    if not name:
        return ""
    for key, sp in (profile.ha("speakers", {}) or {}).items():
        if key.lower() == name and isinstance(sp, dict):
            return sp.get("notify") or sp.get("player") or ""
    return ""


def line():
    r = room()
    if not r:
        return ""
    who = profile.USER_NAME or "Der User"
    article = "in der" if r.lower().endswith("e") else "im"
    return (f"Aufenthaltsort: {who} ist gerade {article} {r} (Präsenzmelder; Bad und Schlafzimmer zählen "
            f"als ein Raum). Durchsagen ohne Raumangabe gehören dorthin.")
