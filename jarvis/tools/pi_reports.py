"""
Pi reports — reads the status of the homelab tools on the Pi; read-only, 0 tokens.

    backups   Restore Probe (:8096/latest.json): last night's test restore of every backed-up service
    updates   Update-Schleuse (:8100/status.json): container updates waiting, held back or failed
    strom     Stromfresser-Detektiv (:8101/api/status): open findings on the power plugs
    zigbee    Zigbee-Platzberater (:8099/api/state): router recommendation and channel check
    akku      Akku-Doktor (:8104/api/status): health of the home battery (usable kWh, SOH, cycles, warranty)
    automationen  Automations-Prüfer (:8102/api/report): broken, looping or conflicting HA automations
    drift     Homelab-Drift (:8097/metrics, Prometheus text): changes on the hosts, scan health

    report(topic)      (severity, German sentence); severity "ok", "attention" or "unknown"
    spoken(topic)      the sentence for the voice output ("alles" = every tool in one go)
    describe(topic)    text for the LLM tool `pi_reports`
    briefing_line()    only what needs attention, "" when everything is fine — for the morning briefing

The tools are still growing, so every reader only relies on a few fields and treats anything else as
optional. URLs: JARVIS_RESTORE_PROBE_URL, JARVIS_UPDATE_AIRLOCK_URL, JARVIS_STROMFRESSER_URL,
JARVIS_ZIGBEE_ADVISOR_URL, JARVIS_AKKU_DOKTOR_URL, JARVIS_AUTOMATIONS_PRUEFER_URL, JARVIS_HOMELAB_DRIFT_URL
in .env; default http://<JARVIS_PI_HOST>:<port>/<path>.
"""
import json
import os
import time
import urllib.request

_PI = os.environ.get("JARVIS_PI_HOST", "").strip()
TIMEOUT = float(os.environ.get("JARVIS_PI_REPORTS_TIMEOUT", "4"))
CACHE_S = float(os.environ.get("JARVIS_PI_REPORTS_CACHE_S", "60"))
RESTORE_MAX_AGE_H = 36       # the probe runs every night; older than that means it did not run
MAX_ITEMS = 3                # findings read aloud; the rest is counted


def _url(env, port, path):
    url = os.environ.get(env, "").strip()
    return url or (f"http://{_PI}:{port}{path}" if _PI else "")


SOURCES = {
    "backups": ("Restore Probe", _url("JARVIS_RESTORE_PROBE_URL", 8096, "/latest.json")),
    "updates": ("Update-Schleuse", _url("JARVIS_UPDATE_AIRLOCK_URL", 8100, "/status.json")),
    "strom": ("Stromfresser-Detektiv", _url("JARVIS_STROMFRESSER_URL", 8101, "/api/status")),
    "zigbee": ("Zigbee-Platzberater", _url("JARVIS_ZIGBEE_ADVISOR_URL", 8099, "/api/state")),
    "akku": ("Akku-Doktor", _url("JARVIS_AKKU_DOKTOR_URL", 8104, "/api/status")),
    "automationen": ("Automations-Prüfer", _url("JARVIS_AUTOMATIONS_PRUEFER_URL", 8102, "/api/report")),
    "drift": ("Homelab-Drift", _url("JARVIS_HOMELAB_DRIFT_URL", 8097, "/metrics")),
}
_PROM = {"drift"}            # Homelab-Drift only writes HTML pages and its /metrics; no JSON of its own
PRUEFER_STALE_H = 3          # the Prüfer checks every few minutes; hours without a check means it hangs
DRIFT_STALE_H = 2            # Drift scans every 15 min
_CACHE = {}


