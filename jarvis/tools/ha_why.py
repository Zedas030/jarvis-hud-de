"""
"Warum ist das Licht angegangen?" — the cause from the Home Assistant logbook, in one sentence. Local, 0 tokens.

    Licht Wohnzimmer ging um 23:12 an. Auslöser: die Automation „Licht folgt mir“, ausgelöst von „Raum“
    (auf Wohnzimmer), das gesetzt hat: die Automation „Wo bin ich“, ausgelöst von „Präsenz Wohnzimmer“ (an).

The logbook stores with every change the context it happened in: which automation, script or person, and
for an automation what triggered it ("state of input_select.room"). This follows that chain up to
two steps back, so the ghost pulses of a presence sensor show up as the real cause. No context = changed
on the device itself or in an app that does not tell HA (Tuya, Hue app).

  explain(entity_id, now)   the sentence, or None when the logbook has nothing for the last 24 hours
  handle(n)                 router: "warum ist/ging … an/aus", "wer hat … eingeschaltet", "wieso läuft …"
"""
import datetime as dt
import json
import os
import re
import urllib.parse
import urllib.request

HOURS = int(os.environ.get("JARVIS_WHY_HOURS", "24"))
_ASK = re.compile(r"\b(warum|wieso|weshalb|wer hat)\b.*\b(an|aus|angegangen|ausgegangen|eingeschaltet|ausgeschaltet|"
                  r"angemacht|ausgemacht|läuft|brennt|geschaltet|gestartet|hochgefahren)\b")


def _get(path):
    import briefing
    req = urllib.request.Request(briefing.HA_URL + path, headers={"Authorization": f"Bearer {briefing.HA_TOKEN}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"[]")


def logbook(entity_id, start, end=None):
    q = "?entity=" + urllib.parse.quote(entity_id)
    if end:
        q += "&end_time=" + urllib.parse.quote(end.isoformat())
    return _get("/api/logbook/" + urllib.parse.quote(start.isoformat()) + q)


def _when(iso):
    try:
        t = dt.datetime.fromisoformat(iso).astimezone()
    except (TypeError, ValueError):
        return "?"
    today = dt.datetime.now().astimezone().date()
    day = "" if t.date() == today else ("gestern " if t.date() == today - dt.timedelta(days=1) else t.strftime("am %d.%m. "))
    return f"{day}um {t.hour}:{t.minute:02d}"


def _state_word(state):
    return {"on": "an", "off": "aus", "playing": "an", "idle": "aus", "unavailable": "weg"}.get(state, f"auf {state}")


def _source_entity(entry):
    """The entity an automation was triggered by: 'state of input_select.room' -> input_select.room."""
    m = re.search(r"state of ([a-z_]+\.[a-z0-9_]+)", entry.get("context_source") or entry.get("source") or "")
    return m.group(1) if m else None


def _change_at(entity_id, before, states):
    """The last state change of entity_id up to `before` (logbook of the 10 minutes before)."""
    try:
        t = dt.datetime.fromisoformat(before)
    except (TypeError, ValueError):
        return None
    rows = [r for r in logbook(entity_id, t - dt.timedelta(minutes=10), t + dt.timedelta(seconds=2))
            if "state" in r and r.get("when", "") <= before]
    return rows[-1] if rows else None


def _name(eid, states):
    s = states.get(eid) or {}
    return (s.get("attributes") or {}).get("friendly_name") or eid


def cause(entry, states, depth=2):
    """German clause naming what caused one logbook entry, following triggers back up to `depth` steps."""
    who = entry.get("context_name") or entry.get("context_entity_id_name")
    kind = entry.get("context_event_type") or ""
    if kind == "automation_triggered" or entry.get("context_domain") == "automation":
        text = f"die Automation „{who}“"
        src = _source_entity(entry)
        if src:
            prev = _change_at(src, entry.get("when", ""), states) if depth > 0 else None
            text += f", ausgelöst von „{_name(src, states)}“"
            if prev and prev.get("state"):
                text += f" ({_state_word(prev['state'])})"
                if depth > 1 and (prev.get("context_event_type") == "automation_triggered"
                                  or prev.get("context_domain") == "automation"):
                    text += ", das gesetzt hat: " + cause(prev, states, depth - 1)
        elif (entry.get("context_message") or entry.get("context_source") or "").startswith(("triggered by time", "time")):
            text += " (zeitgesteuert)"
        return text
    if kind == "script_started" or entry.get("context_domain") == "script":
        return f"das Skript „{who}“"
    if who:
        return f"{who}"
    if entry.get("context_user_id"):
        return "jemand von Hand in Home Assistant"
    return "niemand in Home Assistant — direkt am Gerät oder in dessen App geschaltet"


def explain(entity_id, now=None, states=None):
    import briefing
    now = (now or dt.datetime.now()).astimezone()
    states = states if states is not None else {s["entity_id"]: s for s in briefing._ha_states()}
    rows = [r for r in logbook(entity_id, now - dt.timedelta(hours=HOURS)) if "state" in r]
    if not rows:
        return None
    last = rows[-1]
    name = _name(entity_id, states)
    return f"{name} ging {_when(last.get('when'))} {_state_word(last.get('state'))}. Auslöser: {cause(last, states)}."


def handle(n):
    if not _ASK.search(n):
        return None
    import briefing
    import router
    if not briefing.HA_TOKEN:
        return None
    # the zone switches of the presence sensors are settings, not things that "go on"
    n = re.sub(r"\bfernseher\b", "tv", n)
    hits = [d for d in router._match_devices(n) if not re.search(r"zone|aufenthalt|erkennung|empfindlich", d["name"], re.I)]
    if len(hits) > 1 and re.search(r"\b(licht|lampe|leuchte)\b", n):
        hits = [d for d in hits if d["dom"] == "light"] or hits
    if not hits:
        return None
    if len(hits) > 1:
        return "Meinen Sie " + " oder ".join(d["name"] for d in hits[:3]) + ", Sir?"
    try:
        text = explain(hits[0]["eid"])
    except OSError:
        return "Ich komme gerade nicht an das Logbuch von Home Assistant, Sir."
    return text or f"Im Logbuch steht für {hits[0]['name']} in den letzten {HOURS} Stunden nichts, Sir."
