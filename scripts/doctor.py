"""
Doctor: is this Jarvis installation healthy? Read-only, changes nothing, no Claude call.

    .venv\\Scripts\\python.exe scripts\\doctor.py [--json]

Checks Python and the venv, the packages from the requirements files, .env, the config files,
the Claude CLI, the voice models, the running server with its ports, Home Assistant and free
disk space. Exit code 1 if something failed. Values from .env are never printed.

Idea from `jarvis doctor` in OpenJarvis (github.com/open-jarvis/OpenJarvis, Apache-2.0);
written from scratch for this project.
"""
import json
import os
import pathlib
import re
import shutil
import socket
import sys
import urllib.error
import urllib.request
from importlib import metadata

ROOT = pathlib.Path(__file__).resolve().parent.parent
OK, WARN, FAIL = "ok", "warn", "fail"


def read_env(path):
    """KEY=value pairs of a .env file, quotes stripped; nothing goes into os.environ."""
    out = {}
    try:
        lines = pathlib.Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def requirements(path):
    """[(name, pinned version or '')] from a requirements file."""
    out = []
    try:
        lines = pathlib.Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.split("#", 1)[0].split(";", 1)[0].strip()
        m = re.match(r"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*(?:==\s*([^\s,]+))?", line)
        if line and not line.startswith("-") and m:
            out.append((m.group(1), m.group(2) or ""))
    return out


def check_python(version=sys.version_info, prefix=sys.prefix, base=sys.base_prefix):
    text = f"{version[0]}.{version[1]}.{version[2]}"
    if tuple(version[:2]) < (3, 12):
        return FAIL, "Python", f"{text} — gebraucht wird 3.12"
    if prefix == base:
        return WARN, "Python", f"{text}, aber nicht aus der .venv gestartet"
    return OK, "Python", f"{text} aus der .venv"


def check_packages(files, installed=None):
    """One result for all requirement files: what is missing, what differs from its pin."""
    installed = installed or (lambda name: metadata.version(name))
    missing, differ, count = [], [], 0
    for path in files:
        for name, pin in requirements(path):
            count += 1
            try:
                have = installed(name)
            except metadata.PackageNotFoundError:
                missing.append(name)
                continue
            if pin and have != pin:
                differ.append(f"{name} {have} statt {pin}")
    if missing:
        return FAIL, "Pakete", "fehlen: " + ", ".join(missing)
    if differ:
        return WARN, "Pakete", "andere Version: " + ", ".join(differ)
    return OK, "Pakete", f"{count} aus den Requirements vorhanden"


def check_env(env, exists=True):
    if not exists:
        return [(FAIL, ".env", "fehlt — .env.example kopieren und ausfüllen")]
    out = []
    empty = [k for k in ("HA_URL", "HA_TOKEN") if not env.get(k)]
    out.append((WARN, ".env", "leer: " + ", ".join(empty) + " — ohne sie kein Home Assistant") if empty
               else (OK, ".env", f"{len(env)} Einträge, Home Assistant eingetragen"))
    if env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"):
        out.append((FAIL, "ANTHROPIC_API_KEY", "ist gesetzt — Jarvis läuft über das Abo; mit dem Schlüssel wird je Token abgerechnet"))
    return out


def check_configs(folder):
    """Every live config file must parse (examples and .bak copies are skipped)."""
    bad, count = [], 0
    for path in sorted(pathlib.Path(folder).glob("*")):
        if ".bak" in path.name or ".example." in path.name or path.suffix not in (".json", ".yaml", ".yml"):
            continue
        count += 1
        try:
            text = path.read_text(encoding="utf-8-sig")
            if path.suffix == ".json":
                json.loads(text)
            else:
                import yaml
                yaml.safe_load(text)
        except ImportError:
            count -= 1
        except Exception as e:     # noqa: BLE001 - any parser error is the finding
            bad.append(f"{path.name} ({str(e)[:60]})")
    if bad:
        return FAIL, "Konfiguration", "nicht lesbar: " + "; ".join(bad)
    return OK, "Konfiguration", f"{count} Dateien in config/ lesbar"


