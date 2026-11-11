"""
Homelab situation report ("Lagebericht"): one spoken summary from what already watches the
house on the Pi — all read-only, 0 tokens.

  Uptime Kuma    a status page (KUMA_URL, default http://<pi>:3001; JARVIS_KUMA_SLUG, default
                 "jarvis") — Kuma 1.x has no read API without a key, a public status page has
                 one. Which monitors are down, and since when.
  ORBIT          the security centre on the Pi (JARVIS_ORBIT_URL, default http://<pi>:8092):
                 score, open findings by severity, age of the last scan.
  energy loop    Home Assistant: the entities and automations the zero-feed-in loop needs
                 (config/homelab.json → "energy"; nothing configured = not checked).

  report(topic)  the spoken Lagebericht: alles | kuma | sicherheit | energie
  findings(cfg)  incidents for homelab_watch, in the shape of its own checks
  open_findings()  the open findings with their ids, what to do, how long it takes
  accept(id, reason, days)  a finding as a reasoned exception (the security centre's own API;
                 the tool is CONFIRM, so always a card) — 30, 90 or 365 days
  run_scan()     starts a quick scan (read-only on the Pi, about 10 s)

Device names in the security findings come from the network (DHCP names), so the tool's
result is treated as untrusted (policy.UNTRUSTED).
"""
import datetime as dt
import json
import os
import urllib.error
import urllib.request

_PI = os.environ.get("JARVIS_PI_HOST", "").strip()
KUMA = os.environ.get("KUMA_URL", "").strip().rstrip("/") or (f"http://{_PI}:3001" if _PI else "")
KUMA_SLUG = os.environ.get("JARVIS_KUMA_SLUG", "jarvis").strip()
ORBIT = os.environ.get("JARVIS_ORBIT_URL", "").strip().rstrip("/") or (f"http://{_PI}:8092" if _PI else "")
SCAN_STALE_H = float(os.environ.get("JARVIS_ORBIT_STALE_HOURS", "2"))   # the quick scan runs every 15 min
SEVERITIES = ("hoch", "mittel", "niedrig")


class NotSetUp(Exception):
    """The source is not configured (no URL, no status page) — say so, never count it as an outage."""


