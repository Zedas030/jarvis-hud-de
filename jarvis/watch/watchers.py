"""
Proactive notices — Jarvis speaks up by himself. Plain rules, 0 tokens.

  appliances   "Die Waschmaschine ist fertig" — from a power sensor in Home
               Assistant (config/home.json → "appliances", see the example).
  calendar     "In 15 Minuten: Zahnarzt" (JARVIS_EVENT_LEAD_MIN, 0 = off); with a place
               in the appointment: "Zeit loszufahren" from the driving time (commute.py)
  parcels      "Heute kommt ein Paket von …" from shipping mails (parcels.py)
  storage      "Der Speicher ist voll, gute Zeit für die Waschmaschine" — once a day
  nas          a disk not healthy or the volume over 90 % — once a day
  network      a new device in the WLAN (from the network watchdog in briefing.py)
  printer      "Der 3D-Druck ist fertig", failed, paused for a reason, an error (printer.py)
  away         after leaving home: what still runs goes to the phone and becomes a proposal (away_check.py)
  morning      the first light/TV/Echo in the living room in the morning: the Echo briefs (morning_echo.py)

Every notice is published on the event bus; the HUD speaks it, or only shows
it during the quiet hours (JARVIS_QUIET_HOURS). Each one comes once per key
and day, also across restarts (state.json → "notified").
"""
import datetime as dt
import os
import threading
import time

import away_check
import commands
import events
import jarvis_profile as profile
import morning_echo
import proactive
import airing
import token_budget

EVERY = int(os.environ.get("JARVIS_WATCH_SECONDS", "30"))
WAKE_GAP = float(os.environ.get("JARVIS_WATCH_WAKE_GAP_S", "2"))   # pause after a live HA change
EVENT_LEAD_MIN = int(os.environ.get("JARVIS_EVENT_LEAD_MIN", "15"))
STORAGE_FULL = float(os.environ.get("JARVIS_STORAGE_FULL", "98"))
ENABLED = os.environ.get("JARVIS_PROACTIVE", "1").strip() not in ("0", "false", "no", "off")
_APPL = {}            # name -> {"running": bool, "since": ts of the current low/high phase}
_CAL = {"at": 0.0, "events": []}


def once(key, text, **extra):
    """Publish `text` unless `key` was already announced today."""
    today = time.strftime("%Y-%m-%d")

    def mut(d):
        seen = {k: v for k, v in (d.get("notified") or {}).items() if v >= today}   # drop old days
        if seen.get(key) == today:
            d["notified"] = seen
            return False
        seen[key] = today
        d["notified"] = seen
        return True
    if commands.update_state(mut):
        events.publish("say", text, **extra)
        return True
    return False


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── appliances ───────────────────────────────────────────────
def appliances(states, now=None):
    """A machine counts as running above on_w for run_min minutes, and as done
    below off_w for off_min minutes after that (the drum pauses mid-cycle)."""
    now = now or time.time()
    by = {s["entity_id"]: s["state"] for s in states}
    for a in profile.ha("appliances", []) or []:
        name, watts = a.get("name") or "Gerät", _num(by.get(a.get("power", "")))
        if watts is None:
            continue
        st = _APPL.setdefault(name, {"running": False, "since": now, "high": None})
        on_w, off_w = float(a.get("on_w", 10)), float(a.get("off_w", 4))
        if not st["running"]:
            if watts >= on_w:
                st["high"] = st["high"] or now
                if now - st["high"] >= float(a.get("run_min", 3)) * 60:
                    st.update(running=True, since=now)
            else:
                st["high"] = None
        elif watts > off_w:
            st["since"] = now
        elif now - st["since"] >= float(a.get("off_min", 3)) * 60:
            st.update(running=False, high=None, since=now)
            article = a.get("article") or ("die " if name.lower().endswith("maschine") else "")
            events.publish("say", a.get("say") or f"Sir, {article}{name} ist fertig.", kind="appliance")


# ── calendar ─────────────────────────────────────────────────
def calendar(now=None):
    import icloud_calendar as cal
    if EVENT_LEAD_MIN <= 0 or not cal.configured():
        return
    now = now or dt.datetime.now()
    if time.time() - _CAL["at"] > 300:
        try:
            _CAL.update(events=cal.events(now.date(), 2), at=time.time())
        except Exception:  # noqa: BLE001
            _CAL["at"] = time.time()
            return
    for e in _CAL["events"]:
        if e.get("allday"):
            continue
        mins = (e["start"] - now).total_seconds() / 60
        key = f"{e.get('title')}:{e['start']:%Y%m%d%H%M}"
        drive = _drive(e) if e.get("location") and 0 < mins <= 240 else None
        if drive is not None:
            leave = (e["start"] - now).total_seconds() / 60 - drive - commute_buffer()
            if leave <= LEAVE_WARN_MIN:
                once(f"leave:{key}", f"Sir, Zeit loszufahren: {e.get('title', 'Ihr Termin')} um "
                                     f"{e['start'].hour}:{e['start'].minute:02d}, Fahrzeit etwa {drive} Minuten.",
                     kind="calendar")
            continue                       # with a drive the leave notice replaces the plain one
        if 0 < mins <= EVENT_LEAD_MIN:
            where = f" in {e['location'].split(',')[0]}" if e.get("location") else ""
            once(f"cal:{key}",
                 f"Sir, in {max(1, round(mins))} Minuten: {e.get('title', 'ein Termin')}{where}.", kind="calendar")


