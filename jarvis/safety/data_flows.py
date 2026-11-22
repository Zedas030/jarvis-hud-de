"""
Data flows: which data leave the house, to whom, and what turns each flow off.

  flows(env)     every destination with "active" worked out from the settings (.env)
  spoken(env)    a few sentences for "Welche Daten gehen in die Cloud?" (local router, 0 tokens)
  report(env)    the list for this installation, as text (scripts/data_flows.py)
  markdown()     the table for docs/datenfluesse.md (no state of this installation)

One list in code, so that the answer, the script and the document cannot drift apart;
tests/test_data_flows.py fails when a host in the code is missing here. Reads settings only:
no network, and no value from .env is ever printed.

Idea from the data-boundary scan in OpenJarvis (github.com/open-jarvis/OpenJarvis,
Apache-2.0: `jarvis scan --data-boundaries`); written from scratch for this project.
"""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOME_CONFIG = pathlib.Path(os.environ.get("JARVIS_HOME_CONFIG") or ROOT / "config" / "home.json")

TALK, VOICE, SERVICE, PLAIN = "Gespräche", "Stimme", "Dienste", "ohne persönliche Daten"


def _set(*names):
    return lambda env: all((env.get(n) or "").strip() for n in names)


def _fallback(name, key):
    def active(env):
        order = (env.get("JARVIS_FALLBACK") or "groq,gemini,ollama").lower()
        return bool((env.get(key) or "").strip()) and name in [p.strip() for p in order.split(",")]
    return active


def _tts(name, key=""):
    """In the chain (TTS_CHAIN, else the single choice JARVIS_TTS) and, where one is needed, with a key."""
    def active(env):
        chain = (env.get("TTS_CHAIN") or "").strip().lower()
        names = [p.strip() for p in chain.split(",")] if chain else [(env.get("JARVIS_TTS") or "auto").strip().lower()]
        if not chain and names == ["auto"]:
            names = ["edge"]
        return name in names and (not key or bool((env.get(key) or "").strip()))
    return active


def _stt(env):
    kind = (env.get("JARVIS_STT") or "auto").strip().lower()
    return (kind == "fish" and bool(env.get("FISH_AUDIO_API_KEY"))) or (kind == "elevenlabs" and bool(env.get("ELEVENLABS_API_KEY")))


def _not_off(name):
    return lambda env: (env.get(name) or "1").strip().lower() not in ("0", "false", "no", "off")


def _echo(env):
    try:
        return "alexa_media" in HOME_CONFIG.read_text(encoding="utf-8-sig")
    except OSError:
        return False


def _always(env):
    return True


