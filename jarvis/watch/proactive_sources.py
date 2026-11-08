"""
Information sources of the proactive mode. Each source is one function `name(cfg, ctx)` that returns a list of
notices; proactive.py calls them with a time limit and skips a source that fails (noted once in the log).

A notice is a dict:
  id           stable identity of the thing ("noah", "offline:switch.tv") — the same id with the same
               fingerprint is never said twice
  kind         what sort of notice it is; its level (kritisch / wichtig / nett) comes from config → priorities,
               lowered by feedback per kind
  text         one short German sentence, ready to be spoken
  details      more for "Jarvis, mehr dazu" and the dashboard
  fp           fingerprint of the content: a new fingerprint means something changed and it may be said again
  valid_until  epoch seconds; after that the notice is dropped unsaid
  source       the source's name (set by proactive.py)

ctx: {"now": aware datetime, "mode": "instant" | "arrival" | "morning" | "evening", "states": {entity: state}
      or None when Home Assistant did not answer, "since": epoch seconds (arrival: left home; morning: last night)}

Sources that read Home Assistant raise when `states` is None, so they count as failed and are skipped.
"""
import datetime as dt
import hashlib
import re

import jarvis_log

log = jarvis_log.get(__name__)



def notice(id_, kind, text, details="", fp=None, valid_until=None):
    return {"id": id_, "kind": kind, "text": " ".join(str(text).split()), "details": details or text,
            "fp": str(fp if fp is not None else text), "valid_until": valid_until}


def _fp(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:12]


def _states(ctx):
    if ctx.get("states") is None:
        raise RuntimeError("Home Assistant antwortet nicht")
    return ctx["states"]


def _name(s, strip=""):
    name = ((s.get("attributes") or {}).get("friendly_name") or s.get("entity_id", "")).strip()
    return re.sub(strip, "", name, flags=re.I).strip() if strip else name


def _since(s):
    try:
        return dt.datetime.fromisoformat(s.get("last_changed") or "").timestamp()
    except (TypeError, ValueError):
        return 0.0


def _end_of_day(now):
    return dt.datetime.combine(now.date(), dt.time(23, 59, 59), tzinfo=now.tzinfo).timestamp()


def _hm(t):
    return f"{t.hour}:{t.minute:02d}"


# ── calendar, parcels ────────────────────────────────────────
def _events(day, days=1):
    import icloud_calendar
    if not icloud_calendar.configured():
        raise RuntimeError("iCloud-Kalender nicht eingerichtet")
    return icloud_calendar.events(day, days)


def _is_parcel(e):
    return e.get("title", "").startswith("📦")


def calendar(cfg, ctx):
    now, mode = ctx["now"], ctx["mode"]
    local = now.astimezone().replace(tzinfo=None)
    out = []
    if mode in ("morning", "arrival", "instant"):
        evts = [e for e in _events(local.date()) if not _is_parcel(e) and e["start"].date() == local.date()]
        ahead = [e for e in evts if e.get("allday") or e.get("end", e["start"]) >= local]
        soon = [e for e in ahead if not e.get("allday") and 0 <= (e["start"] - local).total_seconds() <= 7200]
        for e in soon:
            out.append(notice(f"cal:{e['start'].isoformat()}:{e.get('title')}", "calendar_soon",
                              f"Um {_hm(e['start'])} steht {e.get('title') or 'ein Termin'} an.",
                              _details(e), fp=_fp(e["start"], e.get("title"), e.get("location")),
                              valid_until=e["start"].astimezone().timestamp()))
        rest = [e for e in ahead if e not in soon]
        if rest and mode != "instant":
            parts = [("ganztägig " if e.get("allday") else f"um {_hm(e['start'])} ") + (e.get("title") or "Termin")
                     for e in rest[:3]]
            more = f" und {len(rest) - 3} weitere" if len(rest) > 3 else ""
            out.append(notice(f"cal:today:{local.date()}", "calendar", "Heute noch: " + ", ".join(parts) + more + ".",
                              "\n".join(_details(e) for e in rest),
                              fp=_fp(*[(e["start"], e.get("title")) for e in rest]), valid_until=_end_of_day(now)))
    if mode == "evening":
        tomorrow = local.date() + dt.timedelta(days=1)
        evts = [e for e in _events(tomorrow) if not _is_parcel(e) and not e.get("allday")
                and e["start"].date() == tomorrow]
        early = [e for e in evts if e["start"].hour < 10]
        if early:
            e = early[0]
            out.append(notice(f"cal:early:{tomorrow}", "calendar_tomorrow_early",
                              f"Morgen früh um {_hm(e['start'])}: {e.get('title') or 'ein Termin'}.",
                              "\n".join(_details(x) for x in evts), fp=_fp(e["start"], e.get("title")),
                              valid_until=e["start"].astimezone().timestamp()))
    return out


