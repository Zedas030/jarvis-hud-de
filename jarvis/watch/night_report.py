"""
Morning report on the night's ghosts — only when there is something. Local, 0 tokens.

    "Heute Nacht hat Aufenthaltserkennung Wohnzimmer 46-mal angeschlagen, während Sie im Schlafzimmer waren.
     LED ging dabei 3-mal an (Jarvis: Licht Wohnzimmer folgt mir)."

The presence sensors in the flat have had ghost pulses for weeks (PC fan, air purifier, the TV). From the HA
logbook of the night window this counts, per presence/motion sensor, the pulses while the user was in another
room (JARVIS_ROOM_ENTITY, the input_select the room automation keeps), and the lights that went on at night with their
cause. Said once in the morning when a sensor has at least JARVIS_NIGHT_GHOSTS pulses or a light went on;
"Wie war die Nacht?" asks for it any time.

  JARVIS_NIGHT_REPORT        1 / 0 = off
  JARVIS_NIGHT_WINDOW        hours, "0-6"
  JARVIS_NIGHT_REPORT_TIME   not before (07:30)
  JARVIS_NIGHT_GHOSTS        pulses that count as worth saying (5)
  JARVIS_ROOM_ENTITY         where the user is (an input_select with the room name)
"""
import datetime as dt
import os
import re

import events

ENABLED = os.environ.get("JARVIS_NIGHT_REPORT", "1").strip().lower() not in ("0", "false", "no", "off")
WINDOW = os.environ.get("JARVIS_NIGHT_WINDOW", "0-6").strip()
AT = os.environ.get("JARVIS_NIGHT_REPORT_TIME", "07:30").strip()
GHOSTS = int(os.environ.get("JARVIS_NIGHT_GHOSTS", "5") or 5)
ROOM_ENTITY = os.environ.get("JARVIS_ROOM_ENTITY", "input_select.room_tracker").strip()
ROOMS = ("wohnzimmer", "schlafzimmer", "bad", "küche", "flur", "büro", "kinderzimmer", "gästezimmer")


def _window(day):
    a, _, b = WINDOW.partition("-")
    start = dt.datetime.combine(day, dt.time(int(a or 0))).astimezone()
    end = dt.datetime.combine(day, dt.time(min(23, int(b or 6)))).astimezone()
    return start, end


def _room_word(text):
    t = (text or "").lower()
    return next((r for r in ROOMS if r in t), None)


def _at(e):
    try:
        return dt.datetime.fromisoformat(e["when"])
    except (KeyError, TypeError, ValueError):
        return None


def _timeline(rows):
    return sorted((t, (r.get("state") or "").lower()) for r in rows if "state" in r for t in [_at(r)] if t)


def _room_at(timeline, before, t):
    room = before
    for when, state in timeline:
        if when > t:
            break
        room = state
    return room


def analyse(day=None, states=None):
    """{"ghosts": [(name, count, room_word)], "lights": [(name, count, cause)]} for the night of `day`."""
    import briefing
    import ha_why
    day = day or dt.date.today()
    start, end = _window(day)
    states = states if states is not None else {s["entity_id"]: s for s in briefing._ha_states()}
    sensors = [eid for eid, s in states.items() if eid.startswith("binary_sensor.")
               and (s.get("attributes") or {}).get("device_class") in ("occupancy", "motion", "presence")
               and not re.search(r"zone", (s.get("attributes") or {}).get("friendly_name") or "", re.I)]
    lights = [eid for eid in states if eid.startswith("light.") and not re.search(r"druckraum", eid)]
    room_rows = ha_why.logbook(ROOM_ENTITY, start - dt.timedelta(hours=12), end) if ROOM_ENTITY in states else []
    timeline = _timeline(room_rows)
    before = next((s for t, s in reversed(timeline) if t <= start), "")
    timeline = [(t, s) for t, s in timeline if t > start]
    ghosts, lit = [], []
    for eid in sensors:
        name = (states[eid].get("attributes") or {}).get("friendly_name") or eid
        room = _room_word(name) or _room_word(eid)
        pulses = [t for t, s in _timeline(ha_why.logbook(eid, start, end)) if s == "on"]
        stray = [t for t in pulses if room and _room_word(_room_at(timeline, before, t)) != room]
        if stray:
            ghosts.append((name, len(stray), room))
    for eid in lights:
        every = ha_why.logbook(eid, start, end)
        rows = [r for r in every if r.get("state") == "on"]
        if rows:
            name = (states[eid].get("attributes") or {}).get("friendly_name") or eid
            # the state row often lacks the context, the automation's own logbook line next to it has it
            why = next((r.get("context_name") for r in reversed(every) if r.get("context_name")), None)
            lit.append((name, len(rows), why or "ohne erkennbaren Auslöser"))
    ghosts.sort(key=lambda g: -g[1])
    return {"ghosts": ghosts, "lights": lit}


def spoken(result, force=False):
    ghosts = [g for g in result["ghosts"] if force or g[1] >= GHOSTS]
    lights = result["lights"]
    if not ghosts and not lights:
        return "Die Nacht war ruhig, Sir: keine Geisterimpulse, kein Licht ging an." if force else ""
    parts = []
    if ghosts:
        name, count, _ = ghosts[0]
        parts.append(f"Heute Nacht hat {name} {count}-mal angeschlagen, während Sie in einem anderen Raum waren.")
        if len(ghosts) > 1:
            parts.append("Außerdem: " + ", ".join(f"{n} {c}-mal" for n, c, _ in ghosts[1:3]) + ".")
    for name, count, why in lights[:2]:
        parts.append(f"{name} ging dabei {count}-mal an ({why}).")
    return " ".join(parts)


def step(now=None):
    import background
    now = now or dt.datetime.now()
    if not ENABLED or now.strftime("%H:%M") < AT or now.hour >= 13:
        return None
    if not background.claim("night-report", now.date().isoformat()):
        return None
    text = spoken(analyse(now.date()))
    if text:
        events.publish("say", text, kind="homelab")
    return text or None


def handle(n):
    if not re.search(r"\b(wie war die nacht|nachtbericht|geister ?bericht|haben die (melder|sensoren) heute nacht)\b", n):
        return None
    try:
        return spoken(analyse(), force=True)
    except OSError:
        return "Ich komme gerade nicht an das Logbuch von Home Assistant, Sir."


def start():
    import background
    if ENABLED:
        background.loop("night_report", 600, step)
