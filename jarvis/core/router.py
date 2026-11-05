"""
Intent router — decides per message, before any LLM is involved:

  local   simple commands answered by code alone (0 tokens): routines, timers,
          alarms and reminders, shopping/to-do lists, switch devices, music keys,
          time/date, weather/golf, sunrise/sunset, solar, NAS, Pi.
  llm     everything else, with the smallest fitting setup: which tool groups
          the request needs and whether Haiku is enough or Sonnet is needed.

Rules are plain keyword patterns, so they cost nothing and are easy to tune.
When a local handler is unsure (ambiguous device, protected socket, data not
reachable) it returns None and the request goes to the LLM instead.
"""
import datetime as dt
import json
import os
import random
import re
import threading
import time
import urllib.request

MODEL_SIMPLE = os.environ.get("JARVIS_MODEL_SIMPLE", "haiku").strip()
MODEL_SMART = os.environ.get("JARVIS_MODEL_SMART", "sonnet").strip() or os.environ.get("JARVIS_MODEL", "sonnet")
LOCAL_ENABLED = os.environ.get("JARVIS_LOCAL_ROUTER", "1").strip() not in ("0", "false", "no", "off")

WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
          "Oktober", "November", "Dezember")


def _norm(text):
    return " " + re.sub(r"[^\wäöüß%]+", " ", (text or "").lower()).strip() + " "


def _has(n, *words):
    return any(re.search(r"\b" + w, n) for w in words)


def _sir():
    return random.choice(("Sir", "Sir", "Sir", ""))


def _say(*options):
    s = random.choice(options).strip()
    return s.replace(", .", ".").replace(" ,", ",")


# ── tool groups + model choice for the LLM path ──────────────
GROUP_RULES = [
    ("home", r"\b(licht|lampe|led\b|steckdose|schalt|kühlschrank|spülmaschine|waschmaschine|drucker|3d|filament|ams\b|bambu|zelt|bett\b|heizung|thermostat|temperatur im|grad im|luftreiniger|"
             r"ventilator|szene|wohnzimmer|schlafzimmer|gaming|küche|boiler|smart ?home|home ?assistant|"
             r"solar|akku|speicher|strom|watt|überschuss|energie-?coach|sensor|luft|feucht|echo|fire ?tv|fernseher|tv\b)"),
    ("mail", r"\b(mail|e-mail|postfach|posteingang|newsletter|nachricht von|geschrieben|gelesen)"),
    ("calendar", r"\b(termin|kalender|eintragen|trag\w* .*ein|erinner|verabredung|meeting|wann (ist|habe))"),
    # "Was steht heute noch an?" got memory only, and Jarvis said he could not see the calendar
    ("calendar", r"\b(was steht .{0,25}\ban\b|(hab|habe) ich .{0,25}\b(vor|geplant|frei|zeit)\b|vorhaben|"
                 r"tagesplan|wochenplan|was ist (heute|morgen|am \w+) los)"),
    # bookings in the inbox that belong in the calendar: both tool groups at once
    ("mail", r"\b(buchung|buchungen|gebucht|reservier|bestätigung|tee ?time|startzeit|startzeiten)"),
    ("mail", r"\b(paket\w*|päckchen|lieferung\w*|sendung\w*|versand|zustell\w*|tracking|bestellt|bestellung)"),
    ("calendar", r"\b(buchung|buchungen|gebucht|reservier|bestätigung|tee ?time|startzeit|startzeiten)"),
    ("calendar", r"\b(rückgängig|wieder raus|wieder (heraus|rausnehmen|löschen)|termine? .{0,20}(lösch|entfern))"),
    ("systems", r"\b(pi\b|raspi|raspberry|nas\b|synology|netzwerk|wlan|router|fritz|gerät|server|pi-hole|pihole|ip\b|"
                r"internet|dns|backup|grafikkarte|gpu|vram|cpu|prozessor|arbeitsspeicher|ram\b|festplatte|"
                r"speicherplatz|laufwerk|ausgefallen|ausfall|prognose|wann voll|voll sein)"),
    ("homelab", r"\b(pi-?hole|werbung|blockier|grafana|prometheus|homelab|internet|geschwindigkeit|bandbreite|mbit|"
                r"download|upload|datenvolumen|daten (heute|verbraucht)|wlan|mesh|auslastung|cpu|verlauf|spitze|"
                r"am höchsten|am meisten)"),
    ("homelab", r"\b(lagebericht|uptime|kuma|sicherheitszentrale|sicherheitsbefund|sicherheitslage|nulleinspeisung|"
                r"energie-?kreislauf)"),
    ("homelab", r"\b(ausfällt|ausfallen|abstürzt|abhängig|abhängigkeit|hängt .{0,30}\bab\b|single point|spof|"
                r"blast.?radius|was fällt .{0,20}aus)"),
    ("homelab", r"\b(restore[ -]?probe|wiederherstell|backup[ -]?test|update[ -]?schleuse|container[ -]?update|"
                r"stromfresser[ -]?detektiv|detektiv|auffällig|platzberater|zigbee|homelab[ -]?werkzeug)"),
    ("health", r"\b(schlaf|geschlafen|schritte|puls|garmin|body ?battery|stress|fitness|training|lauf|"
               r"gegessen|abendessen|alkohol|bier\b|wein\b|rückblick|wochenrückblick)"),
    ("music", r"\b(musik|spotify|song|lied|playlist|album|abspielen)"),
    ("weather", r"\b(wetter|vorhersage|sonne|sonnig|sonnen|uv\b|uv-|regen|regnet|schirm|schnee|schneit|gewitter|"
                r"sturm|wind\b|windig|nebel|frost|glatt|temperatur(?! im)|grad draußen|wie warm|wie kalt|"
                r"wird es hell|hell wird|wird es dunkel|dunkel wird|wie lange (ist|bleibt) es .{0,25}\bhell|tageslicht)"),
    ("web", r"\b(such|recherch|google|ergoogel|internet|online|website|webseite|seite|öffne|news|nachrichten|aktuell|"
            r"finde? .{0,30}heraus|herausfinden|schau (mal )?nach|guck (mal )?nach|nachschlagen|nachschauen|"
            r"preis|kostet|wer ist|wer war|was ist|wie viel kostet|bundesliga|spiel|ergebnis|kurs|aktie)"),
    ("pc", r"\b(programm|starte|befehl|powershell|kommandozeile|datei|ordner|installier|bildschirm|screenshot|"
           r"fenster|desktop|computer|rechner)"),
    ("timers", r"\b(timer|wecker|weck|erinner|schlummer|alarm|countdown)"),
    ("lists", r"\b(einkauf|to-?do|aufgabenliste|erledigungsliste)"),
    ("routines", r"\b(routine|wenn ich .{1,40} sage|sobald ich .{1,40} sage|immer wenn ich)"),
    ("proposals", r"\b(vorschl|regel|übernimm|übernehmen|lernprotokoll|gelernt|formulierung|entwurf|entwürfe)"),
    ("memory_admin", r"\b(vergiss|vergessen|lösch|warum hast du|weshalb hast du|was weißt du über mich|widerspr|doppelt|"
                     r"prüf (mein|dein) gedächtnis|aufräumen|"
                     r"was hast du (über mich )?gespeichert|gespeichert|korrigier|stimmt nicht mehr|exportier|backup)"),
    ("windows", r"\b(fenster|nebeneinander|anordnen|arbeitsbereich|monitor \d|linke seite|rechte seite)"),
    ("files", r"\b(datei|dateien|dokument|dokumente|ordner|word-?datei|textdatei|downloads|anleitung|"
              r"handbuch|bedienungsanleitung|in meinen (unterlagen|notizen|dokumenten)|pdf)"),
    ("clipboard", r"\b(zwischenablage|kopiert|clipboard)"),
    ("screen", r"\b(bildschirm|screen|was siehst du|fehlermeldung|dieses fenster|auf meinem monitor|was steht da)"),
    ("scripts", r"\b(skript|script)"),
    ("mail", r"\b(entwurf|mail an|schreib .{0,40}(mail|nachricht)|antwort an)"),
    ("dayprep", r"\b(was muss ich (für|fuer) .{0,20}wissen|bereite? .{0,20}(vor|termin)|vorbereitung|"
                r"wie sieht (mein|der) (tag|morgen)|was steht (morgen|übermorgen|heute) an)"),
    ("research", r"\b(recherchier|gründlich|quellen|fakten ?check|sag bescheid,? wenn|beobacht|"
                 r"halt mich auf dem laufenden|was gibt es neues)"),
    ("plans", r"\b(plan(e|en)? .{0,30}(über|bis|für die)|über (mehrere|zwei|drei|\d+) (tage|wochen)|"
              r"mein(en)? plan|schritt .{0,20}erledigt|abhaken|projektplan)"),
    ("phone", r"\b(aufs handy|ans handy|aufs telefon|push(-nachricht)?|schick mir .{0,30}handy)"),
    ("tasks", r"\b(prüf\w*|check|diagnose|bericht|prüfung)"),
    ("updates", r"\b(update|upgrade|aktualisier|firmware|neue version|auf dem neuesten stand|veraltet)"),
    ("audit", r"\b(was hast du (heute|gestern)? ?(alles )?(gemacht|getan|geschaltet)|protokoll deiner|audit|"
              r"was hast du ausgeführt)"),
    # looking back: the conversation log, Garmin days, notices and incidents joined by date
    ("episodes", r"\b(letzte woche|vorletzte woche|neulich|kürzlich|letztens|weißt du noch|damals|vor \w+ (tagen|wochen)|"
                 r"als ich .{0,40}\b(war|hatte|lag)\b|worüber haben wir|was (habe|hab) ich dich .{0,30}gefragt|"
                 r"wie war .{0,30}(schlaf|verbrauch|stress|puls|nacht|tag) .{0,30}\b(als|am|letzte|vor)\b)"),
    ("paperless", r"\b(paperless|archiv|unterlagen|rechnung|vertrag|versicherung|police|kündigung|bescheid|steuer|"
                  r"gescannt|was zahle ich für|wann läuft .{0,30}(vertrag|versicherung|abo)|vertragsnummer|kundennummer)"),
    ("unifi", r"\b(unifi|wer ist .{0,20}(im|in meinem) (wlan|netz)|welche geräte .{0,20}(im|am) (wlan|netz)|access ?point|"
              r"wlan[- ]?clients?|empfang .{0,20}(wlan|gerät))"),
    ("media", r"\b(jellyfin|plex|mediathek|serie|film|folge|staffel|weiter ?(schauen|gucken|sehen)|zuletzt (geschaut|gesehen|geguckt)|"
              r"bibliothek|was gibt es neues (zum|an) (schauen|filmen|serien))"),
]
# A short yes/no while a proposal is open: bring the proposal tools along.
PROPOSAL_REPLY = re.compile(r"^\W*(ja|jo|klar|gerne?|ok(ay)?|mach( das)?|einverstanden|passt|nein|nee|lieber nicht|"
                            r"nicht nötig|später)\b", re.I)