def _prom(text):
    """Prometheus text format -> {(name, ((label, value), ...)): float}. Enough for counters and gauges."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head, _, value = line.rpartition(" ")
        name, _, labels = head.partition("{")
        pairs = tuple(sorted((k.strip(), v.strip().strip('"'))
                             for k, _, v in (p.partition("=") for p in labels.rstrip("}").split(",") if "=" in p)))
        try:
            out[(name, pairs)] = float(value)
        except ValueError:
            continue
    return out


def fetch(topic):
    name, url = SOURCES[topic]
    if not url:
        raise RuntimeError(f"{name} nicht eingerichtet (JARVIS_PI_HOST in .env)")
    hit = _CACHE.get(topic)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        raw = r.read()
    data = _prom(raw.decode("utf-8", "replace")) if topic in _PROM else json.loads(raw)
    _CACHE[topic] = (time.time(), data)
    return data


def _join(names):
    names = [n for n in names if n]
    if len(names) > 1:
        return ", ".join(names[:-1]) + " und " + names[-1]
    return names[0] if names else ""


def _more(items, shown):
    rest = len(items) - shown
    return f" Dazu {rest} weitere." if rest > 0 else ""


# ── Restore Probe ────────────────────────────────────────────────
def _backups(d, brief=False):
    services = d.get("services") or []
    bad = [s for s in services if s.get("status") not in ("ok", "skipped")]
    finished = d.get("finished") or d.get("started") or 0
    age_h = (time.time() - finished) / 3600 if finished else None
    if age_h is not None and age_h > RESTORE_MAX_AGE_H:
        return "attention", (f"Der letzte Wiederherstellungstest der Backups ist {age_h / 24:.0f} Tage alt – "
                             "die Restore Probe ist seitdem nicht gelaufen.")
    if d.get("error") and not services:
        return "attention", "Der Wiederherstellungstest der Backups ist heute Nacht gescheitert."
    if bad:
        names = [s.get("title") or s.get("name") for s in bad]
        warn_only = all(s.get("status") in ("warn", "warning") for s in bad)
        what = "mit Warnungen durchgelaufen" if warn_only else "nicht sauber wiederherstellbar"
        return "attention", (f"Beim Backup-Test heute Nacht {'war' if len(bad) == 1 else 'waren'} "
                             f"{_join(names[:MAX_ITEMS])} {what}.{_more(names, MAX_ITEMS)} "
                             f"{len(services) - len(bad)} von {len(services)} Diensten waren in Ordnung.")
    if not services:
        return "unknown", "Die Restore Probe hat noch keinen Bericht mit Diensten."
    return "ok", (f"Die Backups sind in Ordnung: Heute Nacht wurden alle {len(services)} Dienste testweise "
                  "wiederhergestellt.")


# ── Update-Schleuse ──────────────────────────────────────────────
_POSTPONE = (("at home", "weil du zu Hause warst"), ("active", "weil du zu Hause warst"),
             ("quiet", "wegen der Ruhezeit"), ("window", "weil kein Wartungsfenster war"))
_PRETTY = {"homeassistant": "Home Assistant", "uptime-kuma": "Uptime Kuma", "noah-mqtt": "noah-mqtt"}


def _cname(name):
    return _PRETTY.get(name, name)


def _updates(d, brief=False):
    cs = d.get("containers") or {}
    by = {}
    for name, c in cs.items():
        by.setdefault((c or {}).get("status") or "", []).append((name, c or {}))
    parts, severity = [], "ok"
    broken = by.get("error", []) + by.get("test_failed", []) + by.get("rolled_back", [])
    if broken:
        severity = "attention"
        parts.append(f"Bei {_join([_cname(n) for n, _ in broken[:MAX_ITEMS]])} ist ein Update gescheitert "
                     "oder wurde zurückgerollt." + _more(broken, MAX_ITEMS))
    for name, c in by.get("held", []):
        severity = "attention"
        why = {"breaking_change": "es Breaking Changes gibt", "major": "es ein großer Versionssprung ist"}.get(
            c.get("held_reason") or "", "es erst geprüft werden muss")
        ver = f" {c['available_version']}" if c.get("available_version") and "@" not in c["available_version"] else ""
        parts.append(f"{_cname(name)}{ver} wartet auf dich, weil {why}.")
    pending = by.get("ready", []) + by.get("candidate", [])
    if pending and not brief:          # routine, runs on its own: not worth the morning briefing
        word = "Container-Update" if len(pending) == 1 else "Container-Updates"
        line = f"{len(pending)} {word} {'steht' if len(pending) == 1 else 'stehen'} an und laufen automatisch durch"
        reason = ((d.get("global") or {}).get("last_postponed") or {}).get("reason") or ""
        why = next((w for key, w in _POSTPONE if key in reason), "")
        parts.append(line + (f"; zuletzt verschoben, {why}." if why else "."))
    if d.get("overall") == "error" and not broken:
        severity = "attention"
        parts.append("Die letzte Prüfung der Update-Schleuse ist gescheitert.")
    if not parts:
        return "ok", "Alle Container sind aktuell."
    return severity, " ".join(parts)


# ── Stromfresser-Detektiv ────────────────────────────────────────
def _strom(d, brief=False):
    open_ = [o for o in d.get("offen") or [] if (o.get("zustand") or "offen") == "offen"]
    if open_:
        texts = [(o.get("meldung") or o.get("geraet") or "").strip() for o in open_]
        head = "Eine offene Meldung" if len(open_) == 1 else f"{len(open_)} offene Meldungen"
        return "attention", f"Stromfresser-Detektiv: {head}. " + " ".join(texts[:MAX_ITEMS]) + _more(texts, MAX_ITEMS)
    learning = [k for k, v in (d.get("lernfortschritt") or {}).items()
                if isinstance(v, str) and "/" in v and v.split("/")[0] != v.split("/")[1]]
    tail = f" Noch am Lernen: {len(learning)} Gerät{'e' if len(learning) != 1 else ''}." if learning else ""
    return "ok", "Beim Strom ist nichts auffällig." + tail


# ── Zigbee-Platzberater ──────────────────────────────────────────
def _zigbee(d, brief=False):
    rec = d.get("recommendation") or {}
    text = [t for t in d.get("text") or [] if t]
    ch = d.get("channel") or {}
    health = d.get("health") or {}
    severity = "ok"
    if rec.get("status") == "insufficient_data":
        lines = [text[0] if text else "Noch zu wenig Daten für eine Empfehlung.", d.get("basis") or ""]
    else:
        lines = text[:MAX_ITEMS] or ["Keine Empfehlung."]
        if rec.get("candidates") or rec.get("weak"):
            severity = "attention"
    if ch.get("tone") not in (None, "", "ok"):
        severity = "attention"
        lines.append(ch.get("headline") or ch.get("text") or "")
    if health and health.get("ok") is False:
        severity = "attention"
        lines.append(health.get("text") or "Der Platzberater bekommt keine Daten von Home Assistant.")
    return severity, " ".join(x.strip() for x in lines if x and x.strip())


def _de(x, digits=1):
    return f"{x:.{digits}f}".replace(".", ",")


def _fix(text):
    """Akku-Doktor 1.1.0 sends some German texts double-encoded ("verlÃ¤sslich"); undo that, else keep as is."""
    text = str(text or "").strip()
    if "Ã" not in text and "Â" not in text and "â€" not in text:
        return text
    for enc in ("cp1252", "latin-1"):
        try:
            return text.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return text


# ── Akku-Doktor ──────────────────────────────────────────────────
def _akku(d, brief=False):
    system = (d.get("storages") or {}).get("system") or {}
    cap, war = system.get("capacity") or {}, system.get("warranty") or {}
    severity, parts = "ok", []
    if d.get("ha_connected") is False:
        severity = "attention"
        parts.append("Der Akku-Doktor bekommt gerade keine Daten von Home Assistant.")
    alerts = [a for a in d.get("alerts") or [] if a]
    for a in alerts[:MAX_ITEMS]:
        severity = "attention"
        text = (a.get("text") or a.get("message") or a.get("title") or "") if isinstance(a, dict) else a
        parts.append(_fix(text).rstrip(".") + ".")
    if alerts:
        parts[-1] += _more(alerts, MAX_ITEMS)
    if war.get("status") not in (None, "", "ok"):
        severity = "attention"
        notes = " ".join(_fix(n) for n in war.get("notes") or [] if n)
        parts.append("Bei der Garantie des Speichers ist etwas auffällig." + (f" {notes}" if notes else ""))
    if brief:
        return severity, " ".join(parts)
    soh, kwh = cap.get("soh"), cap.get("cap_kwh")
    if cap.get("status") == "ok" and soh is not None and kwh is not None:
        line = f"Der Speicher hat noch etwa {soh:.0f} Prozent seiner Kapazität, rund {_de(kwh)} Kilowattstunden nutzbar"
        if cap.get("lo") is not None and cap.get("hi") is not None:
            line += f" ({_de(cap['lo'])} bis {_de(cap['hi'])})"
        parts.insert(0, line + ".")
    else:
        parts.insert(0, "Für eine sichere Schätzung der Kapazität reichen die Daten noch nicht.")
    cycles = (d.get("cycles") or {}).get("system")
    if cycles:
        end = str(war.get("end") or "")[:4]
        parts.append(f"Bisher {cycles:.0f} Vollzyklen" + (f", Garantie bis {end}." if end else "."))
    return severity, " ".join(parts)


# ── Automations-Prüfer ───────────────────────────────────────────
def _ftitle(f):
    return str(f.get("title") or f.get("item_label") or f.get("item") or "").strip().rstrip(".")


def _automationen(d, brief=False):
    findings = d.get("findings") or []
    crit = [f for f in findings if f.get("severity") == "critical"]
    warn = [f for f in findings if f.get("severity") == "warning"]
    last = d.get("last_check") or 0
    age_h = (time.time() - last) / 3600 if last else None
    severity, parts = "ok", []
    if d.get("status") not in (None, "", "ok") or d.get("trusted") is False:
        severity = "attention"
        detail = str(d.get("detail") or "").strip().rstrip(".")
        parts.append("Der Automations-Prüfer kann Home Assistant gerade nicht sauber lesen" + (f": {detail}." if detail else "."))
    elif age_h is not None and age_h > PRUEFER_STALE_H:
        severity = "attention"
        parts.append(f"Der Automations-Prüfer hat seit {age_h:.0f} Stunden nicht mehr geprüft.")
    if crit:
        severity = "attention"
        head = "Ein kritischer Befund" if len(crit) == 1 else f"{len(crit)} kritische Befunde"
        parts.append(f"{head} bei den Automationen: " + "; ".join(_ftitle(f) for f in crit[:MAX_ITEMS]) + "."
                     + _more(crit, MAX_ITEMS))
    if warn and not brief:                 # warnings stay out of the morning briefing; the Prüfer pushes criticals itself
        severity = "attention"
        head = "Eine Warnung" if len(warn) == 1 else f"{len(warn)} Warnungen"
        parts.append(f"{head}: " + "; ".join(_ftitle(f) for f in warn[:MAX_ITEMS]) + "." + _more(warn, MAX_ITEMS))
    if parts:
        return severity, " ".join(parts)
    info = sum(1 for f in findings if f.get("severity") == "info")
    tail = f" Dazu {info} Hinweise zur Ordnung." if info and not brief else ""
    return "ok", "Die Automationen sind in Ordnung." + tail


# ── Homelab-Drift ────────────────────────────────────────────────
def _drift(m, brief=False):
    if not m:
        raise ValueError("no metrics")

    def val(name, **labels):
        return m.get((name, tuple(sorted(labels.items()))))

    severity, parts = "ok", []
    last = val("homelab_drift_last_success_timestamp_seconds")
    age_h = (time.time() - last) / 3600 if last else None
    if val("homelab_drift_scan_ok") == 0:
        severity = "attention"
        parts.append("Der letzte Scan von Homelab-Drift ist gescheitert.")
    elif age_h is not None and age_h > DRIFT_STALE_H:
        severity = "attention"
        parts.append(f"Homelab-Drift hat seit {age_h:.0f} Stunden nicht mehr gescannt.")
    down = sorted(dict(labels).get("host", "?") for (name, labels), v in m.items()
                  if name == "homelab_drift_host_reachable" and v == 0)
    if down:
        severity = "attention"
        parts.append(f"Homelab-Drift erreicht {_join(down)} nicht.")
    instant = int(val("homelab_drift_findings", severity="instant", status="open") or 0)
    digest = int(val("homelab_drift_findings", severity="digest", status="open") or 0)
    if instant:
        severity = "attention"
        parts.append("Eine wichtige Änderung im Homelab ist noch offen." if instant == 1
                     else f"{instant} wichtige Änderungen im Homelab sind noch offen.")
    if digest and not brief:
        parts.append("Eine kleinere Änderung wartet auf den Wochenbericht." if digest == 1
                     else f"{digest} kleinere Änderungen warten auf den Wochenbericht.")
    if not parts:
        return "ok", "Im Homelab hat sich nichts Unerwartetes verändert."
    if severity == "ok":
        parts.insert(0, "Im Homelab ist nichts Dringendes.")
    return severity, " ".join(parts)


_READERS = {"backups": _backups, "updates": _updates, "strom": _strom, "zigbee": _zigbee,
            "akku": _akku, "automationen": _automationen, "drift": _drift}


def report(topic, brief=False):
    name = SOURCES[topic][0]
    try:
        data = fetch(topic)
    except Exception:  # noqa: BLE001 — a tool that is down is a finding of its own, not a crash
        return "unknown", f"{name} ist gerade nicht erreichbar."
    try:
        return _READERS[topic](data, brief)
    except Exception:  # noqa: BLE001 — format changed under us
        return "unknown", f"Den Bericht von {name} kann ich gerade nicht lesen."


def spoken(topic):
    if topic != "alles":
        return report(topic)[1]
    results = [report(t) for t in SOURCES]
    bad = [text for sev, text in results if sev != "ok"]
    if not bad:
        return ("Auf dem Pi ist alles in Ordnung: Backups, Updates, Strom, Zigbee, Speicher, Automationen "
                "und Homelab ohne Auffälligkeiten.")
    return " ".join(bad)


def describe(topic):
    topics = list(SOURCES) if topic in ("", None, "alles") else [topic]
    out = []
    for t in topics:
        sev, text = report(t)
        out.append(f"[{SOURCES[t][0]} · {sev}] {text}")
    return "\n".join(out)


def briefing_line():
    bad = []
    for t in SOURCES:
        sev, text = report(t, brief=True)
        if sev == "attention":
            bad.append(text)
    return ("Homelab-Werkzeuge auf dem Pi: " + " ".join(bad)) if bad else ""
