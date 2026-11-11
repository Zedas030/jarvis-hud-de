"""
The memory's long-term store on the NAS (Synology File Station API, the same login as the NAS tools).

Once a day, after the local vault backup: the whole vault as vault-YYYY-MM-DD.zip and the full-text index
(memory_index.sqlite, which keeps every exchange ever said) go to JARVIS_MEMORY_NAS_DIR. The PC copy stays
the working copy — SQLite over a network share breaks, and Jarvis must still remember when the NAS
sleeps — so the NAS is the pool that survives the PC: a dead disk, a reinstall, the move to the server.

  JARVIS_MEMORY_NAS        1 (default when SYNOLOGY_URL is set) / 0 = off
  JARVIS_MEMORY_NAS_DIR    folder on the NAS (/home/Jarvis-Gedaechtnis — the home of the Jarvis NAS user)
  JARVIS_MEMORY_NAS_KEEP   how many daily vault zips stay (90)

Every Wednesday the newest zip and the index are downloaded again and opened (zip test, SQLite integrity
check); a failed check or no upload for JARVIS_MEMORY_NAS_STALE_H (48) hours becomes a homelab incident
(homelab_watch.check_memory_backup), so a pool that quietly stopped working gets noticed.

Restore: copy memory_index.sqlite into logs/ and unpack the newest zip into vault/ (docs/gedaechtnis.md).
"""
import json
import os
import pathlib
import sqlite3
import tempfile
import time
import urllib.parse
import urllib.request
import uuid

import jarvis_log

log = jarvis_log.get(__name__)

URL = os.environ.get("SYNOLOGY_URL", "").strip().rstrip("/")
USER = os.environ.get("SYNOLOGY_USER", "").strip()
PASSWORD = os.environ.get("SYNOLOGY_PASSWORD", "")
ENABLED = os.environ.get("JARVIS_MEMORY_NAS", "1").strip().lower() not in ("0", "false", "no", "off")
FOLDER = os.environ.get("JARVIS_MEMORY_NAS_DIR", "/home/Jarvis-Gedaechtnis").strip().rstrip("/")
KEEP = int(os.environ.get("JARVIS_MEMORY_NAS_KEEP", "90") or 90)
STALE_H = float(os.environ.get("JARVIS_MEMORY_NAS_STALE_H", "48") or 48)   # older = the homelab watcher reports it
STATE = {"last_ok": "", "last_error": "", "files": []}


def enabled():
    return ENABLED and bool(URL and USER and PASSWORD and FOLDER)


def _call(path, params, sid=None, timeout=20):
    q = dict(params, **({"_sid": sid} if sid else {}))
    with urllib.request.urlopen(f"{URL}{path}?{urllib.parse.urlencode(q)}", timeout=timeout) as r:
        d = json.loads(r.read() or b"{}")
    if not d.get("success"):
        raise RuntimeError(f"NAS {params.get('api')}.{params.get('method')}: Fehler {(d.get('error') or {}).get('code')}")
    return d.get("data") or {}


def _login():
    return _call("/webapi/auth.cgi", {"api": "SYNO.API.Auth", "version": "6", "method": "login", "account": USER,
                                      "passwd": PASSWORD, "session": "FileStation", "format": "sid"})["sid"]


def _logout(sid):
    try:
        _call("/webapi/auth.cgi", {"api": "SYNO.API.Auth", "version": "6", "method": "logout",
                                   "session": "FileStation"}, sid)
    except (OSError, RuntimeError):
        pass


def _upload(sid, local, name):
    """multipart/form-data by hand (standard library only); the file part must come last."""
    boundary = "----jarvis" + uuid.uuid4().hex
    fields = {"api": "SYNO.FileStation.Upload", "version": "2", "method": "upload", "path": FOLDER,
              "create_parents": "true", "overwrite": "true"}
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n".encode() + pathlib.Path(local).read_bytes() + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{URL}/webapi/entry.cgi?_sid={urllib.parse.quote(sid)}", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read() or b"{}")
    if not d.get("success"):
        raise RuntimeError(f"Upload {name}: Fehler {(d.get('error') or {}).get('code')}")


def _listing(sid):
    try:
        d = _call("/webapi/entry.cgi", {"api": "SYNO.FileStation.List", "version": "2", "method": "list",
                                        "folder_path": FOLDER}, sid)
    except RuntimeError:
        return []
    return sorted(f["name"] for f in d.get("files") or [])


def _prune(sid, names):
    old = [n for n in names if n.startswith("vault-") and n.endswith(".zip")][:-KEEP] if KEEP > 0 else []
    if old:
        _call("/webapi/entry.cgi", {"api": "SYNO.FileStation.Delete", "version": "2", "method": "delete",
                                    "path": json.dumps([f"{FOLDER}/{n}" for n in old])}, sid, timeout=60)
    return old


def _index_copy(dest):
    """A consistent copy of the index even while it is being written (SQLite backup API)."""
    import memory_index
    src = sqlite3.connect(memory_index.PATH, timeout=10)
    try:
        out = sqlite3.connect(dest)
        with out:
            src.backup(out)
        out.close()
    finally:
        src.close()


