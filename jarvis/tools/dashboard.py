"""
Live figures for the HUD panels around the graph: weather + golf verdict,
next appointment, house (solar, storage, rooms, air), the user's watch, unread
mail and the home systems (Pi, NAS, FRITZ!Box, Home Assistant).

Every source has its own cache. A request never waits on a slow source: it
gets the last known value and a stale source refreshes in the background.
"""
import datetime as dt
import os
import re
import threading
import time

import briefing
import jarvis_profile as profile

MAIL_LIST = int(os.environ.get("JARVIS_DASH_MAIL_LIST", "50"))   # unread mails the tablet's mail sheet lists

_CACHE, _BUSY, _LOCK = {}, set(), threading.Lock()


def _cached(key, ttl, fn):
    with _LOCK:
        val, at = _CACHE.get(key, (None, 0.0))
        if time.time() - at < ttl or key in _BUSY:
            return val
        _BUSY.add(key)

    def run():
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001 — a dead source shows as such, never breaks the HUD
            res = {"error": str(e)[:160]}
        with _LOCK:
            _CACHE[key] = (res, time.time())
            _BUSY.discard(key)
    threading.Thread(target=run, daemon=True).start()
    return val


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── sources ──────────────────────────────────────

# A round needs daylight: this close to sunset the verdict is for tomorrow.
GOLF_DAYLIGHT_MIN = 90
# Working hours (JARVIS_WORK_END): the activity only fits in after that.
WORK_END = dt.time(16, 0)
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


def _golf_day_index(sunsets):
    """First forecast day with a daylight window outside working hours."""
    now = dt.datetime.now()
    for i, s in enumerate(sunsets):
        sunset = dt.datetime.fromisoformat(s)
        start = max(now, dt.datetime.combine(sunset.date(), WORK_END if sunset.weekday() < 5 else dt.time(7, 0)))
        if start < sunset - dt.timedelta(minutes=GOLF_DAYLIGHT_MIN):
            return i
    return len(sunsets) - 1


def _golf_verdict(code, rain, wind, tmax):
    if code >= 95 or rain >= 70 or wind >= 45 or tmax < 3:
        return ("nein", "bad")
    if rain >= 40 or wind >= 30 or tmax < 9:
        return ("mit Jacke", "warn")
    return ("ja", "ok")


RAIN_CHANCE = 30         # % from which an hour counts as a rain hour, even without a forecast amount
RAIN_FLOOR = 10          # ...and with an amount forecast, from here (0.1 mm at 2-4 % is the model's noise)


def rain_hour(p, mm):
    """An hour it may rain in: some amount forecast at a small chance, or a fair chance alone.
    40 % alone hid evenings with 1.5 mm at 12-24 %."""
    return (p or 0) >= RAIN_CHANCE or ((mm or 0) >= 0.1 and (p or 0) >= RAIN_FLOOR)


def _pct(lo, hi):
    return f"{hi} %" if lo == hi else f"{lo}–{hi} %"


def rain_day(hours):
    """hours: one day's [(hour, probability %, mm)], 0-23. Each stretch of rain hours with its own
    chance — the same line for every day: "6–12 Uhr (30–100 %), 23–24 Uhr (18 %)" /
    "ganztägig (40–100 %)" / "" (no rain hour)."""
    spans, cur = [], None
    for h, p, mm in hours:
        if rain_hour(p, mm):
            cur = [h, h + 1, p or 0, p or 0] if cur is None else [cur[0], h + 1, min(cur[2], p or 0), max(cur[3], p or 0)]
        elif cur:
            spans.append(cur)
            cur = None
    if cur:
        spans.append(cur)
    if spans and sum(e - s for s, e, _, _ in spans) >= 20:
        when = f"ganztägig ({_pct(min(sp[2] for sp in spans), max(sp[3] for sp in spans))})"
    else:
        when = ", ".join(f"{s}–{e} Uhr ({_pct(lo, hi)})" for s, e, lo, hi in spans[:4])
    return dict(when=when, peak=max([p or 0 for _, p, _ in hours] or [0]),
                mm=round(sum(m or 0 for _, _, m in hours), 1),
                times=", ".join(f"{s}–{e}" for s, e, _, _ in spans[:3]))


