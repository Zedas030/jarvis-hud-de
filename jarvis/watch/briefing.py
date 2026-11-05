"""
Morning briefing + home-network watchdog.

The briefing is not a separate screen: on the first conversation of the day
(before BRIEFING_UNTIL o'clock) the turn's <kontext> gets weather, battery and
mail figures plus an instruction to open with a short spoken briefing.

The watchdog polls the FRITZ!Box device trackers in Home Assistant. A device
it has never seen gets its own star in the vault (linked to [[FRITZ!Box]])
and is mentioned at the start of the user's next turn.
"""
import json
import os
import re
import threading
import time
import urllib.request

import commands
import memory
import jarvis_profile as profile

HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123").strip().rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "").strip()
BRIEFING_UNTIL = int(os.environ.get("JARVIS_BRIEFING_UNTIL", "13"))     # hour, local time
WATCH_EVERY = int(os.environ.get("JARVIS_NET_WATCH_SECONDS", "300"))
# Weather location (JARVIS_WEATHER_LAT/LON in .env).
LAT, LON = os.environ.get("JARVIS_WEATHER_LAT", "50.11"), os.environ.get("JARVIS_WEATHER_LON", "8.68")
TZ_Q = profile.TIMEZONE.replace("/", "%2F")
DEVICE_DIR = memory.VAULT / "Erinnerungen" / "Netzwerk-Geräte"

WEATHER_CODES = {0: "klar", 1: "überwiegend klar", 2: "teils bewölkt", 3: "bedeckt", 45: "Nebel", 48: "Nebel",
                 51: "Niesel", 53: "Niesel", 55: "Niesel", 61: "leichter Regen", 63: "Regen", 65: "starker Regen",
                 71: "Schnee", 73: "Schnee", 75: "starker Schnee", 80: "Schauer", 81: "Schauer",
                 82: "heftige Schauer", 95: "Gewitter", 96: "Gewitter mit Hagel", 99: "Gewitter mit Hagel"}


def _get(url, headers=None, timeout=10):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def _ha_states():
    if not HA_TOKEN:
        return []
    return _get(HA_URL + "/api/states", {"Authorization": f"Bearer {HA_TOKEN}"}, timeout=15)


# ── morning briefing ─────────────────────────────

def _weather():
    d = _get("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&timezone=%s"
             "&forecast_days=1&daily=weather_code,temperature_2m_max,temperature_2m_min,"
             "precipitation_probability_max,precipitation_sum,wind_speed_10m_max,sunset" % (LAT, LON, TZ_Q))["daily"]
    return (f"Wetter heute{profile.place_suffix()}: {WEATHER_CODES.get(d['weather_code'][0], 'wechselhaft')}, "
            f"{d['temperature_2m_min'][0]:.0f} bis {d['temperature_2m_max'][0]:.0f} °C, "
            f"Regenrisiko {d['precipitation_probability_max'][0]} % ({d['precipitation_sum'][0]} mm), "
            f"Wind bis {d['wind_speed_10m_max'][0]:.0f} km/h, Sonnenuntergang {d['sunset'][0][-5:]}.")


def _energy(states):
    by = {s["entity_id"]: s["state"] for s in states}
    num = lambda v: v.replace(".", "", 1).isdigit()  # noqa: E731
    pattern = profile.ha("battery_soc_pattern")
    bats = [(int(m.group(1)), v) for k, v in by.items()
            if pattern and (m := re.fullmatch(pattern, k)) and num(v)]
    socs = [f"{float(v):.0f}" for _, v in sorted(bats)]
    total = by.get(profile.ha("storage_soc") or "", "")
    if not socs and not num(total):
        return ""
    total = f"{float(total):.0f}" if num(total) else "?"
    gen = sum(float(by[e]) for e in profile.ha("generation_today", []) if num(by.get(e, "")))
    solar = sum(float(by[e]) for e in profile.ha("solar_power", []) if num(by.get(e, "")))
    bat_txt = f" (Batterien: {' / '.join(socs)} %)" if socs else ""
    return (f"Speicher ({profile.ha('storage_name', 'Batteriespeicher')}): Ladestand gesamt {total} %{bat_txt}, "
            f"Solar gerade {solar:.0f} W, heute erzeugt {gen:.1f} kWh.")


def _coach():
    import energy_coach
    return energy_coach.morning_line()


BATTERY_LOW = int(os.environ.get("JARVIS_BATTERY_LOW", "25"))
# The phone's own battery is noise at breakfast.
BATTERY_IGNORE = {e.strip() for e in os.environ.get("JARVIS_BATTERY_IGNORE", ",".join(profile.ha("battery_ignore", []))).split(",")}