def departure_prep(states, now=None):
    """Half an hour before leaving for an appointment: what still runs at home, as a prepared proposal."""
    import departure
    if not _CAL["events"]:
        return None
    return departure.check(_CAL["events"], states, now or dt.datetime.now(), once)


LEAVE_WARN_MIN = int(os.environ.get("JARVIS_LEAVE_WARN_MIN", "5"))
_DRIVE = {}


def commute_buffer():
    import commute
    return commute.BUFFER_MIN


def _drive(e):
    """Driving minutes to an appointment, looked up once per place (commute.py)."""
    import commute
    place = e["location"]
    if place not in _DRIVE:
        try:
            dest = commute.geocode(place) or commute.geocode(place.split(",")[0])
            _DRIVE[place] = commute.drive_minutes(dest) if dest else None
        except Exception:  # noqa: BLE001 — map service down: plain notice instead, no retry every round
            _DRIVE[place] = None
    return _DRIVE[place]


# ── parcels ──────────────────────────────────────────────────
_PARCEL = {"at": 0.0}


def parcel_today():
    if time.time() - _PARCEL["at"] < 600 or not 7 <= dt.datetime.now().hour < 20:
        return
    _PARCEL["at"] = time.time()
    import parcels
    for s in parcels.current(parcels.shipments()):
        if s["status"] == "heute":
            once(f"parcel:{s['id'][:80]}", f"Sir, heute kommt ein Paket von {s['sender']}.", kind="parcel")


# ── storage + NAS ────────────────────────────────────────────
def storage(states, now=None):
    now = now or dt.datetime.now()
    if not 10 <= now.hour < 17:
        return
    by = {s["entity_id"]: s["state"] for s in states}
    soc = _num(by.get(profile.ha("storage_soc") or ""))
    solar = sum(_num(by.get(e)) or 0 for e in profile.ha("solar_power", []) or [])
    if soc is not None and soc >= STORAGE_FULL and solar >= 300:
        once("storage_full", f"Sir, der Speicher ist voll und die Sonne liefert {round(solar)} Watt. "
                             "Gute Zeit für Waschmaschine oder Trockner.", kind="energy")


def nas():
    import dashboard
    s = dashboard._cached("nas", 300, dashboard._nas)
    if not isinstance(s, dict) or s.get("error"):
        return
    if not s.get("ok"):
        once("nas_health", "Sir, das NAS meldet ein Problem mit einer Platte oder einem Volume. "
                           "Fragen Sie mich nach dem NAS, dann sage ich mehr.", kind="systems")
    elif (s.get("used_pct") or 0) >= 90:
        once("nas_full", f"Sir, das NAS ist zu {s['used_pct']} Prozent voll.", kind="systems")


# ── the phone's answer, the power budget ─────────────────────
def phone_answers(states):
    """A Ja/Nein tapped on the phone comes back through a Home Assistant helper (phone_actions.py)."""
    import phone_actions
    if states:
        phone_actions.poll(states)


def power_budget(states):
    """After 18 h, once a day: the month's projection against the budget (power_costs.py)."""
    import power_costs
    if states and dt.datetime.now().hour >= 18:
        text = power_costs.budget_notice({s["entity_id"]: s for s in states})
        if text:
            once("power_budget", text, kind="energy")


# ── 3D printer ───────────────────────────────────────────────
def printer_watch(states):
    import printer
    printer.watch(states, lambda key, text: once(key, text, kind="printer"))


def start():
    if not ENABLED:
        return
    import briefing

    import ha_events

    def loop():
        time.sleep(20)                 # let the HUD connect first
        last_full = 0.0
        while True:
            states = []
            try:
                states = briefing._ha_states()
            except Exception:  # noqa: BLE001 — HA restarting
                pass
            # A door, water or the printer wakes the state checks at once (ha_events.WAKE); the checks
            # that ask other services (calendar, NAS, parcels) keep their 30-s round.
            full = time.time() - last_full >= EVERY
            checks = [lambda: appliances(states), lambda: storage(states), lambda: printer_watch(states),
                      lambda: away_check.check(states), lambda: morning_echo.check(states),
                      lambda: phone_answers(states), lambda: proactive.tick(states)]
            if full:
                last_full = time.time()
                checks += [calendar, lambda: departure_prep(states), nas, parcel_today, lambda: power_budget(states),
                           token_budget.check, lambda: airing.check(states), lambda: airing.mould_check(states)]
            for check in checks:
                try:
                    check()
                except Exception as e:  # noqa: BLE001 — one dead source must not silence the rest
                    print(f"  watcher: {e}", flush=True)
            ha_events.WAKE.wait(max(0.0, EVERY - (time.time() - last_full)))
            ha_events.WAKE.clear()
            time.sleep(WAKE_GAP)       # a burst of changes (arriving home) becomes one round
    threading.Thread(target=loop, daemon=True).start()