SMART_HINTS = re.compile(
    r"\b(erklär|warum|wieso|analys|vergleich|plan|schreib|formulier|zusammenfass|recherch|überleg|hilf mir|"
    r"code|programm|berechne|strategie|optimier|bewert|was meinst du|ausführlich|genau)", re.I)


def llm_route(message, ha_full=False):
    n = _norm(message)
    groups = ["memory"]                     # remembering works in every turn
    for name, pat in GROUP_RULES:
        if re.search(pat, n):
            groups.append(name)
    if ha_full:
        groups.append("ha_full")
    groups = list(dict.fromkeys(groups))       # one group can match several patterns
    if "proposals" not in groups and PROPOSAL_REPLY.search(n):
        import proposals
        if proposals.pending():
            groups.append("proposals")
    smart = (bool(SMART_HINTS.search(message)) or len(message) > 220
             or any(g in groups for g in ("web", "pc", "ha_full")) or len(groups) > 3)
    return {"kind": "llm", "groups": groups, "model": MODEL_SMART if smart else MODEL_SIMPLE,
            "reason": "smart" if smart else "simple"}


# ── local handlers ───────────────────────────────────────────
def _dash(key, ttl, fn):
    """Dashboard data: the HUD keeps this cache warm; if it is still empty, fetch now."""
    import dashboard
    val = dashboard._cached(key, ttl, fn)
    return val if val is not None else fn()


def _time(n):
    if not _has(n, "wie spät", "uhrzeit", "wieviel uhr", "wie viel uhr", "welche zeit"):
        return None
    now = dt.datetime.now()
    return _say(f"Es ist {now.hour} Uhr {now.minute:02d}, {_sir()}.", f"{now:%H:%M} Uhr, {_sir()}.")


def _date(n):
    if not (_has(n, "welcher tag", "welches datum", "der wievielte", "welchen tag", "datum") and not _has(n, "termin")):
        return None
    d = dt.date.today()
    return f"Heute ist {WEEKDAYS[d.weekday()]}, der {d.day}. {MONTHS[d.month - 1]}, {_sir()}."


_WX = {}


def _forecast():
    if _WX.get("at", 0) > time.time() - 900:
        return _WX["d"]
    import briefing
    d = briefing._get("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&timezone=%s"
                      "&forecast_days=3&current=temperature_2m,weather_code"
                      "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
                      "precipitation_probability_max,wind_speed_10m_max,sunrise,sunset"
                      % (briefing.LAT, briefing.LON, briefing.TZ_Q))
    _WX.update(at=time.time(), d=d)
    return d


