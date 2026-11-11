"""
"Was steht morgen an?" — one short look ahead, also as the last step of "Gute Nacht". Local, 0 tokens.

    Ihr Wecker klingelt um 6:45. Morgen um 9:30 Zahnarzt, danach noch zwei Termine. Ein Paket von Amazon
    soll kommen. Wetter: teils bewölkt, 4 bis 11 Grad, Regenrisiko 20 Prozent.

Sources, each optional (a missing one is left out, nothing is guessed):
  alarm     the next Jarvis alarm within the next 24 hours (scheduler.py)
  calendar  tomorrow's appointments (icloud_calendar.py); the all-day "📦 …" entries of shipments.py
            are the expected parcels
  weather   Open-Meteo for tomorrow (the same place as the morning briefing)

  handle(n)   local router: "was steht morgen an", "was ist morgen los", "vorschau auf morgen"
  spoken(now) the sentence
"""
import datetime as dt
import re


def _alarm(now):
    import scheduler
    horizon = now.timestamp() + 24 * 3600
    alarms = [i for i in scheduler.items() if i.get("kind") == "alarm" and now.timestamp() < i["at"] <= horizon]
    if not alarms:
        return ""
    at = dt.datetime.fromtimestamp(alarms[0]["at"])
    return f"Ihr Wecker klingelt um {at.hour}:{at.minute:02d}."


def _calendar(day):
    import icloud_calendar
    if not icloud_calendar.configured():
        return "", ""
    evts = icloud_calendar.events(day, 1)
    parcels = [e for e in evts if e.get("allday") and str(e.get("title", "")).startswith("📦")]
    timed = [e for e in evts if not e.get("allday") and e["start"].date() == day]
    allday = [e for e in evts if e.get("allday") and e not in parcels]
    parts = []
    if timed:
        first = timed[0]
        parts.append(f"Morgen um {first['start'].hour}:{first['start'].minute:02d} {first.get('title') or 'ein Termin'}")
        if len(timed) > 1:
            more = len(timed) - 1
            parts[-1] += ", danach noch " + ("ein Termin" if more == 1 else f"{more} Termine")
        parts[-1] += "."
    if allday:
        parts.append("Ganztägig: " + ", ".join(e.get("title") or "ohne Titel" for e in allday[:2]) + ".")
    if not parts:
        parts.append("Morgen stehen keine Termine im Kalender.")
    pkg = ""
    if parcels:
        shops = [re.sub(r"^📦\s*", "", e.get("title", "")).split("·")[0].strip() for e in parcels]
        pkg = ("Ein Paket" if len(parcels) == 1 else f"{len(parcels)} Pakete") + \
            (f" von {', '.join(s for s in shops if s)}" if any(shops) else "") + " soll kommen."
    return " ".join(parts), pkg


def _weather(day):
    import briefing
    d = briefing._get("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&timezone=%s"
                      "&forecast_days=2&daily=weather_code,temperature_2m_max,temperature_2m_min,"
                      "precipitation_probability_max" % (briefing.LAT, briefing.LON, briefing.TZ_Q))["daily"]
    i = d["time"].index(day.isoformat()) if day.isoformat() in d["time"] else 1
    return (f"Wetter: {briefing.WEATHER_CODES.get(d['weather_code'][i], 'wechselhaft')}, "
            f"{d['temperature_2m_min'][i]:.0f} bis {d['temperature_2m_max'][i]:.0f} Grad, "
            f"Regenrisiko {d['precipitation_probability_max'][i]} Prozent.")


def spoken(now=None):
    now = now or dt.datetime.now()
    day = (now + dt.timedelta(days=1)).date() if now.hour >= 4 else now.date()   # after midnight "morgen" is today
    parts = []
    for fn in (lambda: _alarm(now), lambda: _calendar(day), lambda: _weather(day)):
        try:
            got = fn()
        except Exception:  # noqa: BLE001 — calendar or weather down: say the rest
            continue
        parts += [g for g in (got if isinstance(got, tuple) else (got,)) if g]
    return " ".join(parts) or "Für morgen habe ich gerade keine Daten, Sir."


_ASK = re.compile(r"\b(was steht morgen an|was (ist|steht) morgen (los|an)|was gibt es morgen|"
                  r"vorschau (auf|für) morgen|was erwartet mich morgen|was kommt morgen)\b")


def handle(n):
    if not _ASK.search(n):
        return None
    return spoken()