# key, group, destination, hosts in the code (for the test), what goes there, check, how to turn it off
FLOWS = [
    ("anthropic", TALK, "Claude (Anthropic)", ("api.anthropic.com",),
     "Jede Anfrage, die der lokale Router nicht selbst beantwortet: Ihr Satz, die letzten Wortwechsel, passende Notizen "
     "und die Ergebnisse der Werkzeuge, die dafür gebraucht werden (Mail, Kalender, Dateien nur auf Nachfrage). "
     "Ein Bildschirmfoto nur nach Ihrer Bestätigung auf der Karte.",
     lambda env: (env.get("JARVIS_RUNTIME") or "auto").strip().lower() != "mock",
     "Nicht abschaltbar, das ist das Gehirn. Bildbeschreibung aus: JARVIS_VISION=0."),
    ("groq", TALK, "Groq (Notbetrieb)", ("api.groq.com",),
     "Nur wenn Claude gesperrt ist oder ausfällt: dieselbe Anfrage wie an Claude, auch Mail, Kalender und Gesundheit.",
     _fallback("groq", "GROQ_API_KEY"), "GROQ_API_KEY leeren oder JARVIS_FALLBACK=ollama (bzw. off)."),
    ("gemini", TALK, "Google Gemini (Notbetrieb)", ("generativelanguage.googleapis.com",),
     "Nur im Notbetrieb nach Groq, mit geschwärztem Kontext; nie Anfragen zu Mail, Kalender oder Gesundheit "
     "(die kostenlose Stufe darf mit Eingaben trainieren).",
     _fallback("gemini", "GEMINI_API_KEY"), "GEMINI_API_KEY leeren oder gemini aus JARVIS_FALLBACK nehmen."),
    ("fish", VOICE, "Fish Audio (Sprachausgabe)", ("api.fish.audio",),
     "Der Text, den Jarvis spricht.", _tts("fish", "FISH_AUDIO_API_KEY"), "fish aus TTS_CHAIN nehmen; `local` spricht ohne Cloud."),
    ("elevenlabs", VOICE, "ElevenLabs (Sprachausgabe)", ("api.elevenlabs.io",),
     "Der Text, den Jarvis spricht.", _tts("elevenlabs", "ELEVENLABS_API_KEY"), "elevenlabs aus TTS_CHAIN nehmen."),
    ("azure", VOICE, "Microsoft Azure Speech (Sprachausgabe)", (),
     "Der Text, den Jarvis spricht.", _tts("azure", "AZURE_SPEECH_KEY"), "azure aus TTS_CHAIN nehmen."),
    ("google_tts", VOICE, "Google Text-to-Speech (Sprachausgabe)", ("texttospeech.googleapis.com",),
     "Der Text, den Jarvis spricht.", _tts("google", "GOOGLE_TTS_API_KEY"), "google aus TTS_CHAIN nehmen."),
    ("edge", VOICE, "Microsoft Edge-Stimmen (Sprachausgabe)", (),
     "Der Text, den Jarvis spricht (kostenloser Dienst, ohne Konto).", _tts("edge"), "edge aus TTS_CHAIN nehmen."),
    ("cloud_stt", VOICE, "Fish Audio / ElevenLabs (Spracherkennung)", (),
     "Die Tonaufnahme Ihres Satzes — nur wenn die Erkennung ausdrücklich auf einen Cloud-Dienst gestellt ist. "
     "Standard ist Whisper auf dem eigenen PC.",
     _stt, "JARVIS_STT=local (oder auto mit installiertem faster-whisper)."),
    ("echo", VOICE, "Amazon Alexa (Echo als Lautsprecher)", (),
     "Der Text der Antwort, wenn ein Echo sie vorliest: über Home Assistant an Amazon.",
     _echo, "Den Echo in config/home.json als Lautsprecher austragen."),
    ("mail", SERVICE, "Ihr Mail-Anbieter (IMAP)", (),
     "Jarvis meldet sich an Ihrem Postfach an, liest und sucht; Entwürfe landen im Ordner Entwürfe. Gesendet wird nichts.",
     lambda env: bool((env.get("IMAP_HOST") or env.get("MAIL_USER") or "").strip()), "IMAP_HOST/MAIL_USER leeren."),
    ("icloud", SERVICE, "Apple iCloud (Kalender)", ("caldav.icloud.com",),
     "Kalender lesen; neue Termine nach Bestätigung eintragen.", _set("ICLOUD_USER"), "ICLOUD_USER leeren."),
    ("garmin", SERVICE, "Garmin Connect", (),
     "Anmeldung mit Ihrem Konto; Schlaf- und Aktivitätswerte kommen von dort auf den PC.", _set("GARMIN_EMAIL"), "GARMIN_EMAIL leeren."),
    ("spotify", SERVICE, "Spotify", ("api.spotify.com", "accounts.spotify.com"),
     "Wiedergabe steuern und lesen, was läuft.", _set("JARVIS_SPOTIFY_CLIENT_ID"), "JARVIS_SPOTIFY_CLIENT_ID leeren."),
    ("weather", SERVICE, "Open-Meteo (Wetter)", ("api.open-meteo.com", "geocoding-api.open-meteo.com"),
     "Die Wetter-Koordinaten aus .env (JARVIS_WEATHER_LAT/LON) und Ortsnamen, nach deren Wetter Sie fragen.",
     _always, "Nicht abschaltbar; gröbere Koordinaten eintragen, wenn der genaue Standort nicht hinaus soll."),
    ("alerts", SERVICE, "Bright Sky (Warnungen des Deutschen Wetterdienstes)", ("api.brightsky.dev",),
     "Die Wetter-Koordinaten.", _not_off("JARVIS_WEATHER_ALERTS"), "JARVIS_WEATHER_ALERTS=0."),
    ("commute", SERVICE, "OpenStreetMap (Nominatim, OSRM)", ("nominatim.openstreetmap.org", "router.project-osrm.org"),
     "Bei Fragen zur Fahrzeit: die Zieladresse und die Koordinaten von zu Hause.",
     _always, "Fahrzeit nicht abfragen; JARVIS_HOME_LAT/LON gröber eintragen."),
    ("dhl", SERVICE, "DHL (Sendungsverfolgung)", ("api-eu.dhl.com",),
     "Sendungsnummern aus Ihren Versandmails.", _set("DHL_API_KEY"), "DHL_API_KEY leeren."),
    ("track17", SERVICE, "17TRACK (Sendungsverfolgung)", ("api.17track.net",),
     "Sendungsnummern aus Ihren Versandmails.", _set("TRACK17_API_KEY"), "TRACK17_API_KEY leeren."),
    ("phone", SERVICE, "Push aufs Handy (Home-Assistant-App, über Apple/Google)", (),
     "Der Text eines Vorschlags oder einer Meldung.", _set("JARVIS_PHONE_NOTIFY"), "JARVIS_PHONE_NOTIFY leeren."),
    ("web", SERVICE, "Webseiten und Websuche", (),
     "Nur auf Nachfrage: Suchbegriffe und die aufgerufene Adresse.", _always, "Nicht danach fragen."),
    ("makerworld", SERVICE, "MakerWorld (Druckideen)", ("makerworld.com",),
     "Nur auf Nachfrage: der Suchbegriff.", _always, "Nicht danach fragen."),
    ("cost", PLAIN, "Anthropic Cost API", ("api.anthropic.com",),
     "Nur der Abrechnungsschlüssel; zurück kommen Kosten.", _set("JARVIS_COST_KEY"), "JARVIS_COST_KEY leeren."),
    ("checks", PLAIN, "Versions- und Verbindungsprüfungen (AVM, home-assistant.io, google.com)",
     ("download.avm.de", "version.home-assistant.io", "www.google.com"),
     "Nur der Abruf selbst (Ihre IP-Adresse), keine Inhalte.", _always, "Nicht abschaltbar."),
]