def _sun(n):
    rise = _has(n, "sonnenaufgang", "sonne auf", "wird es hell", "hell wird")
    sets = _has(n, "sonnenuntergang", "sonne unter", "wird es dunkel", "dunkel wird")
    both = _has(n, "sonnenzeiten", r"wie lange (ist|bleibt) es .{0,25}\bhell", "tageslicht")
    if not (rise or sets or both) or _has(n, "woche", "wochenende", "nächste") or _has(n, "golf"):
        return None
    try:
        day = _forecast()["daily"]
    except Exception:  # noqa: BLE001
        return None
    i = 2 if _has(n, "übermorgen") else 1 if _has(n, "morgen") and not _has(n, "heute morgen") else 0
    up, down = day["sunrise"][i][-5:], day["sunset"][i][-5:]
    when = ("heute", "morgen", "übermorgen")[i]
    if both or (rise and sets):
        a, b = (dt.datetime.fromisoformat(day[k][i]) for k in ("sunrise", "sunset"))
        h, m = divmod(round((b - a).total_seconds() / 60), 60)
        return f"Die Sonne geht {when} um {up} auf und um {down} unter, {h} Stunden {m} Minuten Tageslicht, {_sir()}."
    if rise:
        return _say(f"Sonnenaufgang {when} um {up} Uhr, {_sir()}.", f"Die Sonne geht {when} um {up} auf, {_sir()}.")
    return _say(f"Sonnenuntergang {when} um {down} Uhr, {_sir()}.", f"Die Sonne geht {when} um {down} unter, {_sir()}.")


def _weather(n):
    # Golf only as a weather question ("Kann ich morgen golfen?"); "Ich habe morgen eine Trainerstunde
    # zum Golfen, was empfiehlst du?" or a question about new clubs is a conversation for the LLM.
    golf = _has(n, "golf") and len(n.split()) <= 14 and not _has(n, "trainer", "stunde", "schläger", "empf",
                                                                   "hältst", "tipp", "technik", "schwung", "handicap")
    if not (_has(n, "wetter", "regnet", "regen", "temperatur draußen", "wie warm", "wie kalt", "sonne scheint")
            or (golf and (_has(n, "kann ich", "taugt", "wetter", "golfwetter", "lohnt", "passt")
                          or (_has(n, "geht", "spielen") and len(n.split()) <= 7)
                          or (_has(n, "heute", "morgen") and len(n.split()) <= 4)))):
        return None
    if _has(n, "woche", "wochenende", "übermorgen", "nächste"):
        return None                              # longer outlook: let the LLM do it
    import briefing
    import dashboard
    try:
        d = _forecast()
    except Exception:  # noqa: BLE001
        return None
    i = 1 if _has(n, "morgen") and not _has(n, "heute morgen") else 0
    day = d["daily"]
    sky = briefing.WEATHER_CODES.get(day["weather_code"][i], "wechselhaft")
    tmax, tmin = round(day["temperature_2m_max"][i]), round(day["temperature_2m_min"][i])
    rain, wind = day["precipitation_probability_max"][i] or 0, round(day["wind_speed_10m_max"][i] or 0)
    when = "Morgen" if i else "Heute"
    now = ""
    if i == 0 and d.get("current", {}).get("temperature_2m") is not None:
        now = f" Gerade sind es {round(d['current']['temperature_2m'])} Grad."
    text = (f"{when} {sky}, {tmin} bis {tmax} Grad, Regenrisiko {rain} Prozent, Wind bis {wind} km/h.{now}")
    if golf:
        try:
            w = _dash("weather", 1800, dashboard._weather)
            verdict = {"ja": "passt es", "mit Jacke": "geht es, aber mit Jacke", "nein": "lieber nicht"}
            v = verdict.get(w.get("golf"), "schwer zu sagen")
            gday = w.get("golf_day", "heute")
            if gday == "heute":
                text += f" Golf heute: {v}, {_sir()}."
            elif i == 0:
                text += f" Für eine Runde heute reicht das Tageslicht nicht mehr. {gday.capitalize()} {v}, {_sir()}."
            else:
                text += f" Golf {gday}: {v}, {_sir()}."
        except Exception:  # noqa: BLE001
            pass
    elif rain >= 60:
        text += f" Schirm empfohlen, {_sir()}."
    return text.replace(" ,", ",").replace(", .", ".")


def _solar(n):
    if not (_has(n, "solar", "photovoltaik", "pv\\b", "balkonkraftwerk", "akku", "speicher", "batterie")
            # thresholds and "when" are questions about the setup, not the current reading
            and not _has(n, "warum", "erklär", "verlauf", "gestern", "woche", "monat", "automati", "wann", "ab wie",
                         "schutz", "grenze", "schwelle", "aufhör", "einstell", "falsch", "doppel")):
        return None
    import dashboard
    try:
        h = _dash("house", 30, dashboard._house)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(h, dict) or h.get("error"):
        return None
    parts = [f"Die Solaranlage liefert gerade {h.get('solar_w', 0)} Watt"]
    if h.get("soc") is not None:
        parts.append(f"der Akku steht bei {round(h['soc'])} Prozent")
    if h.get("gen_kwh") is not None:
        parts.append(f"heute erzeugt: {str(h['gen_kwh']).replace('.', ',')} Kilowattstunden")
    return ", ".join(parts) + f", {_sir()}."


# Updates are a question for Claude with the update tools, never the quick status line
# ("Mach ein Update vom NAS" used to get "Das NAS: alles in Ordnung …").
_UPDATE_WORDS = ("update", "upgrade", "aktualis", "neuesten stand", "version", "firmware", "dsm", "software",
                 "installier", "pakete?\\b", "apps?\\b")


def _nas(n):
    if not (_has(n, "nas\\b", "synology") and not _has(n, "datei", "ordner", "freigabe", "backup", "warum", *_UPDATE_WORDS)):
        return None
    import dashboard
    try:
        s = _dash("nas", 300, dashboard._nas)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(s, dict) or s.get("error"):
        return None
    ok = "alles in Ordnung" if s.get("ok") else "mindestens eine Platte oder ein Volume meldet ein Problem"
    used = f"{s.get('used_pct')} Prozent belegt ({str(s.get('used_tb')).replace('.', ',')} von " \
           f"{str(s.get('total_tb')).replace('.', ',')} Terabyte)" if s.get("used_pct") is not None else ""
    temp = f", {round(s['temp'])} Grad" if s.get("temp") is not None else ""
    return f"Das NAS: {ok}{temp}, {used}, {_sir()}.".replace(", ,", ",")


def _pi(n):
    if not (_has(n, "pi\\b", "raspi", "raspberry") and _has(n, "status", "wie geht", "läuft", "alles ok", "temperatur",
                                                             "zustand", "fit", "gesund")
            and not _has(n, *_UPDATE_WORDS)):
        return None
    import dashboard
    try:
        s = _dash("pi", 120, dashboard._pi)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(s, dict) or s.get("error"):
        return None
    cont = f", {s.get('up')} von {s.get('containers')} Containern laufen" if s.get("containers") else ""
    de = {"week": ("Woche", "Wochen"), "day": ("Tag", "Tagen"), "hour": ("Stunde", "Stunden")}
    parts = [f"{num} {de[unit][0] if num == '1' else de[unit][1]}"
             for num, unit in re.findall(r"(\d+) (week|day|hour)", s.get("uptime") or "")[:2]]
    since = " und ".join(parts) or "einer Weile"
    disk = f", Speicher zu {round(s['disk'])} Prozent voll" if s.get("disk") is not None else ""
    return f"Der Pi läuft seit {since}, {round(s.get('temp') or 0)} Grad{disk}{cont}, {_sir()}."


