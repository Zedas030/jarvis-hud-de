"""
Paired devices for remote access (iPhone, iPad in the hallway ...).

The HUD on the PC shows a six-digit code; the device types it on /pair.html
and gets a long-lived random token as a cookie. Only a SHA-256 of the token is
stored, in config/devices.json, so the file alone lets nobody in. Removing a
device from the HUD (or from that file) locks it out at once.

A pairing can also hand out a key for Home Assistant (kind "ask") instead of a cookie: it only
opens /api/ask and /api/hear, never the HUD, so a key stored in Home Assistant cannot read the dashboard.
"""
import hashlib
import json
import pathlib
import secrets
import threading
import time

PATH = pathlib.Path(__file__).resolve().parents[2] / "config" / "devices.json"
CODE_TTL = 300            # a pairing code lives five minutes
CODE_TRIES = 5            # wrong guesses before the code is burnt
COOKIE = "jarvis_device"
COOKIE_AGE = 365 * 24 * 3600

_LOCK = threading.Lock()
_CODE = {"code": None, "until": 0, "tries": 0}


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data):
    PATH.parent.mkdir(exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PATH)


def start_pairing():
    """New code, replaces any open one. -> (code, seconds valid)"""
    with _LOCK:
        _CODE.update(code=f"{secrets.randbelow(10**6):06d}", until=time.time() + CODE_TTL, tries=0)
        return _CODE["code"], CODE_TTL


def pairing_open():
    with _LOCK:
        return bool(_CODE["code"]) and time.time() < _CODE["until"]


def pair(code, name, kind="device"):
    """Right code -> token for the cookie or the Home Assistant key (the code is used up). Wrong -> None."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    with _LOCK:
        if not _CODE["code"] or time.time() >= _CODE["until"]:
            return None
        if not secrets.compare_digest(code, _CODE["code"]):
            _CODE["tries"] += 1
            if _CODE["tries"] >= CODE_TRIES:
                _CODE.update(code=None, until=0)
            return None
        _CODE.update(code=None, until=0)
        token = secrets.token_urlsafe(32)
        data = _load()
        entry = dict(name=(str(name or "").strip() or "Gerät")[:40], hash=_hash(token),
                     created=time.strftime("%Y-%m-%d %H:%M"), seen="")
        if kind != "device":
            entry["kind"] = kind
        data[secrets.token_hex(4)] = entry
        _save(data)
        return token


def check(token, kind="device"):
    """Device id for a valid token of that kind, else None. Notes when it was last seen."""
    if not token:
        return None
    h = _hash(token)
    with _LOCK:
        data = _load()
        for did, d in data.items():
            if secrets.compare_digest(d.get("hash", ""), h) and d.get("kind", "device") == kind:
                now = time.strftime("%Y-%m-%d %H:%M")
                if d.get("seen") != now:
                    d["seen"] = now
                    _save(data)
                return did
    return None


def listing():
    return [dict(id=did, name=d.get("name", ""), created=d.get("created", ""), seen=d.get("seen", ""),
                 kind=d.get("kind", "device"))
            for did, d in _load().items()]


def revoke(did):
    with _LOCK:
        data = _load()
        if data.pop(str(did), None) is None:
            return False
        _save(data)
        return True


def token_from_cookie(header):
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            return v
    return ""


def cookie_header(token):
    return f"{COOKIE}={token}; Path=/; Max-Age={COOKIE_AGE}; HttpOnly; Secure; SameSite=Strict"