def check_state(path):
    path = pathlib.Path(path)
    if not path.exists():
        return OK, "state.json", "noch nicht angelegt"
    try:
        json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return FAIL, "state.json", f"beschädigt ({str(e)[:60]})"
    return OK, "state.json", f"lesbar, {path.stat().st_size // 1024} kB"


def check_files(root):
    out = [(OK, "Claude CLI", shutil.which("claude")) if shutil.which("claude")
           else (FAIL, "Claude CLI", "`claude` nicht im PATH — ohne ihn antwortet nur der Notbetrieb")]
    for label, rel, level in (("Silero VAD", "models/silero_vad.onnx", WARN), ("Piper-Stimme", "models/piper/*.onnx", WARN)):
        hit = sorted(pathlib.Path(root).glob(rel))
        out.append((OK, label, hit[0].name) if hit else (level, label, f"{rel} fehlt (scripts/fetch_voice_models.py)"))
    out.append((OK, "Grafikkarte", "nvidia-smi gefunden") if shutil.which("nvidia-smi")
               else (WARN, "Grafikkarte", "kein nvidia-smi — Whisper läuft dann auf der CPU"))
    free = shutil.disk_usage(root).free / 2 ** 30
    out.append((WARN if free < 5 else OK, "Speicherplatz", f"{free:.0f} GB frei"))
    return out


def port_open(port, host="127.0.0.1", timeout=1.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_status(url, token="", timeout=4.0):
    """HTTP status, or the error as text."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (OSError, ValueError) as e:
        return str(getattr(e, "reason", e))[:60]


def check_running(env, port_open=port_open, http_status=http_status):
    port = int(env.get("JARVIS_PORT") or 8720)
    out = []
    if not port_open(port):
        return [(WARN, "Jarvis", f"läuft nicht (Port {port} zu) — start-jarvis.bat")]
    status = http_status(f"http://127.0.0.1:{port}/metrics")
    out.append((OK, "Jarvis", f"läuft auf Port {port}") if status in (200, 404)
               else (FAIL, "Jarvis", f"Port {port} offen, antwortet aber nicht ({status})"))
    out.append((OK, "Sprach-Engine", "Port 8724 offen") if port_open(8724)
               else (WARN, "Sprach-Engine", "Port 8724 zu — iPad, Handy und Satelliten erreichen sie nicht"))
    return out


def check_home_assistant(env, http_status=http_status):
    url, token = (env.get("HA_URL") or "").rstrip("/"), env.get("HA_TOKEN") or ""
    if not url or not token:
        return WARN, "Home Assistant", "nicht eingetragen"
    status = http_status(url + "/api/", token)
    if status == 200:
        return OK, "Home Assistant", "erreichbar, Token gültig"
    if status == 401:
        return FAIL, "Home Assistant", "erreichbar, aber der Token wird abgelehnt (HA_TOKEN in .env erneuern)"
    return FAIL, "Home Assistant", f"nicht erreichbar ({status})"


def run(root=ROOT):
    root = pathlib.Path(root)
    env = read_env(root / ".env")
    reqs = [root / "requirements.txt"] + ([root / "requirements-windows.txt"] if os.name == "nt" else [])
    return [check_python(), check_packages(reqs), *check_env(env, (root / ".env").exists()),
            check_configs(root / "config"), check_state(env.get("JARVIS_STATE") or root / "state.json"),
            *check_files(root), *check_running(env), check_home_assistant(env)]


def main(argv):
    results = run()
    if "--json" in argv:
        print(json.dumps([dict(status=s, check=c, detail=d) for s, c, d in results], ensure_ascii=False, indent=1))
    else:
        mark = {OK: "  ok  ", WARN: " WARN ", FAIL: "FEHLER"}
        for s, c, d in results:
            print(f"[{mark[s]}] {c:18} {d}")
        fails, warns = (sum(1 for r in results if r[0] == k) for k in (FAIL, WARN))
        print(f"\n{len(results)} Prüfungen: {fails} Fehler, {warns} Warnungen.")
    return 1 if any(r[0] == FAIL for r in results) else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(main(sys.argv[1:]))
