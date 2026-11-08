"""
Maintenance calendar for the homelab: not what is broken (homelab_watch), but what will need a
hand soon — all read-only, 0 tokens, once a day.

  certificates   TLS certificates of the hosts in config/homelab.json → maintenance.certs
                 ("host:port"): days until they expire. Verified when possible; self-signed
                 ones are read from the raw certificate.
  docker images  on the Pi: how old the image of every running container is (days since it
                 was built) — older than docker_max_days wants a pull, unless the registry
                 says the tag still points to that very image (upstream has not released since)
  backups        the last Hyper Backup run on the NAS (infra.backup_status): older than
                 backup_max_days is a finding
  NAS disks      a disk that is not "normal" (the dashboard's reading)

  items()        [{what, days, severity, text}] sorted by urgency
  describe()     the spoken calendar, also the tool maintenance_status
  step()         daily from 9 h: one notice per item that is due (watchers-style, once a day)

config/homelab.json:
  "maintenance": {"certs": ["homeassistant.local:8123", "nas.local:5001"], "cert_warn_days": 14,
                  "docker_max_days": 120, "backup_max_days": 3}
"""
import datetime as dt
import os
import re
import socket
import ssl
import subprocess
import time

import background
import events
import hostos

ENABLED = os.environ.get("JARVIS_MAINTENANCE", "1").strip() not in ("0", "false", "no", "off")
FROM_HOUR = 9
DEFAULTS = {"cert_warn_days": 14, "docker_max_days": 120, "backup_max_days": 3}
# 'image <container> <created> <image> <repo digests, comma-separated>'. Looked up by the container's
# image id: an image pulled by digest has no local tag, so 'docker image inspect <tag>' would fail.
DOCKER_AGES_CMD = ("command -v docker >/dev/null && docker ps --format '{{.Names}}' | while read n; do "
                   "id=$(docker inspect --format '{{.Image}}' \"$n\" 2>/dev/null); "
                   "echo \"image $n $(docker image inspect --format '{{.Created}}' \"$id\" 2>/dev/null) "
                   "$(docker inspect --format '{{.Config.Image}}' \"$n\" 2>/dev/null) "
                   "$(docker image inspect --format '{{join .RepoDigests \",\"}}' \"$id\" 2>/dev/null)\"; done; true")
_CACHE = {"day": "", "items": []}


def config():
    import homelab_watch
    cfg = dict(DEFAULTS)
    cfg.update(homelab_watch.config().get("maintenance") or {})
    return cfg


# ── certificates ─────────────────────────────────────────────

def _der_not_after(der):
    """notAfter from a DER certificate without any library: the second time value in the
    certificate (notBefore comes first). UTCTime (0x17) or GeneralizedTime (0x18)."""
    found = []
    i = 0
    while i < len(der) - 2 and len(found) < 2:
        tag, ln = der[i], der[i + 1]
        if tag in (0x17, 0x18) and ln in (13, 15):
            raw = der[i + 2:i + 2 + ln].decode("ascii", "replace")
            try:
                found.append(dt.datetime.strptime(raw, "%y%m%d%H%M%SZ" if tag == 0x17 else "%Y%m%d%H%M%SZ"))
            except ValueError:
                pass
            i += 2 + ln
        else:
            i += 1
    return found[1] if len(found) == 2 else None


def cert_days(host, port=443, timeout=6):
    """Days until the certificate of host:port expires (negative = expired), or None."""
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                not_after = tls.getpeercert().get("notAfter")
        expires = dt.datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after))
    except ssl.SSLCertVerificationError:
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
        expires = _der_not_after(der or b"")
        if expires is None:
            return None
    except (OSError, ValueError):
        return None
    return (expires - dt.datetime.now()).days


def certificates(cfg):
    out = []
    for spec in cfg.get("certs") or []:
        host, _, port = str(spec).partition(":")
        days = cert_days(host.strip(), int(port) if port.isdigit() else 443)
        if days is None:
            out.append(dict(what=f"Zertifikat {host}", days=None, severity="info",
                            text=f"Das Zertifikat von {host} konnte ich nicht lesen."))
            continue
        due = days <= int(cfg.get("cert_warn_days", 14))
        text = (f"Das Zertifikat von {host} ist seit {-days} Tagen abgelaufen." if days < 0 else
                f"Das Zertifikat von {host} läuft in {days} Tagen ab." if due else
                f"Zertifikat {host}: noch {days} Tage.")
        out.append(dict(what=f"Zertifikat {host}", days=days, severity="warn" if due else "ok", text=text))
    return out


# ── docker images on the Pi ──────────────────────────────────

def _pi(cmd):
    import jarvis_mcp as j
    if not j.PI_HOST:
        return ""
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", f"{j.PI_USER}@{j.PI_HOST}", cmd],
                         capture_output=True, text=True, timeout=40, **hostos.QUIET)
    return out.stdout if out.returncode == 0 or out.stdout.strip() else ""