MUSIC = [
    (r"\b(pause|pausier|stopp|stop|anhalten|halt die musik|musik aus)", "pause", "Pausiert"),
    (r"\b(weiter ?spielen|fortsetzen|musik an|spiel musik|play)\b", "play", "Läuft"),
    (r"\b(nächste[nrs]?|skip|überspring)", "next", "Nächster Titel"),
    (r"\b(vorherige[nrs]?|zurück|letzte[nrs]? (lied|song|titel))", "previous", "Vorheriger Titel"),
    (r"\b(lauter)", "volume_up", "Lauter"),
    (r"\b(leiser)", "volume_down", "Leiser"),
]


def _music_target(n):
    """The room the music should go to, when a speaker room is named: "Musik vom Schlafzimmer ins Wohnzimmer",
    "mach die Musik aufm Schlafzimmer im Wohnzimmer an". The room after "vom / aus dem / aufm / auf dem" is
    where it plays now, so the other one is the target; with one room named, that is the target."""
    import speakers
    try:
        rooms = speakers.rooms()
    except Exception:  # noqa: BLE001
        return None
    found = sorted((m.start(), r) for r in rooms for m in re.finditer(r"\b" + re.escape(r.lower()) + r"\b", n))
    if not found:
        return None
    targets = [r for pos, r in found
               if not re.search(r"\b(vom|von|aus|aufm|auf dem|auf der|von dem|von der|aus dem|aus der) $", n[:pos])]
    return targets[0] if targets else found[-1][1]


def _music(n):
    if not _has(n, "musik", "spotify", "song", "lied", "titel", "track") and not re.search(
            r"\b(lauter|leiser)\b", n) or _has(n, "jarvis.*(lauter|leiser)", "stimme", "sprich"):
        return None
    if _has(n, "such", "spiel .* von", "spiele .* von", "playlist", "album"):
        return None                               # searching needs the LLM to pick the query
    target = _music_target(n)
    if target:
        import jarvis_mcp
        try:
            text = jarvis_mcp._music({"action": "transfer", "room": target})
        except Exception:  # noqa: BLE001
            return None
        return f"{text[:-1]}, {_sir()}." if _sir() and text.endswith(".") else text
    for pat, action, text in MUSIC:
        if re.search(pat, n):
            import jarvis_mcp
            try:
                jarvis_mcp._music({"action": action})
            except Exception:  # noqa: BLE001
                return None
            return f"{text}, {_sir()}." if _sir() else f"{text}."
    return None


# Devices: friendly names from Home Assistant. Config switches (child lock,
# status LEDs of sockets, auto-update, FRITZ!Box internet access) are never
# targets; sockets that keep things alive are never switched off without
# Claude (and his confirmation rules). A *light* called "LED Wohnzimmer" is a
# real lamp, though — only switches named "... LED" are indicator lights.
_DEV = {"at": 0.0, "list": []}
_CONFIG_WORDS = re.compile(r"kindersicherung|automatisch|display|update|abschaltung|shuffle|wiederholen|"
                           r"nicht stören|stummschalt|internetzugang|internet access", re.I)
_INDICATOR_LED = re.compile(r"\bled\b", re.I)
PROTECTED_OFF = re.compile(r"router|\bpc\b|raspi|\bpi\b|kühlschrank|smart hub|nas|fritz|wi-?fi|wlan", re.I)
_DOMAIN_WORDS = {"licht": "light", "lampe": "light", "lampen": "light", "led": "light", "steckdose": "switch",
                 "ventilator": "fan", "szene": "scene"}
_FILLER = {"der", "die", "das", "den", "dem", "im", "in", "am", "an", "aus", "ein", "bitte", "mal", "mach", "mache",
           "schalte", "schalt", "jarvis", "und", "auf", "zu", "von", "sir", "kannst", "du", "die", "alle", "einschalten",
           "ausschalten", "anmachen", "ausmachen", "prozent", "%", "licht", "lampe", "steckdose", "lichter", "lampen"}


def _aliases():
    """Extra words per entity from config/home.json → "device_aliases": the lamp HA calls "LED" is also
    "Wohnzimmer", "Licht" or "Steckdose" to the user. Read with every device refresh, no restart needed."""
    import jarvis_profile
    return jarvis_profile._load_home().get("device_aliases") or {}


def _devices():
    if _DEV["at"] > time.time() - 300:
        return _DEV["list"]
    import briefing
    aliases = _aliases()
    out = []
    for s in briefing._ha_states():
        eid = s["entity_id"]
        dom = eid.split(".")[0]
        name = (s.get("attributes", {}).get("friendly_name") or eid).strip()
        if dom not in ("light", "switch", "fan", "scene") or _CONFIG_WORDS.search(name):
            continue
        if dom == "switch" and (_INDICATOR_LED.search(name) or eid.endswith("_led")):
            continue                               # the status LED of a socket, not a lamp
        if s.get("state") == "unavailable":
            continue                               # a dead lamp must not tie with the live one
        base = set(_norm(name).split())
        alias = set(_norm(" ".join(aliases.get(eid) or [])).split())
        out.append({"eid": eid, "dom": dom, "name": name, "base": base, "words": base | alias, "alias": alias})
    _DEV.update(at=time.time(), list=out)
    return out


QUESTION = re.compile(r"^\s*(ist|sind|läuft|laufen|brennt|brennen|hat|haben|wie|was|welche\w*|warum|wieso|"
                      r"steht|stehen|status|zustand|gibt es|weißt du)\b")


def _match_devices(n, hint=None, devs=None):
    said = set(n.split())
    words = {w for w in said if w not in _FILLER and len(w) > 1}
    devs = _devices() if devs is None else devs
    if not any(d["words"] & words for d in devs):
        words = said                               # only generic words ("Licht aus"): an alias may claim them
        devs = [d for d in devs if d["alias"] & said]
    best, score = [], 0
    for d in devs:
        if hint and d["dom"] != hint and not (hint == "switch" and d["dom"] == "fan") \
                and not d["alias"] & said & _DOMAIN_WORDS.keys():
            continue                               # "Steckdose" for a lamp the user calls a socket is fine
        sc = len(d["words"] & words)
        if not sc:
            continue
        if sc > score:
            best, score = [d], sc
        elif sc == score:
            best.append(d)
    if len(best) > 1:
        exact = [d for d in best if d["base"] <= words]   # "TV" beats "TV Schlafzimmer" for "schalt das TV an"
        if len(exact) == 1:
            best = exact
    return best