def sync():
    """Uploads today's vault zip and the index. Returns a short German result."""
    if not enabled():
        return "NAS-Ablage aus (SYNOLOGY_URL/USER/PASSWORD oder JARVIS_MEMORY_NAS)."
    import memory_index
    import memory_tools
    memory_index.refresh(force=True)
    day = time.strftime("%Y-%m-%d")
    with tempfile.TemporaryDirectory(prefix="jarvis-nas-") as tmp:
        zip_path = memory_tools.export("vault-nas")
        idx = pathlib.Path(tmp) / "memory_index.sqlite"
        _index_copy(idx)
        sid = _login()
        try:
            _upload(sid, zip_path, f"vault-{day}.zip")
            _upload(sid, idx, "memory_index.sqlite")
            names = _listing(sid)
            removed = _prune(sid, names)
        finally:
            _logout(sid)
            try:
                pathlib.Path(zip_path).unlink()
            except OSError:
                pass
    STATE.update(last_ok=time.strftime("%Y-%m-%d %H:%M"), last_error="", files=names)
    _remember(last_ok=time.time())
    log.info("Gedächtnis aufs NAS gelegt (%s, %d Sicherungen, %d alte entfernt)", FOLDER, len(names), len(removed))
    return f"Gedächtnis auf dem NAS gesichert: {FOLDER}, {sum(n.startswith('vault-') for n in names)} Tagesstände."


# ── is the pool on the NAS any good? ────────────────────────────

def _remember(**kw):
    """Persisted in state.json: the homelab watcher must know the last good upload after a restart too."""
    import commands
    commands.update_state(lambda d: (d.setdefault("memory_nas", {}).update(kw), True)[1])


def saved_state():
    import commands
    return commands.load().get("memory_nas") or {}


def _download(sid, name, dest):
    q = urllib.parse.urlencode({"api": "SYNO.FileStation.Download", "version": "2", "method": "download",
                                "path": f"{FOLDER}/{name}", "mode": "download", "_sid": sid})
    with urllib.request.urlopen(f"{URL}/webapi/entry.cgi?{q}", timeout=120) as r:
        data = r.read()
    if data[:1] == b"{":                               # File Station answers errors as JSON
        raise RuntimeError(f"Download {name}: {data[:120].decode('utf-8', 'replace')}")
    pathlib.Path(dest).write_bytes(data)


def check_copy(zip_path, index_path):
    """Problems with a downloaded pair, or [] when both can be restored."""
    import zipfile
    problems = []
    try:
        with zipfile.ZipFile(zip_path) as z:
            bad = z.testzip()
            names = z.namelist()
        if bad:
            problems.append(f"Zip beschädigt ({bad})")
        elif not any(n.startswith("Erinnerungen/") for n in names):
            problems.append("im Zip fehlen die Erinnerungen")
    except (OSError, zipfile.BadZipFile) as e:
        problems.append(f"Zip unlesbar ({str(e)[:60]})")
    try:
        con = sqlite3.connect(f"file:{pathlib.Path(index_path).as_posix()}?mode=ro", uri=True)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            rows = con.execute("SELECT count(*) FROM mem").fetchone()[0]
        finally:
            con.close()
        if ok != "ok":
            problems.append(f"Index beschädigt ({ok[:60]})")
        elif not rows:
            problems.append("Index leer")
    except sqlite3.Error as e:
        problems.append(f"Index unlesbar ({str(e)[:60]})")
    return problems


def verify():
    """Downloads the newest zip and the index again and opens both. Returns the problems ([] = fine)."""
    sid = _login()
    try:
        names = _listing(sid)
        zips = [n for n in names if n.startswith("vault-") and n.endswith(".zip")]
        if not zips or "memory_index.sqlite" not in names:
            problems = ["auf dem NAS fehlt die Sicherung"]
        else:
            with tempfile.TemporaryDirectory(prefix="jarvis-nas-check-") as tmp:
                z, i = pathlib.Path(tmp) / "v.zip", pathlib.Path(tmp) / "i.sqlite"
                _download(sid, zips[-1], z)
                _download(sid, "memory_index.sqlite", i)
                problems = check_copy(z, i)
    finally:
        _logout(sid)
    _remember(verified=time.time(), verify_problems=problems)
    log.info("Gedächtnis-Sicherung auf dem NAS geprüft: %s", "; ".join(problems) or "in Ordnung")
    return problems


def finding(now=None):
    """For homelab_watch: one incident while the NAS pool is older than STALE_H or failed its check."""
    if not enabled():
        return None
    st = saved_state()
    now = now or time.time()
    last = st.get("last_ok") or 0
    if not last:
        return None                                   # never uploaded yet: nothing to compare against
    hours = (now - last) / 3600
    problems = st.get("verify_problems") or []
    ok = hours <= STALE_H and not problems
    why = (f"die letzte Sicherung ist {round(hours / 24)} Tage alt" if hours > STALE_H
           else "die Prüfung meldet: " + "; ".join(problems))
    return dict(key="memory_nas", ok=ok, severity="warn",
                down=f"Die Sicherung des Gedächtnisses auf dem NAS hakt: {why} (seit {{min}} Minuten bekannt).",
                up="Die Sicherung des Gedächtnisses auf dem NAS läuft wieder.")


def start():
    import background
    import commands

    def step():
        if enabled() and time.localtime().tm_wday == 2 and saved_state().get("last_ok")                 and background.claim("memory-nas-verify", background.week_key()):
            try:
                verify()
            except (OSError, RuntimeError) as e:
                _remember(verified=time.time(), verify_problems=[f"Prüfung nicht möglich ({str(e)[:80]})"])
        if not enabled() or not background.claim("memory-nas", time.strftime("%Y-%m-%d")):
            return
        try:
            sync()
        except (OSError, RuntimeError, sqlite3.Error) as e:
            STATE["last_error"] = str(e)[:160]
            log.warning("Gedächtnis aufs NAS fehlgeschlagen: %s", STATE["last_error"])
            # NAS asleep or offline: give the day back, the next hourly round tries again
            commands.update_state(lambda d: (d.setdefault("jobs", {}).__setitem__("memory-nas", None), True)[1])
    background.loop("memory_nas", 3600, step, pausable=False)