# What never leaves the house, said in one breath.
LOCAL = ("Spracherkennung (Whisper auf dem PC), Weckwort, Gedächtnis und Gesprächsprotokoll, Home Assistant, "
         "Sonos, NAS, Pi, Paperless, Jellyfin, UniFi, Bildschirm-Texterkennung, die lokale Stimme und Ollama")


def flows(env=None):
    env = os.environ if env is None else env
    out = []
    for key, group, name, hosts, what, check, off in FLOWS:
        try:
            active = bool(check(env))
        except Exception:  # noqa: BLE001 — a broken setting is not a reason to hide the row
            active = False
        out.append(dict(key=key, group=group, name=name, hosts=hosts, what=what, active=active, off=off))
    return out


def _short(name):
    return name.split(" (")[0]


def spoken(env=None):
    """A few sentences: who gets something right now, what stays at home."""
    rows = [f for f in flows(env) if f["active"] and f["group"] != PLAIN]
    by = {g: [_short(f["name"]) for f in rows if f["group"] == g] for g in (TALK, VOICE, SERVICE)}
    out = [f"Außer Haus gehen Daten derzeit an {len(rows)} Dienste."]
    if by[TALK]:
        first, rest = by[TALK][0], by[TALK][1:]
        out.append(f"Ihre Anfragen gehen an {first}" + (f", im Notbetrieb an {' und '.join(rest)}." if rest else "."))
    if by[VOICE]:
        out.append("Was ich spreche, geht an " + ", ".join(by[VOICE]) + ".")
    if by[SERVICE]:
        out.append("Dazu kommen " + ", ".join(by[SERVICE]) + ".")
    out.append("Im Haus bleiben Spracherkennung, Gedächtnis und Home Assistant. "
               "Die ganze Liste mit den Schaltern steht in docs/datenfluesse.md.")
    return " ".join(out)


def report(env=None):
    lines = []
    rows = flows(env)
    for group in (TALK, VOICE, SERVICE, PLAIN):
        lines.append(f"\n{group}")
        for f in (r for r in rows if r["group"] == group):
            lines.append(f"  [{'aktiv' if f['active'] else '  aus'}] {f['name']}\n          {f['what']}\n          Abschalten: {f['off']}")
    lines.append(f"\nBleibt im Haus: {LOCAL}.")
    active = sum(1 for r in rows if r["active"] and r["group"] != PLAIN)
    return f"{active} Dienste außer Haus bekommen derzeit Daten ({len(rows)} sind möglich)." + "\n".join(lines)


def markdown():
    out = ["# Datenflüsse: was das Haus verlässt",
           "",
           "Erzeugt aus `jarvis/safety/data_flows.py` (`scripts/data_flows.py --write`); nicht von Hand ändern.",
           "Welche Zeilen in Ihrer Installation aktiv sind, zeigt `scripts/data_flows.py` oder die Frage",
           "„Welche Daten gehen in die Cloud?“.",
           ""]
    for group in (TALK, VOICE, SERVICE, PLAIN):
        out += [f"## {group}", "", "| Ziel | Was dorthin geht | Abschalten |", "|---|---|---|"]
        out += [f"| {name} | {what} | {off} |" for _, g, name, _, what, _, off in FLOWS if g == group]
        out.append("")
    out += ["## Bleibt im Haus", "", LOCAL + ".", ""]
    return "\n".join(out)