def _get(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _minutes(since, now=None):
    return max(1, round(((now or _now()) - since).total_seconds() / 60)) if since else None


def _ago(minutes):
    if minutes is None:
        return ""
    if minutes < 90:
        return f"seit {minutes} Minuten"
    if minutes < 48 * 60:
        return f"seit {round(minutes / 60)} Stunden"
    return f"seit {round(minutes / 1440)} Tagen"


# ── Uptime Kuma ──────────────────────────────────────────────

def _kuma_time(s):
    # heartbeat times are UTC without a zone: "2026-10-28 11:47:50.123"
    try:
        return dt.datetime.fromisoformat(str(s).replace(" ", "T")[:19]).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def parse_kuma(page, beats, now=None):
    """[{name, up, since}] from the status page and its heartbeat list. Status 0 = down, 3 = maintenance."""
    out = []
    lists = (beats or {}).get("heartbeatList") or {}
    for group in (page or {}).get("publicGroupList") or []:
        for mon in group.get("monitorList") or []:
            hb = sorted(lists.get(str(mon.get("id"))) or [], key=lambda b: str(b.get("time")))
            if not hb:
                continue
            up = hb[-1].get("status") != 0
            since = None
            if not up:                                  # the first beat of the current down streak
                for b in reversed(hb):
                    if b.get("status") != 0:
                        break
                    since = _kuma_time(b.get("time"))
            out.append(dict(name=mon.get("name") or f"Monitor {mon.get('id')}", up=up,
                            minutes=_minutes(since, now), msg=str(hb[-1].get("msg") or "")[:80]))
    return out


def kuma():
    if not (KUMA and KUMA_SLUG):
        raise NotSetUp("Uptime Kuma nicht eingerichtet (KUMA_URL oder JARVIS_PI_HOST in .env)")
    missing = NotSetUp(f"In Uptime Kuma fehlt die Statusseite „{KUMA_SLUG}“ mit den Diensten")
    try:
        # heartbeats first: for an unknown slug they come back empty at once, while Kuma 1.23
        # never answers the page request itself
        beats = _get(f"{KUMA}/api/status-page/heartbeat/{KUMA_SLUG}")
        if not (beats or {}).get("heartbeatList"):
            raise missing
        page = _get(f"{KUMA}/api/status-page/{KUMA_SLUG}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise missing from e
        raise
    return parse_kuma(page, beats)


def say_kuma(monitors):
    if not monitors:
        return "Uptime Kuma: die Statusseite zeigt keine Dienste."
    down = [m for m in monitors if not m["up"]]
    if not down:
        return f"Uptime Kuma: alle {len(monitors)} Dienste erreichbar."
    names = ", ".join(f"{m['name']} {_ago(m['minutes'])}".strip() for m in down)
    return f"Uptime Kuma: {len(down)} von {len(monitors)} Diensten ausgefallen — {names}."


# ── ORBIT security centre ────────────────────────────────────

def security():
    if not ORBIT:
        raise NotSetUp("Sicherheitszentrale nicht eingerichtet (JARVIS_ORBIT_URL oder JARVIS_PI_HOST in .env)")
    try:
        return _get(f"{ORBIT}/api/security", timeout=10)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotSetUp("Auf dem Pi gibt es keine Sicherheitszentrale") from e
        raise


def scan_age_minutes(rep, now=None):
    try:
        return _minutes(dt.datetime.fromisoformat(rep["generated"]), now)
    except (KeyError, TypeError, ValueError):
        return None


def say_security(rep, now=None, detail=False):
    counts = rep.get("counts") or {}
    open_ = [f for f in rep.get("findings") or [] if f.get("sev") in SEVERITIES]
    parts = [f"Sicherheit: {rep.get('score', '?')} von 100 Punkten"]
    if not open_:
        parts.append("keine offenen Befunde")
    else:
        hoch = counts.get("hoch", 0)
        parts.append("nichts Dringendes" if not hoch else f"{hoch} {'dringender Befund' if hoch == 1 else 'dringende Befunde'}")
        rest = [f"{counts.get(s, 0)} {s}" for s in ("mittel", "niedrig") if counts.get(s)]
        if rest:
            parts.append(" und ".join(rest))
    text = ", ".join(parts) + "."
    shown = sorted(open_, key=lambda f: SEVERITIES.index(f["sev"]))[: (6 if detail else 2)]
    shown = [f for f in shown if detail or f["sev"] != "niedrig"]
    if shown:
        text += " " + " ".join(f"{f['sev'].capitalize()}: {f.get('title', '')}." for f in shown)
    age = scan_age_minutes(rep, now)
    if age is not None and age > SCAN_STALE_H * 60:
        text += f" Achtung, die letzte Prüfung ist {_ago(age).replace('seit ', '')} alt."
    return text


# ── energy loop (Home Assistant) ─────────────────────────────

def _states():
    import briefing
    return {s["entity_id"]: s for s in briefing._ha_states()}


def _label(spec, eid, states):
    if isinstance(spec, dict) and spec.get(eid):
        return spec[eid]
    return ((states.get(eid) or {}).get("attributes") or {}).get("friendly_name") or eid


def energy_problems(cfg, states, now=None):
    """[(key, text)] for every part of the loop that is not working; [] = all fine.

    cfg: {"entities": {id: label} | [id], "online": {binary_sensor: label},
          "automations": {automation: max seconds since it last ran, 0 = only switched on}}"""
    now = now or _now()
    out = []
    ents = cfg.get("entities") or {}
    missing = [_label(ents, e, states) for e in ents
               if (states.get(e) or {}).get("state", "unavailable") in ("unavailable", "unknown", "none", "")]
    if missing:
        out.append(("energy:data", "Dem Energie-Kreislauf fehlen Werte: " + ", ".join(missing) + "."))
    online = cfg.get("online") or {}
    for e in online:
        if (states.get(e) or {}).get("state") != "on":
            out.append((f"energy:{e}", f"{_label(online, e, states)} hat keine Verbindung."))
    for e, max_s in (cfg.get("automations") or {}).items():
        s = states.get(e) or {}
        name = _label({}, e, states)
        if s.get("state") != "on":
            out.append((f"energy:{e}", f"Die Automation „{name}“ ist {'aus' if s.get('state') == 'off' else 'nicht verfügbar'}."))
            continue
        try:
            last = dt.datetime.fromisoformat((s.get("attributes") or {}).get("last_triggered") or "")
        except ValueError:
            last = None
        if max_s and (last is None or (now - last).total_seconds() > max_s):
            out.append((f"energy:{e}", f"Die Automation „{name}“ ist {_ago(_minutes(last, now)) or 'noch nie'} nicht gelaufen."))
    return out


def say_energy(problems):
    if not problems:
        return "Energie-Kreislauf: läuft."
    return "Energie-Kreislauf: " + " ".join(t for _, t in problems)


# ── report ───────────────────────────────────────────────────

def _cfg():
    import homelab_watch
    return homelab_watch.config()


def report(topic="alles", detail=False):
    topic = (topic or "alles").lower()
    cfg = _cfg()
    parts = []
    if topic in ("alles", "kuma"):
        try:
            parts.append(say_kuma(kuma()))
        except NotSetUp as e:
            parts.append(f"{e}.")
        except Exception as e:  # noqa: BLE001 — one dead source must not silence the rest
            parts.append(f"Uptime Kuma antwortet nicht ({str(e)[:60]}).")
    if topic in ("alles", "sicherheit"):
        try:
            parts.append(say_security(security(), detail=detail))
        except NotSetUp as e:
            parts.append(f"{e}.")
        except Exception as e:  # noqa: BLE001
            parts.append(f"Die Sicherheitszentrale antwortet nicht ({str(e)[:60]}).")
    if topic in ("alles", "energie") and cfg.get("energy"):
        try:
            parts.append(say_energy(energy_problems(cfg["energy"], _states())))
        except Exception as e:  # noqa: BLE001
            parts.append(f"Den Energie-Kreislauf kann ich gerade nicht prüfen ({str(e)[:60]}).")
    elif topic == "energie":
        parts.append("Für den Energie-Kreislauf ist nichts eingerichtet (config/homelab.json → energy).")
    if topic == "alles":
        try:
            import homelab_watch
            last = next((h for h in homelab_watch.history(5) if h.get("state") == "bad"), None)
            if last:
                parts.append(f"Letzte Störung am {last['at'][8:10]}.{last['at'][5:7]}. um {last['at'][11:]}: {last['text']}")
        except Exception:  # noqa: BLE001
            pass
    return " ".join(parts)


# ── working through findings ─────────────────────────────────

ACCEPT_DAYS = (30, 90, 365)


def _post(path, body):
    # the X-Orbit header is what the security centre's API requires for every change
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{ORBIT}{path}", data=data, method="POST",
                                 headers={"X-Orbit": "1", "Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def open_findings():
    rep = security()
    rows = []
    for f in sorted(rep.get("findings") or [], key=lambda f: SEVERITIES.index(f["sev"]) if f.get("sev") in SEVERITIES else 9):
        fix = " ".join((f.get("fix") or [])[:2])
        rows.append(f"[{f.get('id')}] {f.get('sev')}: {f.get('title')}. Warum: {f.get('why', '')} "
                    f"Was tun: {fix} ({f.get('effort', '?')})")
    # the report keeps the exception under "accepted" inside each finding
    acc = [f"{a.get('title')} ({'bis ' + until[8:10] + '.' + until[5:7] + '.' + until[:4] if until else 'unbefristet'})"
           for a in rep.get("accepted") or [] for until in [((a.get("accepted") or {}).get("until") or "")]]
    return (("Offene Befunde:\n" + "\n".join(rows)) if rows else "Keine offenen Befunde.") + \
        ("\nAls Ausnahme akzeptiert: " + "; ".join(acc) if acc else "")


def accept(fid, reason, days):
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 0
    if days not in ACCEPT_DAYS:
        return "Eine Ausnahme gilt 30, 90 oder 365 Tage — unbefristet nur auf der Website."
    reason = " ".join(str(reason or "").split())[:280]
    if len(reason) < 10:
        return "Für eine Ausnahme brauche ich eine Begründung in einem Satz."
    if not any(f.get("id") == fid for f in security().get("findings") or []):
        return f"Einen offenen Befund „{fid}“ gibt es nicht."
    res = _post("/api/security/accept", {"id": fid, "days": days, "reason": "Per Jarvis akzeptiert: " + reason})
    if not (res or {}).get("ok"):
        return f"Die Sicherheitszentrale hat abgelehnt ({(res or {}).get('reason', '?')})."
    run_scan()
    return f"Eingetragen: Ausnahme für {days} Tage. Die Prüfung läuft gerade neu und rechnet sie ein."


def run_scan():
    try:
        _post("/api/security/run/quick", {})
    except urllib.error.HTTPError as e:
        return "Eine Prüfung läuft gerade schon." if e.code == 409 else f"Die Prüfung ließ sich nicht starten (HTTP {e.code})."
    return "Die Schnellprüfung läuft, in etwa 15 Sekunden ist sie fertig."


# ── findings for homelab_watch ───────────────────────────────

def _skipped(name, cfg):
    return any(s.lower() in name.lower() for s in cfg.get("kuma_skip") or [])


def kuma_findings(monitors, cfg):
    import kuma_alerts
    critical = cfg.get("kuma_critical") or []
    out = [dict(key=f"kuma:{m['name']}", ok=m["up"],
                 severity="critical" if any(c.lower() in m["name"].lower() for c in critical) else "warn",
                 down=f"Uptime Kuma meldet {m['name']} seit etwa {{min}} Minuten als ausgefallen.",
                 up=f"{m['name']} ist laut Uptime Kuma wieder erreichbar.")
           for m in monitors if not _skipped(m["name"], cfg)]
    # Kuma's webhook already confirmed these (its own retries): no second round of the watcher
    return out + [f for f in out if not f["ok"] and kuma_alerts.pushed_down(f["key"][5:])]


def security_findings(rep, cfg, known, now=None):
    """Open findings of the announced severities; titles seen before and now gone come back as ok."""
    sevs = set(cfg.get("security_announce") or ["hoch"])
    found, keys = [], set()
    for f in rep.get("findings") or []:
        if f.get("sev") in sevs and f.get("title"):
            key = "security:" + f["title"][:120]
            keys.add(key)
            found.append(dict(key=key, ok=False, severity="warn",
                              down=f"Die Sicherheitszentrale meldet: {f['title']}.",
                              up=f"Erledigt laut Sicherheitszentrale: {f['title']}."))
    for key in known:
        if key.startswith("security:") and key != "security:stale" and key not in keys:
            found.append(dict(key=key, ok=True, severity="warn", down="",
                              up=f"Erledigt laut Sicherheitszentrale: {key[9:]}."))
    age = scan_age_minutes(rep, now)
    found.append(dict(key="security:stale", ok=age is None or age <= SCAN_STALE_H * 60, severity="warn",
                      down="Die Sicherheitszentrale auf dem Pi hat seit über "
                           f"{round(SCAN_STALE_H)} Stunden nicht mehr geprüft.",
                      up="Die Sicherheitszentrale prüft wieder."))
    return found


def energy_findings(ecfg, states, now=None):
    bad = dict(energy_problems(ecfg, states, now))
    keys = ["energy:data"] + [f"energy:{e}" for e in (ecfg.get("online") or {})] \
        + [f"energy:{e}" for e in (ecfg.get("automations") or {})]
    return [dict(key=k, ok=k not in bad, severity="warn", down=bad.get(k, ""),
                 up="Der Energie-Kreislauf läuft wieder." if k == "energy:data" else
                 "Im Energie-Kreislauf ist wieder alles in Ordnung.")
            for k in keys]


def findings(cfg):
    """All three sources; an unreachable or unconfigured source yields nothing (the Pi check covers that)."""
    import commands
    known = list((commands.load().get("homelab") or {}).keys())
    out = []
    try:
        monitors = kuma()
        out += kuma_findings(monitors, cfg)
        import kuma_alerts
        kuma_alerts.sync([m for m in monitors if not _skipped(m["name"], cfg)], cfg)
    except Exception:  # noqa: BLE001
        pass
    try:
        out += security_findings(security(), cfg, known)
    except Exception:  # noqa: BLE001
        pass
    if cfg.get("energy"):
        try:
            out += energy_findings(cfg["energy"], _states())
        except Exception:  # noqa: BLE001
            pass
    return out
