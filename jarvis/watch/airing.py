"""
Airing and mould: "Im Schlafzimmer 66 Prozent — jetzt lüften lohnt sich". Local, 0 tokens.

Whether airing dries a room is a question of absolute humidity (grams of water per m³), not of the percent
on the sensor: cold November air at 90 % holds far less water than the 19-°C bedroom at 65 %. This compares
the rooms' humidity sensors (HA, device_class humidity, with a room word in the name — the grow tent and the
AMS are left out) with the outside air from Open-Meteo.

  tip       a room ≥ JARVIS_AIR_TIP_RH (60 %) and the outside air at least 2 g/m³ drier: once per room and
            day between 8 and 21 h
  mould     a room above JARVIS_AIR_MOULD_RH (65 %) for more than half of each of the last 3 days (HA
            history): once per room and week
  handle(n) router: "Soll ich lüften?", "Lohnt sich lüften?", "Wie feucht ist es?"

  JARVIS_AIRING   1 / 0 = off
"""
import datetime as dt
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request

ENABLED = os.environ.get("JARVIS_AIRING", "1").strip().lower() not in ("0", "false", "no", "off")
TIP_RH = float(os.environ.get("JARVIS_AIR_TIP_RH", "60"))
MOULD_RH = float(os.environ.get("JARVIS_AIR_MOULD_RH", "65"))
DRIER_G = 2.0
ROOMS = ("wohnzimmer", "schlafzimmer", "bad", "küche", "flur", "büro", "kinderzimmer", "gästezimmer")
_OUT = {"at": 0.0, "data": None}
_LAST = {"tip": 0.0}


def absolute(temp_c, rh):
    """Grams of water per m³ (Magnus formula)."""
    return 6.112 * math.exp(17.67 * temp_c / (temp_c + 243.5)) * rh * 2.1674 / (273.15 + temp_c)


def rooms(states):
    """{room: {"rh", "temp", "rh_entity"}} from the HA states."""
    out = {}
    for eid, s in states.items():
        a = s.get("attributes") or {}
        if not eid.startswith("sensor.") or a.get("device_class") not in ("humidity", "temperature"):
            continue
        name = (a.get("friendly_name") or "").lower()
        room = next((r for r in ROOMS if r in name), None)
        if not room:
            continue
        try:
            value = float(s.get("state"))
        except (TypeError, ValueError):
            continue
        slot = out.setdefault(room, {})
        if a["device_class"] == "humidity":
            slot.update(rh=value, rh_entity=eid)
        else:
            slot["temp"] = value
    return {r: v for r, v in out.items() if "rh" in v and "temp" in v}


def outside():
    if _OUT["data"] and time.time() - _OUT["at"] < 1800:
        return _OUT["data"]
    import briefing
    d = briefing._get("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&timezone=%s"
                      "&current=temperature_2m,relative_humidity_2m" % (briefing.LAT, briefing.LON, briefing.TZ_Q))["current"]
    data = {"temp": float(d["temperature_2m"]), "rh": float(d["relative_humidity_2m"])}
    _OUT.update(at=time.time(), data=data)
    return data


def advice(room_data, out):
    """[(room, rh, inside_g, outside_g, worth)] — worth: airing makes it drier."""
    g_out = absolute(out["temp"], out["rh"])
    rows = []
    for room, v in sorted(room_data.items()):
        g_in = absolute(v["temp"], v["rh"])
        rows.append((room, v["rh"], g_in, g_out, v["rh"] >= TIP_RH and g_in - g_out >= DRIER_G))
    return rows


def _cap(room):
    return room[:1].upper() + room[1:]