def _weather():
    d = briefing._get("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&timezone=%s"
                      "&forecast_days=7&current=temperature_2m,weather_code"
                      "&hourly=precipitation_probability,precipitation"
                      "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_min,"
                      "precipitation_probability_max,precipitation_sum,wind_speed_10m_max,sunrise,sunset"
                      % (briefing.LAT, briefing.LON, briefing.TZ_Q))
    day, now = d["daily"], d.get("current", {})
    rain, wind, tmax = day["precipitation_probability_max"][0] or 0, day["wind_speed_10m_max"][0] or 0, \
        day["temperature_2m_max"][0]
    code = now.get("weather_code", day["weather_code"][0])
    i = _golf_day_index(day["sunset"])
    if i == 0:
        golf = _golf_verdict(code, rain, wind, tmax)
    else:
        golf = _golf_verdict(day["weather_code"][i], day["precipitation_probability_max"][i] or 0,
                             day["wind_speed_10m_max"][i] or 0, day["temperature_2m_max"][i])
    golf_day = "heute" if i == 0 else "morgen" if i == 1 else \
        WEEKDAYS[dt.date.fromisoformat(day["sunset"][i][:10]).weekday()]
    # the next days for the iPad's forecast row (the PC only shows today)
    hr = d.get("hourly") or {}
    by_day = {}
    for t, p, mm in zip(hr.get("time", []), hr.get("precipitation_probability", []), hr.get("precipitation", [])):
        by_day.setdefault(t[:10], []).append((int(t[11:13]), p or 0, round(mm or 0, 1)))
    days = []
    for k in range(min(5, len(day.get("time", [])))):
        hours = by_day.get(day["time"][k], [])
        days.append(dict(day="Heute" if k == 0 else WEEKDAYS[dt.date.fromisoformat(day["time"][k]).weekday()][:2],
                         code=day["weather_code"][k], sky=briefing.WEATHER_CODES.get(day["weather_code"][k], "wechselhaft"),
                         tmin=day["temperature_2m_min"][k], tmax=day["temperature_2m_max"][k],
                         rain=day["precipitation_probability_max"][k] or 0,
                         rain_min=(day.get("precipitation_probability_min") or [None] * 7)[k] or 0,
                         mm=round((day.get("precipitation_sum") or [None] * 7)[k] or 0, 1),
                         rain_when=rain_day(hours)["when"], rain_times=rain_day(hours)["times"],
                         hours=[dict(h=h, p=p, mm=m, rain=rain_hour(p, m)) for h, p, m in hours]))
    return dict(now=now.get("temperature_2m"), tmin=day["temperature_2m_min"][0], tmax=tmax, code=code,
                sky=briefing.WEATHER_CODES.get(code, "wechselhaft"), rain=rain, wind=round(wind),
                sunset=day["sunset"][0][-5:], golf=golf[0], golf_cls=golf[1], golf_day=golf_day, days=days,
                location=dict(latitude=float(briefing.LAT), longitude=float(briefing.LON), timezone=profile.TIMEZONE),
                sun_times=[dict(date=stamp[:10], sunrise=(day.get("sunrise") or [None] * len(day["sunset"]))[k],
                                sunset=stamp) for k, stamp in enumerate(day["sunset"])])


def _agenda(limit=5):
    """The next few appointments of the coming week, for the iPad's day view."""
    import icloud_calendar as cal
    if not cal.configured():
        return None
    now, out = dt.datetime.now(), []
    for e in cal.events(dt.date.today(), 7):
        if e.get("calendar", "").lower() == "abfuhrtermine" and not e.get("allday"):
            continue
        if (e.get("end") or e["start"]) < now:
            continue
        d = e["start"].date()
        out.append(dict(title=e.get("title", "(ohne Titel)"), allday=bool(e.get("allday")),
                        day="Heute" if d == now.date() else "Morgen" if d == now.date() + dt.timedelta(days=1)
                        else WEEKDAYS[d.weekday()][:2] + e["start"].strftime(" %d.%m."),
                        time="" if e.get("allday") else e["start"].strftime("%H:%M"),
                        now=e["start"] <= now))
        if len(out) >= limit:
            break
    return out


def _next_event():
    import icloud_calendar as cal
    if not cal.configured():
        return None
    now = dt.datetime.now()
    for e in cal.events(dt.date.today(), 7):
        if e.get("calendar", "").lower() == "abfuhrtermine" and not e.get("allday"):
            continue
        end = e.get("end") or e["start"]
        if end >= now:
            day = "heute" if e["start"].date() == now.date() else \
                "morgen" if e["start"].date() == now.date() + dt.timedelta(days=1) else \
                ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")[e["start"].weekday()] + e["start"].strftime(" %d.%m.")
            return dict(title=e.get("title", "(ohne Titel)"),
                        when=day + ("" if e.get("allday") else e["start"].strftime(" %H:%M")))
    return {"title": "", "when": "keine Termine"}