def _details(e):
    t = "ganztägig" if e.get("allday") else e["start"].strftime("%H:%M") + (
        "–" + e["end"].strftime("%H:%M") if e.get("end") else "")
    loc = f", {e['location']}" if e.get("location") else ""
    return f"{t} {e.get('title') or 'Termin'}{loc}"


def parcels(cfg, ctx):
    if ctx["mode"] not in ("morning", "arrival"):
        return []
    local = ctx["now"].astimezone().replace(tzinfo=None)
    found = [e for e in _events(local.date()) if _is_parcel(e) and e["start"].date() == local.date()]
    if not found:
        return []
    senders = list(dict.fromkeys(e["title"].lstrip("📦 ").split(":", 1)[0].strip() for e in found))
    what = "ein Paket" if len(found) == 1 else f"{len(found)} Pakete"
    return [notice(f"parcel:{local.date()}", "parcel", f"Heute kommt {what} von {' und '.join(senders)}.",
                   "\n".join(e["title"] for e in found), fp=_fp(*[e["title"] for e in found]),
                   valid_until=_end_of_day(ctx["now"]))]


# ── weather, warnings, energy ────────────────────────────────
def weather(cfg, ctx):
    if ctx["mode"] not in ("morning", "arrival"):
        return []
    import briefing
    raw = briefing._weather()
    text = re.sub(r"\s*\([^)]*\)", "", raw.split(": ", 1)[-1]).replace("°C", "Grad").rstrip(".")
    day = ctx["now"].astimezone().date()
    return [notice(f"weather:{day}", "weather", f"Wetter: {text}.", raw, fp=f"{day}",   # once a day is enough
                   valid_until=_end_of_day(ctx["now"]))]


def dwd(cfg, ctx):
    import weather_alerts as wa
    out = []
    for a in wa.fetch():
        level = wa.LEVEL.get(a.get("severity"), 0)
        if level < wa.MIN_LEVEL:
            continue
        key = f"{a.get('event_code') or a.get('event_de')}:{(a.get('onset') or '')[:13]}"
        try:
            until = dt.datetime.fromisoformat(a.get("expires") or "").timestamp()
        except ValueError:
            until = None
        out.append(notice(f"dwd:{key}", "dwd_severe" if level >= 3 else "dwd",
                          "Warnung des Wetterdienstes: " + wa.say(a),
                          " ".join((a.get("description_de") or "").split()) or wa.say(a), fp=f"{key}:{level}",
                          valid_until=until))
    return out


def energy(cfg, ctx):
    if ctx["mode"] not in ("morning", "arrival"):
        return []
    import energy_coach
    line = energy_coach.morning_line()
    if not line:
        return []
    day = ctx["now"].astimezone().date()
    return [notice(f"energy:{day}", "energy", line.replace("Energie-Coach: heute", "Die Sonne bringt heute"),
                   line, fp=f"{day}", valid_until=_end_of_day(ctx["now"]))]


# ── the house (Home Assistant) ───────────────────────────────
def noah(cfg, ctx):
    c = cfg.sources.get("noah") or {}
    states = _states(ctx)
    sw = states.get(c.get("switch") or "")
    if not sw or sw.get("state") != "on":
        return []
    master, slave = (states.get(c.get(k) or "", {}).get("state") for k in ("master", "slave"))
    try:
        diff = abs(float(master) - float(slave))
        nums = f"Master {float(master):.0f} %, Slave {float(slave):.0f} %"
    except (TypeError, ValueError):
        diff, nums = None, "Ladestände gerade unbekannt"
    text = "Die NOAH-Akkus laufen auseinander" + (f": {diff:.0f} Prozent Unterschied." if diff is not None else ".")
    return [notice("noah", "noah", text, f"{nums}. Die HA-Automation „StromAutomatisierung“ drosselt die "
                   "Ausgangsleistung, bis sich die Akkus angeglichen haben.", fp=sw.get("last_changed") or "on")]