def _home_state(n):
    """'Ist der Luftreiniger an?' — answered from Home Assistant, nothing is switched."""
    if not (QUESTION.search(n) and re.search(r"\b(an|aus|ein|eingeschaltet|ausgeschaltet|läuft|laufen)\s*$", n.strip())):
        return None
    hint = next((d for w, d in _DOMAIN_WORDS.items() if re.search(r"\b" + w, n)), None)
    try:
        best = _match_devices(n, hint)
        if not best or len(best) > 3:
            return None
        import briefing
        states = {s["entity_id"]: s["state"] for s in briefing._ha_states()}
    except Exception:  # noqa: BLE001
        return None
    word = {"on": "an", "off": "aus", "playing": "an", "idle": "aus"}
    parts = [f"{d['name']} ist {word.get(states.get(d['eid']), states.get(d['eid'], 'unbekannt'))}" for d in best]
    return " und ".join(parts) + f", {_sir()}."


def _climate(n):
    """Handle clear room-thermostat commands locally so the read-only HA MCP is never used for them."""
    if QUESTION.search(n) or not re.search(r"heiz\w*|thermostat|temperatur|wärme|grad", n):
        return None
    raw = _raw()
    temperature = re.search(r"\bauf\s+(\d{1,2}(?:[.,]\d)?)\b|(\d{1,2}(?:[.,]\d)?)\s*(?:°\s*C|grad)\b", raw, re.I)
    off = re.search(r"\b(aus|ausschalten|ausmachen|abschalten)\b", n)
    on = re.search(r"\b(an|einschalten|anmachen|anschalten)\b", n)
    if sum((bool(off), bool(on), bool(temperature))) != 1:
        return None
    try:
        import jarvis_profile
        groups = jarvis_profile._load_home().get("climate_groups", {})
    except Exception:  # noqa: BLE001
        return None
    rooms = []
    for room, entities in groups.items():
        if not isinstance(entities, list):
            continue
        alias = _norm(str(room)).strip()
        pattern = r"(?<!\w)" + re.escape(alias) + r"(?:heizung|thermostat(?:e)?)?(?!\w)"
        if alias and re.search(pattern, n):
            rooms.append((str(room), [str(eid) for eid in entities if str(eid).startswith("climate.")]))
    if len(rooms) != 1 or not rooms[0][1]:
        return None
    name, entities = rooms[0]
    if temperature:
        raw_value = temperature.group(1) or temperature.group(2)
        data = {"temperature": float(raw_value.replace(",", "."))}
        service = "set_temperature"
    else:
        data = {"hvac_mode": "off" if off else "heat"}
        service = "set_hvac_mode"
    import jarvis_mcp
    results = []
    for eid in entities:
        result = jarvis_mcp._home("home_control", {"entity_id": eid, "service": service, "data": data})
        if not result or not result.startswith("Erledigt"):
            done = "; ".join(results)
            prefix = f"{done}. " if done else ""
            return f"{prefix}{name}: {result or 'Keine Rückmeldung von Home Assistant.'}"
        results.append(result)
    return " ".join(results)


def _home(n):
    if QUESTION.search(n):
        return None                                # a question never switches anything
    on = re.search(r"\b(an|ein|einschalten|anmachen|anschalten|aktivier\w*)\s*$|\b(schalt\w* .* an|mach\w* .* an)\b", n.strip())
    off = re.search(r"\b(aus|ausschalten|ausmachen|abschalten|deaktivier\w*)\s*$|\b(schalt\w* .* aus|mach\w* .* aus)\b", n.strip())
    pct = re.search(r"\bauf (\d{1,3}) ?(%|prozent)", n)
    scene = re.search(r"\b(szene|stimmung)\s+(\w[\w ]*)", n)
    if not (on or off or pct or scene):
        return None
    if _has(n, "wenn", "automati", "jeden", "täglich", "um \\d", "timer", "später", "warum", "ob ",
            "in \\w+ (sekunden?|minuten?|stunden?)\\b"):
        return None                                # schedules/conditions: scheduler or LLM
    if _has(n, "musik\\b", "spotify\\b") and _music_target(n):
        return None                                # "mach die Musik im Wohnzimmer an": the music, not a switch
    try:
        devs = _devices()
    except Exception:  # noqa: BLE001
        return None
    hint = next((d for w, d in _DOMAIN_WORDS.items() if re.search(r"\b" + w, n)), None)
    best = _match_devices(n, hint, devs)
    import learning
    learned = learning.alias(n)                    # a wording Claude once resolved for us
    if learned:
        by_eid = {d["eid"]: d for d in devs}
        best = [by_eid.get(e) or {"eid": e, "dom": e.split(".")[0], "name": e.split(".", 1)[1].replace("_", " "),
                                  "words": set()} for e in learned]
        hint = hint or best[0]["dom"]
    lights = [d for d in devs if d["dom"] == "light"]
    if not best and hint == "light" and re.search(r"\balle\b", n) and lights and not pct:
        best = lights                              # "mach alle Lichter aus"
    elif not best or len(best) > 4:
        return None
    if len(best) > 1 and not hint and not all(d["dom"] == "light" for d in best):
        # "mach Wohnzimmer an" with a lamp and a socket: an answer the user gave once is kept
        # (learning.choice); otherwise ask back locally and remember the answer — no model needed
        chosen = learning.choice(n)
        picked = [d for d in best if d["eid"] in chosen]
        if not picked:
            return _ask_choice(n, best)
        best = picked
    service = "turn_on" if (on or pct or scene) and not off else "turn_off"
    # Only what keeps things alive is protected — a lamp called "LED PC" is just a lamp.
    if service == "turn_off" and any(d["dom"] != "light" and (PROTECTED_OFF.search(d["name"])
                                                               or PROTECTED_OFF.search(d["eid"])) for d in best):
        return None
    import jarvis_mcp
    done = []
    for d in best:
        data = {"brightness_pct": max(1, min(100, int(pct.group(1))))} if pct and d["dom"] == "light" else None
        svc = "turn_on" if d["dom"] == "scene" else service
        res = jarvis_mcp._home("home_control", {"entity_id": d["eid"], "service": svc, "data": data or {}})
        if res.startswith("Gesendet"):              # sent, but the state did not follow: say so
            return f"{d['name']} reagiert nicht, {_sir()}: der Befehl ging raus, aber der Zustand ist unverändert."
        if not res.startswith("Erledigt"):
            return None
        done.append(d["name"])
    _SWITCHED.update(devs=best, service=service, at=time.time())
    what = " und ".join(done)
    state = f"auf {pct.group(1)} Prozent" if pct else ("aktiviert" if best[0]["dom"] == "scene"
                                                         else "an" if service == "turn_on" else "aus")
    return _say(f"{what} ist {state}, {_sir()}.", f"Erledigt, {_sir()}. {what} {state}.") \
        .replace(" ist", " sind", 1 if len(done) > 1 else 0)