def _where():
    """The room the presence sensors see the user in, for the tablet's "Raum" tile; None when off."""
    try:
        import room_scope
        cur = room_scope.current()
    except Exception:  # noqa: BLE001
        return None
    if cur["status"] == "off":
        return None
    return dict(room=cur["room"], sure=cur["sure"], offline=cur["status"] == "offline",
                candidates=cur["candidates"])


def _house():
    states = briefing._ha_states()
    by = {s["entity_id"]: s for s in states}
    val = lambda eid: _num(by.get(eid, {}).get("state")) if eid else None  # noqa: E731
    total = lambda ids: sum(val(e) or 0 for e in (ids or []))  # noqa: E731
    pattern = profile.ha("battery_soc_pattern")
    bats = sorted((int(m.group(1)), _num(s["state"])) for k, s in by.items()
                  if pattern and (m := re.fullmatch(pattern, k))) if pattern else []
    rooms = []
    for eid, name in profile.ha("rooms", []):
        s = by.get(eid)
        if s:
            a = s.get("attributes", {})
            rooms.append(dict(name=name, temp=_num(a.get("current_temperature")),
                              target=_num(a.get("temperature")), heating=s["state"] == "heat"))
    pm = val(profile.ha("air_pm25"))
    rt = profile.ha("router", {})
    return dict(
        where=_where(),
        solar_w=round(total(profile.ha("solar_power"))),
        soc=val(profile.ha("storage_soc")), bats=[b for _, b in bats if b is not None],
        gen_kwh=round(total(profile.ha("generation_today")), 1),
        charge_w=val(profile.ha("charge_power")), out_w=val(profile.ha("output_power")),
        rooms=rooms, pm25=pm,
        air="gut" if pm is not None and pm <= 12 else "mäßig" if pm is not None and pm <= 35 else
        "schlecht" if pm is not None else None,
        router=dict(online=by.get(rt.get("online"), {}).get("state") == "on",
                    down=val(rt.get("down")), up=val(rt.get("up")), temp=val(rt.get("temp"))),
        person=by.get(profile.ha("person") or "", {}).get("state"),
        # The station goes black while this is on — the iPad's focus follows the sleep schedule.
        night=by.get(profile.ha("station_night") or "", {}).get("state") == "on",
    )


def _garmin():
    import jarvis_mcp as j
    if not (j.GARMIN_EMAIL and j.GARMIN_PASSWORD):
        return {"error": "nicht verbunden"}
    import os
    from garminconnect import Garmin
    g = Garmin(j.GARMIN_EMAIL, j.GARMIN_PASSWORD, return_on_mfa=True)
    g.login(j.GARMIN_TOKENS)
    os.makedirs(j.GARMIN_TOKENS, exist_ok=True)
    g.client.dump(j.GARMIN_TOKENS)
    day = dt.date.today().isoformat()
    s = (g.get_sleep_data(day) or {}).get("dailySleepDTO") or {}
    st = g.get_stats(day) or {}
    return dict(
        sleep_h=round((s.get("sleepTimeSeconds") or 0) / 3600, 1) or None,
        sleep_score=((s.get("sleepScores") or {}).get("overall") or {}).get("value"),
        battery=st.get("bodyBatteryMostRecentValue"), steps=st.get("totalSteps"),
        rhr=st.get("restingHeartRate"), stress=st.get("averageStressLevel"),
        at=time.strftime("%H:%M"))


