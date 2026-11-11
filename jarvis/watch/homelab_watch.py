"""
Homelab and network observer: outages, recoveries and unusual values — reported
by Jarvis himself, but only when it matters. Plain rules, 0 tokens.

Watches (all optional, from .env and config/homelab.json):
  Home Assistant    reachable?                                   critical
  Raspberry Pi      reachable? services, containers, temperature, disk, load
  NAS               reachable? temperature (disk health and a full volume
                    are already reported by watchers.py)
  devices           important devices by ping (host) or HA tracker (entity)

When it reports:
  - an outage only after JARVIS_HOMELAB_CONFIRM (2) checks in a row, so a
    reboot or a dropped packet stays silent; one message per incident, plus
    one when it is back
  - during the quiet hours only critical things; the rest waits for the morning
    (if it is still a problem then)
  - unusual values: fixed limits from config/homelab.json, and after one week
    of hourly samples also clear outliers from the device's normal values
  - at most JARVIS_HOMELAB_MAX_PER_DAY (2) messages per incident and day

  Uptime Kuma, the security centre on the Pi and the energy loop: situation.py
                    (config/homelab.json: kuma_skip, kuma_critical, security_announce, energy)

New devices on the WLAN are still reported by the existing watchdog (briefing.py).
"""
import json
import os
import pathlib
import re
import statistics
import subprocess
import threading
import time
import urllib.request

import background
import commands
import events
import hostos
import jarvis_log