# An ambiguous target ("mach Wohnzimmer an": a lamp and a socket) is asked back once; the
# answer names one of them ("die Lampe", "beide") and is kept for that wording (learning.choice).
CHOICE_S = 60
_CHOICE = {"n": "", "devs": [], "at": 0.0}


def _ask_choice(n, devs):
    _CHOICE.update(n=n, devs=devs, at=time.time())
    names = [d["name"] for d in devs]
    return f"Meinen Sie {', '.join(names[:-1])} oder {names[-1]}, {_sir()}? Oder beide?".replace(", ?", "?")


def _choice(n):
    if time.time() - _CHOICE["at"] > CHOICE_S or not _CHOICE["devs"]:
        return None
    s = re.sub(r"\b(bitte|jarvis|sir|die|der|das|den|meine|ich meine|meinte)\b", " ", n).strip()
    devs = _CHOICE["devs"]
    if re.fullmatch(r"\s*(beide|alle|beides|alle beide)\s*", s):
        picked = devs
    else:
        words = set(s.split())
        picked = [d for d in devs if d["words"] & words]
        if len(picked) != 1:
            picked = [d for d in devs if _norm(d["name"]).strip() in s] or picked
        if len(picked) != 1:
            return None                            # not an answer to the question: on to the other handlers
    import learning
    learning.remember_choice(_CHOICE["n"], [d["eid"] for d in picked])
    asked = _CHOICE["n"]
    _CHOICE.update(n="", devs=[], at=0.0)
    return _home(asked)


# Follow-ups to the last switching, within FOLLOW_S: "heller", "dunkler", "auf 30 Prozent",
# "wieder aus", "und im Schlafzimmer auch". Short words with no device in them, so they only
# count right after Jarvis switched something; otherwise they go on to the LLM as before.
FOLLOW_S = 120
STEP_PCT = 25
_SWITCHED = {"devs": [], "service": "", "at": 0.0}
_ROOM_WORD = re.compile(r"\w*zimmer|küche|kueche|bad|flur|diele|büro|buero|keller|garten|balkon|links|rechts")
_LEAD = r"(?:(?:und|jetzt|dann|doch|noch|bitte|mach|machs|mach es|mach sie|das|die|den|es|sie)\s+)*"


def _followup(n):
    s = re.sub(r"\s+(bitte|sir|jarvis)\b", "", n.strip())
    if time.time() - _SWITCHED["at"] > FOLLOW_S or not _SWITCHED["devs"] or len(s.split()) > 6:
        return None
    devs = _SWITCHED["devs"]
    lights = [d for d in devs if d["dom"] == "light"]
    import jarvis_mcp

    def send(targets, service, data=None):
        for d in targets:
            res = jarvis_mcp._home("home_control", {"entity_id": d["eid"], "service": service, "data": data or {}})
            if not res.startswith(("Erledigt", "Gesendet")):
                return False
        _SWITCHED["at"] = time.time()
        return True

    what = " und ".join(d["name"] for d in (lights or devs))
    m = re.fullmatch(_LEAD + r"(?:etwas|ein bisschen|ein wenig|viel|noch)?\s*(heller|dunkler)", s)
    if m and lights:
        step = STEP_PCT if m.group(1) == "heller" else -STEP_PCT
        if send(lights, "turn_on", {"brightness_step_pct": step}):
            return _say(f"{m.group(1).capitalize()}, {_sir()}.", f"{what} ist {m.group(1)}, {_sir()}.")
        return None
    m = re.fullmatch(_LEAD + r"(?:auf )?(\d{1,3}) ?(?:%|prozent)", s)
    if m and lights:
        pct = max(1, min(100, int(m.group(1))))
        if send(lights, "turn_on", {"brightness_pct": pct}):
            return f"{what} auf {pct} Prozent, {_sir()}."
        return None
    m = re.fullmatch(_LEAD + r"(?:wieder |doch )?(an|aus|ein)(?: machen| schalten)?", s)
    if m:
        service = "turn_off" if m.group(1) == "aus" else "turn_on"
        if service == "turn_off" and any(d["dom"] != "light" and PROTECTED_OFF.search(d["name"] + " " + d["eid"])
                                         for d in devs):
            return None
        if send(devs, service):
            _SWITCHED["service"] = service
            state = "an" if service == "turn_on" else "aus"
            return f"{' und '.join(d['name'] for d in devs)} {'sind' if len(devs) > 1 else 'ist'} {state}, {_sir()}."
        return None
    m = re.fullmatch(_LEAD + r"(?:auch )?(?:im|in der|in|am|beim) ([a-zäöüß]+(?: [a-zäöüß]+)?) auch", s) \
        or re.fullmatch(_LEAD + r"auch (?:im|in der|in|am|beim) ([a-zäöüß]+(?: [a-zäöüß]+)?)", s)
    if m:
        # the same kind of thing in the other room: "TV Schlafzimmer" -> "tv", then the new room
        thing = " ".join(w for w in _norm(devs[0]["name"]).split() if not _ROOM_WORD.fullmatch(w))
        state = "an" if _SWITCHED["service"] == "turn_on" else "aus"
        return _home(f"{thing} {m.group(1)} {state}".strip())
    return None


# Some handlers need the original wording (what to remind of, what goes on the
# shopping list); the normalised text has lost case and punctuation.
# Per thread: the scheduler runs planned commands from its own thread.
_RAW = threading.local()


def _raw():
    return getattr(_RAW, "text", "")


def _timers(n):
    import scheduler
    return scheduler.handle(n, _raw())


def _lists(n):
    import lists
    return lists.handle(n, _raw())


def _routine(n):
    import routines
    if re.search(r"\b(welche|was für|zeig\w*|liste?)\b.*\broutinen\b", n):
        return routines.describe()
    dry = routines.explain_request(_raw())
    if dry:
        return dry                                 # "Was passiert, wenn ich Gute Nacht sage?" — nothing runs
    r = routines.find(_raw())
    return routines.run(r, run_local) if r else None


def _pronounce(n):
    import pronounce
    return pronounce.handle(n, _raw())


def _pc(n):
    import pc
    return pc.handle(n, _raw())


def _commute(n):
    import commute
    return commute.handle(n, _raw())


def _parcels(n):
    import parcels
    return parcels.handle(n, _raw())


def _printer(n):
    import printer
    return printer.handle(n, _raw())


# "Was passiert, wenn der Pi abstürzt?" — Blast-Radius on the Pi knows the dependencies. Unknown device or
# Blast-Radius unreachable: None, and the question goes to Claude (who has the outage_impact tool).
OUTAGE = re.compile(r"\b(was (passiert|ist|geht|fällt|funktioniert|bleibt)\b.{0,40}\b(wenn|falls|sobald)\b.{0,60}"
                    r"\b(ausfällt|ausfallen|ausfall|abstürzt|abstürzen|abschmiert|kaputt|stirbt|offline|down|"
                    r"keinen strom|ausgeht|weg ist)|was fällt (alles )?aus\b.{0,30}\b(wenn|falls|ohne)|"
                    r"was hängt (alles )?(am|an der|an dem|vom|von der)\b)")


