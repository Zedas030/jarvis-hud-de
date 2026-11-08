"""
Official weather warnings of the Deutscher Wetterdienst for the weather location — read
through Bright Sky (api.brightsky.dev, free, no key; it only gets the coordinates).

  current()   the warnings in force now, spoken ("Gibt es Unwetterwarnungen?")
  step()      every JARVIS_ALERTS_SECONDS (600): each new warning once —
              moderate and up by day, severe and extreme also in the quiet hours
              and on the phone; minor ones (frost on a November morning) stay quiet
  is a warning updated (new id, same event), it is said again only if it got worse

Announced ids live in state.json → weather_alerts (pruned when they expire).
"""
import datetime as dt
import os

import background
import commands
import events

ENABLED = os.environ.get("JARVIS_WEATHER_ALERTS", "1").strip() not in ("0", "false", "no", "off")
EVERY = int(os.environ.get("JARVIS_ALERTS_SECONDS", "600"))
URL = "https://api.brightsky.dev/alerts?lat=%s&lon=%s"
LEVEL = {"minor": 1, "moderate": 2, "severe": 3, "extreme": 4}
MIN_LEVEL = LEVEL.get(os.environ.get("JARVIS_ALERTS_MIN", "moderate").strip(), 2)


def fetch():
    import briefing
    d = briefing._get(URL % (briefing.LAT, briefing.LON), timeout=10)
    return (d or {}).get("alerts") or []


def _time(s):
    try:
        t = dt.datetime.fromisoformat(s).astimezone()
    except (TypeError, ValueError):
        return ""
    today = dt.datetime.now().astimezone().date()
    day = "heute" if t.date() == today else "morgen" if t.date() == today + dt.timedelta(days=1) else t.strftime("%d.%m.")
    return f"{day} {t.strftime('%H:%M')}"


def say(a):
    head = (a.get("headline_de") or a.get("event_de") or "Wetterwarnung").strip()
    when = ""
    if a.get("onset") and a.get("expires"):
        when = f" von {_time(a['onset'])} bis {_time(a['expires'])}"
    desc = " ".join((a.get("description_de") or "").split())
    first = desc.split(". ")[0].rstrip(".") + "." if desc else ""
    return f"{head}{when}. {first}".strip()


def current():
    try:
        alerts = fetch()
    except Exception as e:  # noqa: BLE001
        return f"Die Warnungen des Wetterdienstes kann ich gerade nicht abrufen ({str(e)[:60]})."
    if not alerts:
        return "Für hier gibt es keine Warnungen des Deutschen Wetterdienstes."
    alerts = sorted(alerts, key=lambda a: -LEVEL.get(a.get("severity"), 0))
    return "Warnungen des Deutschen Wetterdienstes: " + " ".join(say(a) for a in alerts[:3])


def new_ones(alerts, seen, now=None):
    """[(alert, critical)] to announce now; updates `seen` {key: level} (pruned when expired)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []
    for a in alerts:
        level = LEVEL.get(a.get("severity"), 0)
        key = f"{a.get('event_code') or a.get('event_de')}:{(a.get('onset') or '')[:13]}"
        if level < MIN_LEVEL or seen.get(key, {}).get("level", 0) >= level:
            continue
        seen[key] = {"level": level, "until": a.get("expires") or ""}
        out.append((a, level >= 3))
    for key in [k for k, v in seen.items() if v.get("until") and v["until"] < now.isoformat()]:
        del seen[key]
    return out


def step():
    import proactive
    if proactive.owns("dwd"):
        return                     # the proactive mode announces warnings, with its quiet rules and in the room
    alerts = fetch()
    found = []

    def mut(d):
        seen = d.setdefault("weather_alerts", {})
        found.extend(new_ones(alerts, seen))
    commands.update_state(mut)
    if not found:
        return
    import infra
    for a, critical in found:
        text = "Sir, Warnung des Deutschen Wetterdienstes: " + say(a)
        events.publish("say", text, kind="weather", priority="critical" if critical else "normal")
        if critical:
            infra.phone_notify(text, title="Unwetterwarnung")


def start():
    if ENABLED:
        background.loop("weather_alerts", EVERY, step)