log = jarvis_log.get(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
CONFIG = pathlib.Path(os.environ.get("JARVIS_HOMELAB_CONFIG", ROOT / "config" / "homelab.json"))
BASELINE = pathlib.Path(os.environ.get("JARVIS_HOMELAB_BASELINE", ROOT / "logs" / "homelab_baseline.json"))
EVERY = int(os.environ.get("JARVIS_HOMELAB_SECONDS", "300"))
CONFIRM = int(os.environ.get("JARVIS_HOMELAB_CONFIRM", "2"))
MAX_PER_DAY = int(os.environ.get("JARVIS_HOMELAB_MAX_PER_DAY", "2"))
ENABLED = os.environ.get("JARVIS_HOMELAB", "1").strip() not in ("0", "false", "no", "off")
LEARN_SAMPLES = 7 * 24            # one week of hourly samples before outliers count
LIMITS = {"pi_temp": 75, "pi_disk": 90, "pi_load": 4.0, "nas_temp": 60, "pc_gpu_temp": 85, "pc_disk": 92}
HISTORY = pathlib.Path(os.environ.get("JARVIS_HOMELAB_HISTORY", ROOT / "logs" / "homelab_events.jsonl"))
# how far above normal an outlier must be (absolute), so 41 °C after a calm 38 °C stays quiet
MIN_DELTA = {"pi_temp": 8, "pi_disk": 5, "pi_load": 1.0, "nas_temp": 6, "pc_gpu_temp": 10, "pc_disk": 5}
LABEL = {"pi_temp": ("die Temperatur des Pi", " Grad"), "pi_disk": ("die Belegung der Pi-Speicherkarte", " Prozent"),
         "pi_load": ("die Last auf dem Pi", ""), "nas_temp": ("die Temperatur des NAS", " Grad"),
         "pc_gpu_temp": ("die Grafikkarte", " Grad"), "pc_disk": ("die Belegung der Systemplatte", " Prozent"),
         "nas_used": ("die Belegung des NAS", " Prozent")}


def config():
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    cfg["limits"] = dict(LIMITS, **(cfg.get("limits") or {}))
    return cfg


def _num(s):
    try:
        return float(str(s).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _say_num(v):
    return (f"{v:.1f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)).replace(".", ",")


# ── checks: each yields findings ─────────────────────────────
# finding: dict(key, ok, severity: critical|warn, down: text, up: text)

def check_ha():
    url, token = os.environ.get("HA_URL", "").rstrip("/"), os.environ.get("HA_TOKEN", "").strip()
    if not (url and token):
        return []
    try:
        req = urllib.request.Request(url + "/api/", headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=8) as r:
            ok = r.status == 200
    except Exception:  # noqa: BLE001
        ok = False
    return [dict(key="ha", ok=ok, severity="critical",
                 down="Home Assistant antwortet seit etwa {min} Minuten nicht. Licht und Steckdosen kann ich so nicht schalten.",
                 up="Home Assistant ist wieder erreichbar.")]


def parse_pi(out):
    """Fields from jarvis_mcp._pi_status() output."""
    grab = lambda pat: (m.group(1) if (m := re.search(pat, out)) else None)  # noqa: E731
    return dict(temp=_num(grab(r"temp: ([\d.]+)")), load=_num(grab(r"load: ([\d.]+)")),
                disk=_num(grab(r"disk: .*\((\d+)%\)")),
                services=re.findall(r"dienst (\S+): (\S+)", out),
                containers=re.findall(r"container (\S+): (.*)", out))


def check_pi(metrics):
    import jarvis_mcp as j
    if not j.PI_HOST:
        return []
    out = j._pi_status()
    down = out.startswith("Pi nicht erreichbar")
    found = [dict(key="pi", ok=not down, severity="critical",
                  down="Der Raspberry Pi ist seit etwa {min} Minuten nicht erreichbar.",
                  up="Der Raspberry Pi ist wieder erreichbar.")]
    if down:
        return found
    pi = parse_pi(out)
    for name, state in pi["services"]:
        found.append(dict(key=f"pi_service:{name}", ok=state == "active", severity="warn",
                          down=f"Auf dem Pi ist der Dienst {name} nicht aktiv ({state}).",
                          up=f"Der Dienst {name} auf dem Pi läuft wieder."))
    for name, status in pi["containers"]:
        bad = not status.startswith("Up") or "unhealthy" in status
        found.append(dict(key=f"pi_container:{name}", ok=not bad, severity="warn",
                          down=f"Auf dem Pi läuft der Container {name} nicht sauber ({status.strip()[:40]}).",
                          up=f"Der Container {name} läuft wieder."))
    metrics.update({k: v for k, v in (("pi_temp", pi["temp"]), ("pi_disk", pi["disk"]), ("pi_load", pi["load"]))
                    if v is not None})
    return found


def check_nas(metrics):
    import dashboard
    import jarvis_mcp as j
    if not (j.SYNO_USER and j.SYNO_PASSWORD):
        return []
    s = dashboard._cached("nas", 300, dashboard._nas)
    if s is None:                 # not fetched yet: no verdict
        return []
    ok = isinstance(s, dict) and not s.get("error")
    if ok and s.get("temp") is not None:
        metrics["nas_temp"] = s["temp"]
    if ok and s.get("used_pct") is not None:
        metrics["nas_used"] = s["used_pct"]
    return [dict(key="nas", ok=ok, severity="warn", down="Das NAS ist seit etwa {min} Minuten nicht erreichbar.",
                 up="Das NAS ist wieder erreichbar.")]


def _ping(host):
    try:
        p = subprocess.run(hostos.ping_args(host),
                           capture_output=True, text=True, timeout=8,
                           **hostos.QUIET)
        return p.returncode == 0 and "ttl=" in p.stdout.lower()
    except (OSError, subprocess.TimeoutExpired):
        return False


def _ha_state(entity):
    import briefing
    for s in briefing._ha_states():
        if s["entity_id"] == entity:
            return s["state"]
    return None


def check_devices(cfg):
    found = []
    for dev in cfg.get("devices") or []:
        name = dev.get("name") or dev.get("host") or dev.get("entity")
        if dev.get("host"):
            ok = _ping(dev["host"])
        elif dev.get("entity"):
            ok = _ha_state(dev["entity"]) in ("home", "on", "online", "connected")
        else:
            continue
        found.append(dict(key=f"device:{name}", ok=ok, severity="critical" if dev.get("critical") else "warn",
                          down=f"{name} ist seit etwa {{min}} Minuten nicht mehr im Netz.",
                          up=f"{name} ist wieder im Netz."))
    return found


# ── values: fixed limits and learned normal range ────────────

def _baseline():
    try:
        return json.loads(BASELINE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def value_findings(metrics, limits, now=None):
    """Limit and outlier findings; also stores one sample per metric and hour."""
    now = now or time.time()
    base = _baseline()
    found = []
    for key, val in metrics.items():
        label, unit = LABEL.get(key, (key, ""))
        hist = base.get(key) or {"samples": [], "last": 0}
        samples = hist["samples"]
        over = limits.get(key) is not None and val >= limits[key]
        odd = False
        if not over and len(samples) >= LEARN_SAMPLES:
            mean, sd = statistics.mean(samples), statistics.pstdev(samples)
            odd = val > mean + 3 * max(sd, 0.01) and val - mean >= MIN_DELTA.get(key, 0)
        text = (f"{label[0].upper() + label[1:]} liegt bei {_say_num(val)}{unit}"
                + (f", sonst um {_say_num(round(statistics.mean(samples), 1))}." if odd else "."))
        found.append(dict(key=f"value:{key}", ok=not (over or odd), severity="warn", down=text,
                          up=f"{label[0].upper() + label[1:]} ist wieder im normalen Bereich."))
        if now - hist.get("last", 0) >= 3600 and not (over or odd):     # outliers do not move the normal range
            samples.append(val)
            base[key] = {"samples": samples[-LEARN_SAMPLES:], "last": now}
    try:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(base), encoding="utf-8")
    except OSError:
        pass
    return found


# ── incidents → messages ─────────────────────────────────────

class Msg(str):
    """A message text that knows its severity (for the phone push)."""
    severity = "warn"


def _msg(text, severity):
    m = Msg(text)
    m.severity = severity
    return m


def _history(key, state, text, now):
    try:
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)), "key": key,
                                 "state": state, "text": text}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def history(limit=20):
    """Newest incidents first, for the HUD and 'was ist zuletzt ausgefallen?'."""
    try:
        rows = [json.loads(ln) for ln in HISTORY.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except (OSError, ValueError):
        return []
    return rows[-limit:][::-1]


def process(findings, now=None, quiet=None):
    """Updates the incident state and returns the messages to send now."""
    now = now or time.time()
    quiet = events.quiet_now() if quiet is None else quiet
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    out = []

    def mut(d):
        inc = d.setdefault("homelab", {})
        for f in findings:
            s = inc.setdefault(f["key"], {"bad": 0, "since": 0, "announced": False, "day": today, "sent": 0})
            if s.get("day") != today:
                s.update(day=today, sent=0)
            if f["ok"]:
                if s["bad"] >= CONFIRM:
                    _history(f["key"], "ok", f["up"], now)
                if s["announced"] and s["sent"] < MAX_PER_DAY + 1 and (f["severity"] == "critical" or not quiet):
                    out.append(_msg(f["up"], f["severity"]))
                    s["sent"] += 1
                s.update(bad=0, since=0, announced=False)
                continue
            s["bad"] += 1
            s["since"] = s["since"] or now
            if s["bad"] == CONFIRM:
                _history(f["key"], "bad", f["down"].replace("{min}", str(max(1, round((now - s["since"]) / 60)))), now)
            relevant = f["severity"] == "critical" or not quiet
            if s["bad"] >= CONFIRM and not s["announced"] and relevant and s["sent"] < MAX_PER_DAY:
                minutes = max(1, round((now - s["since"]) / 60))
                text = f["down"].replace("{min}", str(minutes))
                try:
                    import monitoring
                    n = monitoring.recurring(f["key"], now)
                    if n >= 3:
                        text += f" Das ist der {n}. Ausfall in 7 Tagen — wiederkehrend."
                except Exception:  # noqa: BLE001
                    pass
                out.append(_msg(text, f["severity"]))
                s["announced"] = True
                s["sent"] += 1
        return out
    return commands.update_state(mut)


_CHECK_LOCK = threading.Lock()


def check(cfg=None):
    with _CHECK_LOCK:           # the 5-min round and a Kuma alarm (kuma_alerts.py) must not count twice
        return _check(cfg)


def _check(cfg=None):
    cfg = cfg or config()
    metrics, findings = {}, []
    for fn in (check_ha, lambda: check_pi(metrics), lambda: check_nas(metrics), lambda: check_devices(cfg),
               lambda: check_pc(metrics), check_backup, check_memory_backup, lambda: check_situation(cfg)):
        try:
            findings += fn()
        except Exception as e:  # noqa: BLE001 — one dead source must not silence the rest
            log.warning("%s", str(e)[:300], exc_info=True)
    findings += value_findings({k: v for k, v in metrics.items() if k != "nas_used"}, cfg["limits"])
    try:
        import monitoring
        monitoring.record(metrics)
        findings += monitoring.forecast_findings()
    except Exception as e:  # noqa: BLE001
        log.warning("Prognose: %s", e)
    import infra
    for text in process(findings):
        events.publish("say", sir(text), kind="homelab", key=f"homelab:{_norm_key(text)}",
                       priority="critical" if getattr(text, "severity", "") == "critical" else "normal")
        infra.push_if_needed(sir(text), getattr(text, "severity", "warn"))
    return findings


def check_pc(metrics):
    import infra
    metrics.update({k: v for k, v in infra.pc_metrics().items() if k in ("pc_gpu_temp", "pc_disk")})
    return []


def check_situation(cfg):
    """Uptime Kuma, the Pi's security centre and the energy loop (situation.py)."""
    import situation
    return situation.findings(cfg)


_BACKUP = {"day": "", "finding": None}


def check_backup():
    """Once a day after 9 h: a failed Hyper Backup task becomes an incident until the next good run."""
    import infra
    import jarvis_mcp as j
    if not (j.SYNO_URL and j.SYNO_USER and j.SYNO_PASSWORD):
        return []
    today = time.strftime("%Y-%m-%d")
    if _BACKUP["day"] != today and time.localtime().tm_hour >= 9:
        text = infra.backup_status()
        _BACKUP.update(day=today, finding=dict(
            key="backup", ok=not infra.backup_failed(text), severity="warn",
            down="Ein Backup auf dem NAS ist fehlgeschlagen: " + text.splitlines()[0][:120] + " (seit {min} Minuten bekannt)",
            up="Die Backups auf dem NAS laufen wieder sauber."))
    f = _BACKUP["finding"]
    return [f, f] if f and not f["ok"] else ([f] if f else [])


def check_memory_backup():
    """The memory's pool on the NAS (memory_nas.py): too old or failed its weekly check."""
    import memory_nas
    f = memory_nas.finding()
    return [] if f is None else ([f, f] if not f["ok"] else [f])   # like check_backup: no second round needed


def _norm_key(text):
    return "".join(c for c in text.lower() if c.isalpha())[:80]


def sir(text):
    """'Der Pi …' → 'Sir, der Pi …' (nouns keep their capital)."""
    first, _, rest = text.partition(" ")
    if first in ("Der", "Die", "Das", "Auf"):
        first = first.lower()
    return f"Sir, {first} {rest}"


def start():
    if ENABLED:
        background.loop("homelab_watch", EVERY, check)