def _batteries(states):
    low = []
    for s in states:
        a = s.get("attributes", {})
        if a.get("device_class") != "battery" or a.get("unit_of_measurement") != "%" \
                or s["entity_id"] in BATTERY_IGNORE or s["entity_id"].endswith("soc"):  # storage, not AA cells
            continue
        try:
            level = float(s["state"])
        except ValueError:
            continue
        if level <= BATTERY_LOW:
            low.append(f"{a.get('friendly_name') or s['entity_id']} {level:.0f} %")
    return ("Batterien fast leer (bitte tauschen): " + ", ".join(sorted(low)) + ".") if low else ""


def _mail_count():
    import mailboxes
    if not mailboxes.configured():
        return ""
    results, errors = mailboxes.each(lambda acc, box: (acc["name"], len(box.search(None, "UNSEEN")[1][0].split())),
                                     timeout=15)
    if not results:
        return ""
    n = sum(c for _, c in results)
    split = f" ({', '.join(f'{a}: {c}' for a, c in results)})" if len(results) > 1 else ""
    return f"Postfach: {n} ungelesene Mails{split} (Details bei Bedarf mit mail_inbox)."

def _pi_tools():
    """Only findings (failed backup test, held update, open power finding); silence when all is well."""
    import pi_reports
    return pi_reports.briefing_line()


def morning_block():
    """Briefing data on the day's first turn, once. Empty string otherwise."""
    today = time.strftime("%Y-%m-%d")
    if time.localtime().tm_hour >= BRIEFING_UNTIL:
        return ""
    if not commands.update_state(lambda d: d.get("briefing_day") != today and not d.update(briefing_day=today)):
        return ""
    lines = []
    states = []
    try:
        states = _ha_states()
    except Exception:  # noqa: BLE001
        pass
    for part in (_weather, lambda: _energy(states), _coach, lambda: _batteries(states), _mail_count, _pi_tools):
        try:
            text = part()
        except Exception:  # noqa: BLE001 — a dead source must not block the conversation
            text = ""
        if text:
            lines.append("- " + text)
    lines.append("- Termine: siehe Kalender oben.")
    gentle = _gentle_line()
    return ("## Morgenbriefing — erstes Gespräch heute\n"
            + ("Beginne mit einem sehr kurzen, ruhigen Morgenbriefing (zwei Sätze: Termine und Wetter), ohne "
               "Pointe und ohne Vorschläge. " + gentle if gentle else
               "Beginne deine Antwort mit einem kurzen gesprochenen Morgenbriefing (drei bis vier Sätze): "
               "Termine, Wetter mit Einschätzung, ob es zum Golfen taugt, Ladestand des Speichers, ungelesene Mails, "
               "fast leere Batterien und Meldungen der Pi-Werkzeuge in einem Satz, falls welche gemeldet sind. ")
            + "Danach geh auf die Nachricht des Users ein.\n" + "\n".join(lines))


def _gentle_line():
    """The wellbeing hint for a bad night, '' otherwise (never a reason to fail the briefing)."""
    try:
        import wellbeing
        return wellbeing.hint()
    except Exception:  # noqa: BLE001
        return ""


# ── live stars ───────────────────────────────────

_LIVE = {"at": 0.0, "data": {}}
LIVE_TTL = 30


def live():
    """Per note id: online (bool|None) and pegel (0..1|None), from the HA entities
    named in the note's frontmatter. Cached so a dozen open tabs don't hammer HA."""
    if time.time() - _LIVE["at"] < LIVE_TTL:
        return _LIVE["data"]
    try:
        by = {s["entity_id"]: s["state"] for s in _ha_states()}
    except Exception:  # noqa: BLE001 — HA down: every star stays as it is
        by = {}
    out = {}
    if by:
        for n in memory.build_graph()["nodes"]:
            on, lvl = by.get(n.get("online") or ""), by.get(n.get("pegel") or "")
            if on is None and lvl is None:
                continue
            try:
                lvl = max(0.0, min(1.0, float(lvl) / 100))
            except (TypeError, ValueError):
                lvl = None
            out[n["id"]] = dict(online=None if on is None else on == "home", pegel=lvl)
    _LIVE.update(at=time.time(), data=out)
    return out


# ── network watchdog ─────────────────────────────

def _slug(name):
    return re.sub(r'[\\/:*?"<>|\[\]#]', "-", name).strip(" .-") or "Gerät"