def parse_image_rows(text, now=None):
    """[(container, days old, image, {repo digests})] from DOCKER_AGES_CMD's lines."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []
    for m in re.finditer(r"^image (\S+) (\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})\S*(?: (\S+))?(?: (\S+))?", text, re.M):
        created = dt.datetime.fromisoformat(f"{m.group(2)}T{m.group(3)}+00:00")
        digests = {d.rpartition("@")[2] for d in (m.group(5) or "").split(",") if "@" in d}
        out.append((m.group(1), (now - created).days, m.group(4) or "", digests))
    return out


def parse_images(text, now=None):
    """[(container, days old)] from 'image <name> <created ISO> ...' lines."""
    return [(name, days) for name, days, _, _ in parse_image_rows(text, now)]


def _still_newest(image, have, digest=None):
    """True when the tag in the registry still points to the image the Pi runs: then there is
    nothing to pull, however old it is. Pinned images, local builds (no repo digest) and any
    registry error give False — then the age warning stands."""
    if not image or "@" in image or not have:
        return False
    if digest is None:
        import updates
        digest = updates._registry_digest
    try:
        return digest(image) in have
    except Exception:  # noqa: BLE001
        return False


def images(cfg, digest=None):
    out = []
    limit = int(cfg.get("docker_max_days", 120))
    for name, days, image, have in parse_image_rows(_pi(DOCKER_AGES_CMD)):
        old = days > limit
        if old and _still_newest(image, have, digest):
            out.append(dict(what=f"Container {name}", days=None, severity="ok",
                            text=f"Container {name}: Image {days} Tage alt, aber schon die neueste Fassung — "
                                 "es gibt nichts zu ziehen."))
            continue
        out.append(dict(what=f"Container {name}", days=limit - days, severity="warn" if old else "ok",
                        text=(f"Das Image von {name} auf dem Pi ist {days} Tage alt — ein Pull wäre fällig." if old
                              else f"Container {name}: Image {days} Tage alt.")))
    return out


# ── backups and disks ────────────────────────────────────────

def backup_age(text, today=None):
    """Days since the newest 'zuletzt <date>' in infra.backup_status()'s text, or None."""
    today = today or dt.date.today()
    dates = []
    for m in re.finditer(r"zuletzt (\d{4}-\d{2}-\d{2})", text or ""):
        try:
            dates.append(dt.date.fromisoformat(m.group(1)))
        except ValueError:
            pass
    for m in re.finditer(r"zuletzt (\d{1,2})\.(\d{1,2})\.(\d{4})", text or ""):
        dates.append(dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
    return (today - max(dates)).days if dates else None


def backups(cfg):
    import infra
    import jarvis_mcp as j
    if not (j.SYNO_URL and j.SYNO_USER and j.SYNO_PASSWORD):
        return []
    text = infra.backup_status()
    age = backup_age(text)
    if age is None:
        return []
    limit = int(cfg.get("backup_max_days", 3))
    old = age > limit
    return [dict(what="Backup NAS", days=limit - age, severity="warn" if old else "ok",
                 text=(f"Das letzte Backup auf dem NAS ist {age} Tage her." if old
                       else f"Backup auf dem NAS: zuletzt vor {age} Tag(en)."))]


def disks():
    import dashboard
    s = dashboard._cached("nas", 300, dashboard._nas)
    if not isinstance(s, dict) or s.get("error") or s.get("ok") is None:
        return []
    return [dict(what="NAS-Platten", days=0 if not s["ok"] else None, severity="warn" if not s["ok"] else "ok",
                 text="Eine Platte oder ein Volume im NAS ist nicht normal — SMART prüfen." if not s["ok"]
                 else "NAS-Platten: alle normal.")]


# ── all together ─────────────────────────────────────────────

def items(now=None):
    now = now or dt.datetime.now()
    today = now.date().isoformat()
    if _CACHE["day"] == today:
        return _CACHE["items"]
    cfg = config()
    found = []
    for fn in (lambda: certificates(cfg), lambda: images(cfg), lambda: backups(cfg), disks):
        try:
            found += fn()
        except Exception as e:  # noqa: BLE001 — one source down must not hide the rest
            print(f"  maintenance: {e}", flush=True)
    order = {"warn": 0, "info": 1, "ok": 2}
    found.sort(key=lambda i: (order.get(i["severity"], 3), i["days"] if i["days"] is not None else 9999))
    _CACHE.update(day=today, items=found)
    return found


def describe(now=None):
    rows = items(now)
    if not rows:
        return ("Noch nichts im Wartungskalender, Sir. Zertifikate, Container und Backups trage ich ein, sobald "
                "config/homelab.json unter maintenance etwas nennt und Pi oder NAS erreichbar sind.")
    due = [r for r in rows if r["severity"] == "warn"]
    fine = [r for r in rows if r["severity"] == "ok"]
    out = []
    if due:
        out.append("Fällig: " + " ".join(r["text"] for r in due[:5]))
    if fine:
        soon = sorted((r for r in fine if r["days"] is not None), key=lambda r: r["days"])[:3]
        out.append("In Ordnung: " + ", ".join(r["text"].rstrip(".") for r in soon) + ".")
    unknown = [r for r in rows if r["severity"] == "info"]
    if unknown:
        out.append(" ".join(r["text"] for r in unknown[:3]))
    return " ".join(out)


def step(now=None):
    now = now or dt.datetime.now()
    if now.hour < FROM_HOUR or not background.claim("maintenance", now.date().isoformat()):
        return
    import watchers
    for r in items(now):
        if r["severity"] == "warn":
            watchers.once(f"maintenance:{r['what']}", "Sir, " + r["text"][0].lower() + r["text"][1:], kind="systems")


_ASK = re.compile(r"\b(wartung|wartungskalender|was (steht|ist) (bald |demnächst )?(fällig|an im homelab)|"
                  r"zertifikat\w*|was läuft (bald )?ab|wie alt (sind|ist) .{0,20}(container|image|backup))\b")


def handle(n):
    return describe() if _ASK.search(n) else None


def start():
    if ENABLED:
        background.loop("maintenance", 1800, step)