def _mail():
    import email
    import email.header
    import email.utils
    import mailboxes
    if not mailboxes.configured():
        return {"error": "nicht verbunden"}
    dec = lambda v: str(email.header.make_header(email.header.decode_header(v or ""))).strip()  # noqa: E731

    def read(acc, box):                       # box is read-only: nothing gets marked as read
        _, data = box.search(None, "UNSEEN")
        ids = data[0].split()
        latest = []
        if not ids:
            return 0, latest
        # one FETCH for the whole page of headers: fifty round trips per account would stall the refresh
        _, parts = box.fetch(b",".join(ids[-MAIL_LIST:]).decode(), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        for part in parts:
            if not isinstance(part, tuple):
                continue
            msg = email.message_from_bytes(part[1])
            name, addr = email.utils.parseaddr(dec(msg.get("From")))
            sent = email.utils.parsedate_to_datetime(msg["Date"]) if msg.get("Date") else None
            latest.append(dict(sender=name or addr, subject=dec(msg.get("Subject")) or "(ohne Betreff)",
                               when=sent.strftime("%d.%m. %H:%M") if sent else "", ts=sent.timestamp() if sent else 0,
                               account=acc["name"]))
        return len(ids), latest

    results, errors = mailboxes.each(read, timeout=15)
    if not results:
        return {"error": "; ".join(errors) or "nicht erreichbar"}
    newest = sorted((m for _, ms in results for m in ms), key=lambda m: -m["ts"])
    # latest stays four for the small tiles; the tablet's mail sheet scrolls through the whole page
    return dict(unread=sum(n for n, _ in results), latest=newest[:4], inbox=newest[:MAIL_LIST], at=time.strftime("%H:%M"),
                accounts=len(results) + len(errors), errors=errors)

def _nas():
    import jarvis_mcp as j
    if not (j.SYNO_USER and j.SYNO_PASSWORD):
        return {"error": "nicht verbunden"}
    sid = j._syno({"api": "SYNO.API.Auth", "version": 6, "method": "login", "account": j.SYNO_USER,
                   "passwd": j.SYNO_PASSWORD, "session": "jarvis", "format": "sid"})["sid"]
    try:
        info = j._syno({"api": "SYNO.Core.System", "version": 1, "method": "info"}, sid)
        st = j._syno({"api": "SYNO.Storage.CGI.Storage", "version": 1, "method": "load_info"}, sid)
    finally:
        try:
            j._syno({"api": "SYNO.API.Auth", "version": 6, "method": "logout", "session": "jarvis"}, sid)
        except Exception:  # noqa: BLE001
            pass
    used = total = 0
    for v in st.get("volumes", []):
        used += int(v.get("size", {}).get("used", 0))
        total += int(v.get("size", {}).get("total", 0))
    disks = st.get("disks", [])
    healthy = all(k.get("status") == "normal" and k.get("smart_status", "normal") in ("normal", "safe")
                  for k in disks) and all(v.get("status") == "normal" for v in st.get("volumes", []))
    return dict(model=info.get("model"), temp=_num(info.get("sys_temp")), ok=healthy, disks=len(disks),
                used_pct=round(used * 100 / total) if total else None, used_tb=round(used / 1e12, 1),
                total_tb=round(total / 1e12, 1))


def _pi():
    import jarvis_mcp as j
    out = j._pi_status()
    if out.startswith("Pi nicht erreichbar"):
        return {"error": "nicht erreichbar"}
    grab = lambda pat: (m.group(1) if (m := re.search(pat, out)) else None)  # noqa: E731
    containers = re.findall(r"container (\S+): (.*)", out)
    return dict(temp=_num(grab(r"temp: ([\d.]+)")), load=_num(grab(r"load: ([\d.]+)")),
                disk=_num(grab(r"\((\d+)%\)")), uptime=grab(r"uptime: up (.*)"),
                containers=len(containers), up=sum(1 for _, s in containers if s.startswith("Up")))


# ── public ───────────────────────────────────────

def data():
    return dict(
        weather=_cached("weather", 1800, _weather),
        event=_cached("event", 600, _next_event),
        agenda=_cached("agenda", 600, _agenda),
        house=_cached("house", 30, _house),
        me=_cached("garmin", 900, _garmin),     # Garmin throttles logins — keep it rare
        mail=_cached("mail", 300, _mail),
        nas=_cached("nas", 300, _nas),
        pi=_cached("pi", 120, _pi),
        pc=_cached("pc", 20, _pc),
        homelab=_cached("homelab", 30, _homelab),
        usage=_cached("usage", 300, _usage),
        resources=_resources(),
    )


def _usage():
    """Tokens this week against last week, the local share, the dearest repeated questions, misses."""
    import learning
    week = learning.report(7)
    both = learning.report(14)
    prev = max(0, both.get("tokens", 0) - week.get("tokens", 0))
    total = week.get("total", 0)
    out = dict(total=total, llm=week.get("llm", 0), tokens=week.get("tokens", 0), prev_tokens=prev,
               local_pct=round(100 * week.get("local", 0) / total) if total else None,
               trend_pct=round(100 * (week.get("tokens", 0) - prev) / prev) if prev else None,
               top=[dict(msg=m, count=c, tokens=t) for m, c, t in week.get("repeat") or []][:5],
               aliases=week.get("aliases", 0), drafts=week.get("drafts", 0))
    try:
        import selfcheck
        out["misses"] = len(selfcheck.misses(7))
    except Exception:  # noqa: BLE001
        out["misses"] = None
    return out


def _resources():
    import resources
    st = resources.status()
    return {"ok": st["ok"], "text": st["text"], "shed": st.get("shed", [])}


def _homelab():
    import homelab
    return homelab.tile()


def _pc():
    import infra
    return infra.pc_metrics()
