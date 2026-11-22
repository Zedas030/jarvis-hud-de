"""
"Seit wann ist das Licht an?" — when a device's current state began, from the Home Assistant history. Local, 0 tokens.

    LED ist seit 16:52 an, also seit 51 Minuten.

`last_changed` alone is not enough: a restart of Home Assistant stamps every entity with the restart time,
and a Zigbee lamp that drops off for a minute comes back as a fresh change. The history is walked back over
both (same state again, "unavailable"/"unknown" gaps) to the moment the state really changed.

  began(entity_id, state, last_changed=None, now=None)  (aware datetime, lower_bound) or (None, False)
  phrase(entity_id, state, last_changed=None, now=None) "seit 16:52 an, also seit 51 Minuten" or ""
"""
import datetime as dt
import os
import urllib.parse

DAYS = int(os.environ.get("JARVIS_SINCE_DAYS", "7"))
_GAP = {"unavailable", "unknown"}
_WORD = {"on": "an", "off": "aus", "playing": "an", "idle": "aus", "paused": "pausiert", "open": "offen",
         "closed": "zu", "home": "zu Hause", "not_home": "weg"}


def _parse(iso):
    try:
        t = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _history(entity_id, start, end=None):
    import ha_why
    end = end or dt.datetime.now(dt.timezone.utc)      # without end_time HA returns only one day from `start`
    q = ("?filter_entity_id=" + urllib.parse.quote(entity_id) + "&end_time=" + urllib.parse.quote(end.isoformat())
         + "&minimal_response&no_attributes")
    data = ha_why._get("/api/history/period/" + urllib.parse.quote(start.isoformat()) + q)
    return data[0] if data else []


def began(entity_id, state, last_changed=None, now=None):
    """When `state` began. lower_bound=True: it has not changed in the whole history window."""
    now = now or dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(days=DAYS)
    try:
        rows = _history(entity_id, start, now)
    except Exception:  # noqa: BLE001 — no history: last_changed is the best we have
        rows = []
    t = None
    for row in reversed(rows):
        s = row.get("state")
        if s == state:
            t = _parse(row.get("last_changed")) or t
        elif s in _GAP:
            continue
        else:
            break
    else:
        if t is not None:
            return max(t, start), True
    if t is not None:
        return t, False
    lc = _parse(last_changed)
    return (lc, False) if lc else (None, False)


def _when(t, now):
    local, today = t.astimezone(), now.astimezone().date()
    clock = f"{local.hour}:{local.minute:02d}"
    if local.date() == today:
        return clock
    if local.date() == today - dt.timedelta(days=1):
        return f"gestern {clock}"
    return f"dem {local.day}.{local.month}. um {clock}"


def _duration(secs):
    m = int(secs // 60)
    if m < 60:
        return f"{m} Minute" if m == 1 else f"{m} Minuten"
    h, m = divmod(m, 60)
    if h < 24:
        hours = "einer Stunde" if h == 1 else f"{h} Stunden"
        return hours + (f" und {m} Minuten" if m >= 5 else "")
    d = h // 24
    return "einem Tag" if d == 1 else f"{d} Tagen"


def stamp(entity_id, state, last_changed=None, now=None):
    """'seit 16:52 (51 Minuten)' for the home_states tool, or ""."""
    now = now or dt.datetime.now(dt.timezone.utc)
    t, bound = began(entity_id, state, last_changed, now)
    if t is None:
        return ""
    if bound:
        return f"seit mehr als {DAYS} Tagen"
    return f"seit {_when(t, now)} ({_duration(max(0.0, (now - t).total_seconds()))})"


def phrase(entity_id, state, last_changed=None, now=None):
    """'seit 16:52 an, also seit 51 Minuten' — the part after "<Name> ist", or "" when unknown."""
    now = now or dt.datetime.now(dt.timezone.utc)
    word = _WORD.get(state, f"auf {state}")
    t, bound = began(entity_id, state, last_changed, now)
    if t is None:
        return ""
    if bound:
        return f"seit mehr als {DAYS} Tagen {word}"
    secs = max(0.0, (now - t).total_seconds())
    if secs < 60:
        return {"an": "gerade eben angegangen", "aus": "gerade eben ausgegangen"}.get(word, f"seit gerade eben {word}")
    return f"seit {_when(t, now)} {word}, also seit {_duration(secs)}"