def _new_device_note(name, ip, mac):
    DEVICE_DIR.mkdir(parents=True, exist_ok=True)
    title = f"Neu {_slug(name)}"
    path = DEVICE_DIR / f"{title}.md"
    if path.exists():
        return
    path.write_text(
        "---\ntype: geraet\nupdated: %s\nwichtigkeit: mittel\n---\n\n# %s\n\n"
        "Hängt an der [[FRITZ!Box]]. Vom Netzwerk-Wachhund am %s neu entdeckt — noch nicht zugeordnet.\n\n"
        "- Name im Netz: %s\n- IP: %s\n- MAC: %s\n"
        % (time.strftime("%Y-%m-%d"), title, time.strftime("%d.%m.%Y um %H:%M"), name, ip or "?", mac or "?"),
        encoding="utf-8")


def _check_network():
    trackers = [s for s in _ha_states() if s["entity_id"].startswith("device_tracker.")]
    if not trackers:
        return
    current = {s["entity_id"] for s in trackers}

    def merge(d):
        known = set(d.get("known_devices") or [])
        if not known:                      # first run: everything present counts as known
            d["known_devices"] = sorted(current)
            return []
        fresh = [s for s in trackers if s["entity_id"] not in known and s["state"] == "home"]
        d["known_devices"] = sorted(known | {s["entity_id"] for s in fresh})
        alerts = d.setdefault("net_alerts", [])
        for s in fresh:
            a = s.get("attributes", {})
            alerts.append(dict(name=a.get("friendly_name") or s["entity_id"], ip=a.get("ip", ""),
                               mac=a.get("mac", ""), at=time.strftime("%d.%m. %H:%M")))
        return fresh

    for s in commands.update_state(merge):
        a = s.get("attributes", {})
        name = a.get("friendly_name") or s["entity_id"].split(".", 1)[1]
        _new_device_note(name, a.get("ip", ""), a.get("mac", ""))
        import events
        events.publish("say", f"Sir, ein neues Gerät ist im WLAN: {name}. Kennen Sie das?", kind="network")


def network_block():
    """New devices since the last turn, once. Empty string otherwise."""
    alerts = commands.update_state(lambda d: d.pop("net_alerts", None) or [])
    if not alerts:
        return ""
    rows = "\n".join(f"- {a['at']}: {a['name']} (IP {a['ip'] or '?'}, MAC {a['mac'] or '?'})" for a in alerts)
    return ("## Netzwerk-Wachhund: neue Geräte im WLAN\n"
            "Erwähne das gleich zu Beginn kurz und frag, ob der User das Gerät kennt. "
            "Es hat schon einen Stern unter Netzwerk-Geräte; benenne die Notiz um, sobald der User sagt, was es ist.\n"
            + rows)


def start_watchdog():
    if not HA_TOKEN:
        return

    def loop():
        while True:
            try:
                _check_network()
            except Exception:  # noqa: BLE001 — Pi down or HA restarting: just try again later
                pass
            time.sleep(WATCH_EVERY)
    threading.Thread(target=loop, daemon=True).start()


# ── daily briefing, assembled in code (no web search, no stock news) ──
def briefing_facts():
    """Facts for the spoken daily briefing: weather + golf, appointments, solar and
    storage, low batteries, unread mail. Collected here, so the LLM only has to
    phrase it — one small call without tools instead of a research turn."""
    import dashboard
    import icloud_calendar
    lines = []
    states = []
    try:
        states = _ha_states()
    except Exception:  # noqa: BLE001
        pass
    for part in (_weather, lambda: _energy(states), _coach, lambda: _batteries(states), _mail_count, _pi_tools):
        try:
            text = part()
        except Exception:  # noqa: BLE001 — a dead source must not block the briefing
            text = ""
        if text:
            lines.append("- " + text)
    try:
        w = dashboard._weather()
        lines.append(f"- Golf-Einschätzung (berücksichtigt Tageslicht und Arbeitszeit): {w['golf']} "
                     f"({w['golf_day']}).")
    except Exception:  # noqa: BLE001
        pass
    try:
        agenda = icloud_calendar.agenda_block()
        if agenda:
            lines.append("- " + agenda.split("\n", 1)[-1].replace("\n", "; "))
    except Exception:  # noqa: BLE001
        pass
    gentle = _gentle_line()
    if gentle:
        lines.append("- Hinweis: " + gentle + " Nur zwei Sätze: Termine und Wetter.")
    return "\n".join(lines)