def check(states_list, now=None):
    """watchers.py, every full round; says at most one tip per room and day. Returns what it said."""
    import watchers
    now = now or dt.datetime.now()
    if not ENABLED or not states_list or not 8 <= now.hour < 21 or time.time() - _LAST["tip"] < 1800:
        return []
    _LAST["tip"] = time.time()
    data = rooms({s["entity_id"]: s for s in states_list})
    if not data:
        return []
    said = []
    for room, rh, g_in, g_out, worth in advice(data, outside()):
        if worth:
            text = (f"Im {_cap(room)} sind es {rh:.0f} Prozent Luftfeuchte. Jetzt lüften lohnt sich, die Luft draußen "
                    f"ist trockener ({g_out:.0f} statt {g_in:.0f} Gramm Wasser pro Kubikmeter). Fünf bis zehn Minuten "
                    "Stoßlüften reichen.")
            if watchers.once(f"airing:{room}", text, kind="climate"):
                said.append(text)
    return said


def _history(entity_id, days=3):
    import briefing
    start = (dt.datetime.now().astimezone() - dt.timedelta(days=days)).isoformat()
    q = (f"/api/history/period/{urllib.parse.quote(start)}?filter_entity_id={entity_id}"
         "&minimal_response&no_attributes")
    req = urllib.request.Request(briefing.HA_URL + q, headers={"Authorization": f"Bearer {briefing.HA_TOKEN}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        rows = (json.loads(r.read() or b"[]") or [[]])[0]
    out = []
    for row in rows:
        try:
            out.append((dt.datetime.fromisoformat(row.get("last_changed") or row.get("lu")), float(row["state"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def wet_days(series, now=None, days=3):
    """How many of the last `days` days had the room above MOULD_RH more than half of the time."""
    now = (now or dt.datetime.now()).astimezone()
    wet = 0
    for back in range(1, days + 1):
        day = (now - dt.timedelta(days=back)).date()
        start = dt.datetime.combine(day, dt.time()).astimezone()
        end = start + dt.timedelta(days=1)
        above = total = 0.0
        prev = None
        for t, v in series + [(end, None)]:
            if prev is not None:
                a, b = max(prev[0], start), min(t, end)
                if b > a:
                    total += (b - a).total_seconds()
                    if prev[1] > MOULD_RH:
                        above += (b - a).total_seconds()
            prev = (t, v) if v is not None else prev
        if total and above / total > 0.5:
            wet += 1
    return wet


def mould_check(states_list, now=None):
    """Once a day: a room that was wet on each of the last 3 days. Returns what it said."""
    import background
    import events
    now = now or dt.datetime.now()
    if not ENABLED or not states_list or now.hour < 9 or not background.claim("airing-mould", now.date().isoformat()):
        return []
    said = []
    for room, v in rooms({s["entity_id"]: s for s in states_list}).items():
        try:
            series = _history(v["rh_entity"])
        except OSError:
            continue
        if wet_days(series, now) >= 3 and background.claim(f"mould:{room}", background.week_key(now.date())):
            text = (f"Im {_cap(room)} lag die Luftfeuchte die letzten drei Tage meist über {MOULD_RH:.0f} Prozent — "
                    "da kann sich Schimmel bilden. Mehrmals täglich kurz lüften und etwas wärmer heizen hilft.")
            events.publish("say", text, kind="climate")
            said.append(text)
    return said


def handle(n):
    if not re.search(r"\b(soll ich lüften|lohnt sich (das )?lüften|lüften lohnt|wie feucht|luftfeuchtigkeit|luftfeuchte)\b", n):
        return None
    import briefing
    data = rooms({s["entity_id"]: s for s in briefing._ha_states()})
    if not data:
        return None
    try:
        out = outside()
    except Exception:  # noqa: BLE001 — no weather: just the rooms
        return "Luftfeuchte: " + ", ".join(f"{_cap(r)} {v['rh']:.0f} Prozent" for r, v in sorted(data.items())) + "."
    rows = advice(data, out)
    parts = [f"{_cap(r)} {rh:.0f} Prozent" for r, rh, *_ in rows]
    worth = [r for r, *_, w in rows if w]
    tail = (f" Lüften lohnt sich jetzt {' und '.join('im ' + _cap(r) for r in worth)} — draußen ist die Luft trockener."
            if worth else " Lüften bringt gerade nicht viel, oder es ist ohnehin trocken genug.")
    return "Luftfeuchte: " + ", ".join(parts) + "." + tail