def devices_offline(cfg, ctx):
    c = cfg.sources.get("devices_offline") or {}
    states = _states(ctx)
    pattern = re.compile(c.get("pattern") or r"_cloud_verbindung$")
    ignore = set(c.get("ignore") or [])
    limit = float(c.get("minutes", 15)) * 60
    now = ctx["now"].timestamp()
    out = []
    for eid, s in sorted(states.items()):
        if eid in ignore or not pattern.search(eid) or s.get("state") not in ("off", "unavailable"):
            continue
        since = _since(s)
        if since and now - since < limit:
            continue
        name = _name(s, r"\s*cloud-verbindung\s*")
        out.append(notice(f"offline:{eid}", "device_offline", f"{name} ist offline.",
                          f"{name} ({eid}) hat seit {dt.datetime.fromtimestamp(since):%d.%m. %H:%M} keine "
                          "Verbindung." if since else f"{name} ({eid}) hat keine Verbindung.",
                          fp=s.get("last_changed") or s.get("state")))
    return out


def _binary(states, classes):
    return [(eid, s) for eid, s in sorted(states.items()) if eid.startswith("binary_sensor.")
            and (s.get("attributes") or {}).get("device_class") in classes and s.get("state") == "on"]


def safety(cfg, ctx):
    classes = set((cfg.sources.get("safety") or {}).get("device_classes") or [])
    word = {"moisture": "Wasser", "smoke": "Rauch", "gas": "Gas", "carbon_monoxide": "Kohlenmonoxid"}
    return [notice(f"safety:{eid}", "safety",
                   f"Achtung: {_name(s)} meldet {word.get(s['attributes'].get('device_class'), 'Alarm')}!",
                   f"{_name(s)} ({eid}) seit {s.get('last_changed', '')[:16].replace('T', ' ')}",
                   fp=s.get("last_changed") or "on")
            for eid, s in _binary(_states(ctx), classes)]


def devices_on(cfg, ctx):
    if ctx["mode"] != "evening":
        return []
    import away_check
    import jarvis_profile as profile
    found = away_check.still_on(_states(ctx), profile.ha("away_check", {}) or {})
    if not found:
        return []
    names = [label for _, label in found]
    return [notice("devices_on", "devices_on", "Noch an: " + ", ".join(names[:4])
                   + (f" und {len(names) - 4} weitere" if len(names) > 4 else "") + ".",
                   "\n".join(names), fp=_fp(*names))]


def windows(cfg, ctx):
    if ctx["mode"] != "evening":
        return []
    classes = set((cfg.sources.get("windows") or {}).get("device_classes") or [])
    found = [_name(s) for _, s in _binary(_states(ctx), classes)]
    if not found:
        return []
    return [notice("windows", "window_open", "Noch offen: " + ", ".join(found) + ".", "\n".join(found),
                   fp=_fp(*found))]


def missed(cfg, ctx):
    """What Jarvis announced while the user was away or asleep (the old welcome back, as one notice)."""
    if ctx["mode"] not in ("arrival", "morning") or not ctx.get("since"):
        return []
    import events
    skip = {"alarm", "timers", "welcome", "wake", "proactive"}
    seen, items = set(), []
    for n in events.notices_since(ctx["since"]):
        if n.get("kind") in skip or str(n.get("kind", "")).startswith("proactive"):
            continue
        text = re.sub(r"^Sir,\s*", "", n.get("text", "")).strip()
        if text and text not in seen:
            seen.add(text)
            items.append(text[:1].upper() + text[1:])
    if not items:
        return []
    word = "In der Nacht" if ctx["mode"] == "morning" else "Während du weg warst"
    return [notice(f"missed:{int(ctx['since'])}", "missed", f"{word}: " + " ".join(items[-2:])
                   + (f" Und {len(items) - 2} weitere Meldungen." if len(items) > 2 else ""),
                   "\n".join(items), fp=_fp(*items))]


SOURCES = {"safety": safety, "dwd": dwd, "noah": noah, "devices_offline": devices_offline, "calendar": calendar,
           "parcels": parcels, "weather": weather, "energy": energy, "devices_on": devices_on,
           "windows": windows, "missed": missed}
# What each trigger asks; the instant round only asks what can be urgent and is cheap to ask every 30 s.
# The calendar is not in it: watchers.calendar already says "In 15 Minuten: …".
BY_MODE = {"instant": ("safety", "noah", "devices_offline", "dwd"),
           "arrival": ("safety", "dwd", "noah", "devices_offline", "calendar", "parcels", "missed", "weather",
                       "energy"),
           "morning": ("safety", "dwd", "noah", "devices_offline", "calendar", "parcels", "weather", "energy",
                       "missed"),
           "evening": ("safety", "dwd", "devices_on", "windows", "calendar", "noah", "devices_offline")}
NEEDS_HA = {"safety", "noah", "devices_offline", "devices_on", "windows"}