def _outage(n):
    if not OUTAGE.search(n):
        return None
    import outage_map
    try:
        return outage_map.spoken(outage_map.ask(_raw() or n.strip()))
    except Exception:  # noqa: BLE001
        return None


# Status of the homelab tools on the Pi (Restore Probe, Update-Schleuse, Stromfresser-Detektiv, Zigbee-Platzberater).
# Narrow on purpose: plain "Stromfresser" stays with power_costs' standby report, "Updates?" with updates_check.
PI_REPORTS = (
    ("backups", re.compile(r"\b(restore[ -]?probe|wiederherstellungstest|(sind|ist|waren|war) (die |das |meine? )?"
                           r"(backups?|sicherung(en)?)\b.{0,25}\b(ok|okay|in ordnung|gut|wiederherstellbar|heil|"
                           r"brauchbar|sauber)|backup[ -]?test|lassen sich (die )?backups)")),
    ("updates", re.compile(r"\b(update[ -]?schleuse|container[ -]?updates?|(warum|wieso) (wurde|wird|hast du) .{0,30}nicht "
                           r"(aktualisiert|geupdatet|upgedatet)|was (macht|sagt) die schleuse)")),
    ("strom", re.compile(r"\b(stromfresser[ -]?detektiv|detektiv|(ist|gibt es) .{0,20}beim strom .{0,20}auffällig|"
                         r"strom.{0,15}auffälligkeit|auffällig\w* .{0,15}(strom|verbrauch)|"
                         r"(steckdosen?|geräte?) .{0,20}(keine werte|auffällig))")),
    ("zigbee", re.compile(r"\b(platzberater|zigbee[ -]?(router|netz|empfang|verbindung|kanal|signal)|"
                          r"wo (soll|sollte|kann) .{0,40}zigbee|zigbee .{0,30}(stabil|schwach|gut))")),
    ("alles", re.compile(r"\b(homelab[ -]?werkzeuge|pi[ -]?werkzeuge|was sagen (die )?werkzeuge|werkzeuge auf dem pi)")),
)


def _pi_reports(n):
    topic = next((t for t, rx in PI_REPORTS if rx.search(n)), None)
    if not topic or _has(n, "warum", "erklär", "wie behebe", "repar"):
        return None                                # why/fix questions need Claude (he has pi_reports too)
    import pi_reports
    return pi_reports.spoken(topic)


def _report(n):
    if not re.search(r"\b(verbrauchsbericht|tokenbericht|token bericht|was hast du (dazu)?gelernt|"
                     r"wie viele? tokens?|wie viel hab\w* ich verbraucht)\b", n):
        return None
    import learning
    return learning.spoken_report(7)


_SHIFT = r"(spülmaschine|geschirrspüler|waschmaschine|trockner|boiler|drucker|druck|wäsche)"


def _coach(n):
    usage = re.search(r"\b(?:den|die|das)\s+(\w[\w-]*)\s+(?:nutze|benutze|brauche)\s+ich\s+(?:\w+\s+)?"
                      r"(oft|häufig|regelmäßig|ständig|täglich|selten|kaum|nie)\b|"
                      r"\b(?:ich\s+)?(?:nutze|benutze|brauche)\s+(?:ich\s+)?(?:den|die|das)\s+(\w[\w-]*)\s+(?:\w+\s+)?"
                      r"(oft|häufig|regelmäßig|ständig|täglich|selten|kaum|nie)\b", n)
    if usage:
        word, how = (usage.group(1), usage.group(2)) if usage.group(1) else (usage.group(3), usage.group(4))
        import energy_coach
        return energy_coach.set_usage(word, how not in ("selten", "kaum", "nie"))
    if not re.search(r"\b(energie-?coach|energie ?tipp|strom ?tipp|sonnenüberschuss|überschuss|sonnenstrom|"
                     r"wann (soll|sollte|kann|könnte) ich .{0,40}" + _SHIFT + r"|"
                     r"lohnt (es )?sich .{0,30}(" + _SHIFT[1:-1] + r"|strom|sonne)|"
                     r"(" + _SHIFT[1:-1] + r") .{0,30}(jetzt|gerade|heute) .{0,15}(starten|anmachen|einschalten|laufen))", n):
        return None
    import energy_coach
    return energy_coach.report()


def _announce(n):
    m = re.match(r"^\s*(?:jarvis[,\s]+)?(?:sag|sage|mach eine durchsage|durchsage)\s+(?:bitte\s+)?"
                 r"(?:im|in der|ins|in die|in den)\s+([\wäöüß]+)\s*[:,]?\s*(?:dass\s+)?(.+?)\s*$", _raw(), re.I)
    if not m:
        return None
    import speakers
    if m.group(1).lower() not in (r.lower() for r in speakers.rooms()):
        return None                                # not a room with a speaker: Claude decides
    return speakers.announce(m.group(1), m.group(2))


_TV_DO = [("pause", r"pause|anhalten|stopp"), ("weiter", r"weiter|fortsetzen|abspielen"),
          ("lauter", r"lauter"), ("leiser", r"leiser"), ("stumm", r"stumm|ton aus"), ("laut", r"ton an|ton wieder"),
          ("home", r"startseite|startbildschirm|home"), ("zurueck", r"zurück")]


def _tv(n):
    if not re.search(r"\b(fernseher|fire ?tv|glotze)\b", n):
        return None
    on_tv = r"\s+(?:auf|am)\s+(?:dem\s+)?(?:fernseher|fire ?tv)"
    app = (re.search(r"\b(?:öffne|starte|zeig mir|mach)\s+(?:mal\s+)?([\wäöüß+.-]+(?: [\wäöüß+.-]+)?)" + on_tv, n)
           or re.match(r"^\s*(?:jarvis\s+)?([\wäöüß+.-]+)" + on_tv + r"\s*$", n))
    import speakers
    if app and not re.fullmatch(r"(den|die|das|ton|es|was|etwas|\w+ (an|aus|lauter|leiser))", app.group(1)):
        word = app.group(1)
        raw = re.search(re.escape(word), _raw(), re.I)
        return speakers.tv("app", raw.group(0) if raw else word)
    for cmd, pat in _TV_DO:
        if re.search(r"\b(" + pat + r")\b", n):
            return speakers.tv(cmd)
    return None                                    # "Fernseher an/aus" stays with the plug (_home)


def _alerts(n):
    if not re.search(r"\b(unwetter|wetterwarnung|sturmwarnung|gewitterwarnung|glättewarnung|hitzewarnung|"
                     r"warnungen? (des|vom) (dwd|wetterdienst)|dwd)", n):
        return None
    import weather_alerts
    return weather_alerts.current()


def _costs(n):
    if not re.search(r"\b(stromkosten|stromfresser|standby|leerlauf ?verbrauch|was kostet (mich )?.{0,30}(strom|im monat|pro monat|im jahr)|"
                     r"wie ?viel strom (verbraucht|braucht|zieht|frisst)|stromverbrauch|strom ?budget|"
                     r"(liege|bin|bleibe) ich .{0,20}(im|unter|über) .{0,10}(budget|rahmen|plan))", n):
        return None
    import power_costs
    if _has(n, "standby", "leerlauf", "stromfresser"):
        return power_costs.report(standby=True)
    if _has(n, "budget", "im rahmen", "im plan"):
        return power_costs.report(budget=True)
    m = re.search(r"\b(?:kostet (?:mich )?|verbraucht |braucht |zieht |frisst )(?:der|die|das|mein\w*)\s+(\w[\w-]*)", n)
    return power_costs.report(m.group(1) if m and m.group(1) not in ("strom", "haus", "wohnung") else "")


def _situation(n):
    if re.search(r"\b(sicherheits(prüfung|check|scan)|sicherheitszentrale)\b.{0,25}\b(starten|start|laufen|neu|jetzt|durchlaufen)\b|"
                 r"\b(starte|mach) (eine |einen )?(neue |neuen )?sicherheits(prüfung|check|scan)", n):
        import situation
        return situation.run_scan()
    m = re.search(r"\b(lagebericht|lage im (haus|netz|homelab)|wie ist die lage|läuft alles|alles im grünen bereich|"
                  r"(uptime )?kuma|sicherheitszentrale|sicherheitslage|sicherheitsbefunde?|nulleinspeisung|"
                  r"energie-?kreislauf)", n)
    if not m or _has(n, "warum", "erklär", "einricht", "behe", "repar", "wie geht das"):
        return None                                # why/fix questions need Claude, not the status line
    word = m.group(1)
    topic = ("kuma" if "kuma" in word else "sicherheit" if "sicherheit" in word
             else "energie" if "nulleinspeisung" in word or "energie" in word else "alles")
    import situation
    return situation.report(topic)


def _emergency(n):
    import emergency
    return emergency.handle(n, deny_cards=EMERGENCY_HOOKS.get("deny"), cancel=EMERGENCY_HOOKS.get("cancel"))


EMERGENCY_HOOKS = {}     # the server sets deny (open cards) and cancel (running Claude process)


def _personality(n):
    import personality
    return personality.handle(n)


def _desktop(n):
    import desktop
    return desktop.handle(n)


def _context(n):
    import context
    return context.handle(n)


def _tasks(n):
    import tasks
    return tasks.handle(n)


def _focus(n):
    import focus
    return focus.handle(n)


def _daytime(n):
    import daytime
    return daytime.handle(n)


def _episodes(n):
    import episodes
    return episodes.handle(n)


def _wellbeing(n):
    import wellbeing
    return wellbeing.handle(n)


def _maintenance(n):
    import maintenance
    return maintenance.handle(n)


def _unifi(n):
    import unifi
    return unifi.handle(n)


def _media(n):
    import jellyfin
    return jellyfin.handle(n)


def _selfcheck(n):
    import selfcheck
    return selfcheck.handle(n)


def _habits(n):
    import user_habits
    return user_habits.handle(n)


LOCAL_HANDLERS = [_emergency, _choice, _personality, _focus, _daytime, _context, _tasks, _desktop, _routine, _timers, _lists, _pronounce,
                  _outage, _pi_reports, _report, _selfcheck, _habits, _episodes, _wellbeing, _maintenance, _situation, _coach, _costs, _announce, _tv, _alerts,
                  _unifi, _media, _pc, _commute, _parcels, _printer, _followup, _home_state, _climate, _home,
                  _music, _time, _date, _sun, _weather, _solar, _nas, _pi]


def _local(text, handlers):
    n = _norm(text)
    if text.rstrip().endswith("?") and not QUESTION.search(n):
        n = " ist" + n                             # "Luftreiniger an?" is a question too
    saved, _RAW.text = _raw(), text
    try:
        for h in handlers:
            try:
                reply = h(n)
            except Exception:  # noqa: BLE001
                reply = None
            if reply:
                reply = re.sub(r",\s*\.", ".", reply).replace(" ,", ",").replace("  ", " ")
                return reply, h.__name__.strip("_")
    finally:
        _RAW.text = saved
    return None, None


def run_local(text):
    """Only the local path, for routine steps and scheduled commands: reply or None.
    Routines cannot call routines, so a loop is impossible."""
    reply, _ = _local((text or "").strip(), [h for h in LOCAL_HANDLERS if h is not _routine])
    return reply


# Follow-ups ("Also was?", "Dann nochmal alles", "Mach das") name no topic, so the
# keyword rules gave them memory only — and Jarvis, who still sees the conversation,
# said his tools were missing. A turn without a topic keeps the last turn's tools.
CARRY_S = 600
WEB_ALWAYS = os.environ.get("JARVIS_WEB_ALWAYS", "1").strip().lower() not in ("0", "off", "false", "no", "aus")
# A follow-up that does name a topic still belongs to the last question for a while:
# "Ab wie viel Prozent greift der Schutz?" after a question about the HA automation
# brought "home" of its own and lost the full HA server that reads automations.
JOIN_S = 180
_LAST = {"groups": [], "own": [], "model": None, "at": 0.0}


def route(message, ha_full=False):
    """{'kind': 'local', 'reply': ..., 'handler': ...} or the llm_route dict."""
    text = (message or "").strip()
    if LOCAL_ENABLED and text and len(text) < 160 and not text.startswith("/"):
        reply, handler = _local(text, LOCAL_HANDLERS)
        if reply:
            return {"kind": "local", "reply": reply, "handler": handler}
    r = llm_route(text, ha_full)
    own = [g for g in r["groups"] if g != "memory"]
    age = time.time() - _LAST["at"]
    if not own and _LAST["groups"] and age < CARRY_S:
        r = dict(r, groups=list(dict.fromkeys(r["groups"] + _LAST["groups"])), reason="Folgefrage",
                 model=_LAST["model"] if _LAST["model"] == MODEL_SMART else r["model"])
    elif own and _LAST["own"] and age < JOIN_S:
        # only the last turn's own groups join, so a long conversation does not pile them up
        joined = list(dict.fromkeys(r["groups"] + _LAST["own"]))
        if joined != r["groups"]:
            r = dict(r, groups=joined, model=MODEL_SMART if _LAST["model"] == MODEL_SMART else r["model"])
    if [g for g in r["groups"] if g != "memory"]:
        _LAST.update(groups=r["groups"], own=own or _LAST["own"], model=r["model"], at=time.time())
    # Web search rides along in every turn: what he cannot find in notes or tools, he looks
    # up instead of saying he does not know. Added last and kept out of _LAST — it is no
    # topic, and it does not ask for the bigger model (only a search request does, above).
    if WEB_ALWAYS and "web" not in r["groups"]:
        r = dict(r, groups=r["groups"] + ["web"])
    return r


def forget_topic():
    """New conversation (or a test): the next vague turn carries nothing over."""
    _LAST.update(groups=[], own=[], model=None, at=0.0)
