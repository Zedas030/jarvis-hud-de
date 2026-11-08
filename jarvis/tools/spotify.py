"""
Spotify on every device of the account — the phone on the Bluetooth speaker as much as the PC — through
the Web API (Spotify Connect). Two jobs:

  hold / release   "Jarvis" (and every time he speaks) turns the music down to a third of its volume, at
                   most QUIET_PCT (JARVIS_SPOTIFY_HOLD_MODE=leise, the default) or pauses it (=pause), so he hears the request clearly; once he is done the
                   old volume comes back, or it plays on — only if he did it himself, and never after
                   "Musik aus" in between. A device Spotify cannot set the volume of is paused instead.
  control          play, pause, next, previous for the music tool, on whatever device is playing.
  transfer         the music into another room: playback moves to the Spotify Connect device of that
                   name (Sonos, Echo, PC) and plays on there — "Musik vom Schlafzimmer ins Wohnzimmer".

The media keys in jarvis_mcp only reach Spotify on this PC; with this module set up the music tool uses
the Web API instead and falls back to the keys when it fails.

One-time setup (docs/installation.md, "Konfiguration" → Spotify): an app on developer.spotify.com with the redirect URI below, its
Client ID in JARVIS_SPOTIFY_CLIENT_ID, then  .venv\\Scripts\\python.exe scripts\\spotify_login.py
Needs Premium (a family plan member is fine): Spotify refuses playback control for free accounts.
"""
import json
import os
import pathlib
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
CLIENT_ID = os.environ.get("JARVIS_SPOTIFY_CLIENT_ID", "").strip()
TOKEN_FILE = pathlib.Path(os.environ.get("JARVIS_SPOTIFY_TOKEN", ROOT / "config" / "spotify_token.json"))
HOLD = os.environ.get("JARVIS_SPOTIFY_HOLD", "1").strip().lower() not in ("0", "off", "aus", "false", "no", "nein")
# Plays on by itself after this long, should the HUD never say "done" (tab closed, network gone).
HOLD_MAX_S = float(os.environ.get("JARVIS_SPOTIFY_HOLD_MAX_S", "120"))
# "leise": the music keeps running at QUIET_FACTOR of its volume, never above QUIET_PCT; "pause": it stops.
# Relative because a fixed 20 % did nothing on a speaker whose normal level is 15 %.
HOLD_MODE = "pause" if os.environ.get("JARVIS_SPOTIFY_HOLD_MODE", "leise").strip().lower() in ("pause", "stopp") else "leise"
QUIET_PCT = max(0, min(100, int(os.environ.get("JARVIS_SPOTIFY_QUIET_PCT", "20"))))
QUIET_FACTOR = max(0.0, min(1.0, float(os.environ.get("JARVIS_SPOTIFY_QUIET_FACTOR", "0.3").replace(",", "."))))
REDIRECT = "http://127.0.0.1:8722/callback"    # Spotify allows loopback IPs, no longer "localhost"
SCOPES = "user-read-playback-state user-modify-playback-state"
API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
TIMEOUT = 4
# A hold must outlive a restart of Jarvis (code change, .env): before, the restart forgot it and the music
# stayed paused. And what hold/release did, with Spotify's answers — the HUD's own log is out of reach on the iPad.
HELD_FILE = pathlib.Path(os.environ.get("JARVIS_SPOTIFY_HELD", ROOT / "logs" / "spotify_held.json"))
LOG = pathlib.Path(os.environ.get("JARVIS_SPOTIFY_LOG", ROOT / "logs" / "spotify.log"))
LOG_KEEP = 300

_LOCK = threading.RLock()
_ACCESS = {"token": "", "until": 0.0}
_HELD = {"device": None, "at": 0.0, "timer": None, "volume": None, "quiet": None}
STATUS = {"error": "", "holds": 0}


def _log(text):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text}\n"
    with _LOCK:
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            try:
                lines = LOG.read_text(encoding="utf-8").splitlines(keepends=True)[-(LOG_KEEP - 1):]
            except FileNotFoundError:
                lines = []
            LOG.write_text("".join(lines) + line, encoding="utf-8")
        except OSError:
            pass


def configured():
    return bool(CLIENT_ID) and TOKEN_FILE.exists()


def status():
    return dict(STATUS, configured=configured(), hold=HOLD, hold_mode=HOLD_MODE, held=_HELD["device"] is not None)


def save_token(data):
    """The refresh token from scripts/spotify_login.py, or a rotated one from a refresh."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"refresh_token": data["refresh_token"]}), encoding="utf-8")


def _refresh():
    try:
        refresh = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))["refresh_token"]
    except (OSError, ValueError, KeyError) as e:
        raise RuntimeError("kein Spotify-Login (scripts/spotify_login.py)") from e
    body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh,
                                   "client_id": CLIENT_ID}).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.loads(r.read())
    if data.get("refresh_token") and data["refresh_token"] != refresh:
        save_token(data)                      # PKCE refresh tokens rotate; the old one stops working
    _ACCESS.update(token=data["access_token"], until=time.time() + int(data.get("expires_in", 3600)) - 60)


def _api(method, path, retry=True, body=None):
    """(HTTP status, JSON or None). 204 is Spotify's "done" and also "no device active"."""
    with _LOCK:
        if not _ACCESS["token"] or time.time() > _ACCESS["until"]:
            _refresh()
        token = _ACCESS["token"]
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        headers["content-type"] = "application/json"
    req = urllib.request.Request(API + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None
                                 else b"" if method in ("PUT", "POST") else None)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
            try:
                return r.status, (json.loads(raw) if raw.strip() else None)
            except ValueError:            # pause/play answer 200 with a bare text body, not JSON
                return r.status, None
    except urllib.error.HTTPError as e:
        if e.code == 401 and retry:
            _ACCESS["token"] = ""
            return _api(method, path, retry=False, body=body)   # the retry must carry the same request
        return e.code, None


def _ok(code):
    return code in (200, 202, 204)


def play_context(uri, device=None):
    """Start a playlist/album/artist (spotify:playlist:…) on the active device. True when Spotify took it."""
    if not configured() or not str(uri or "").startswith("spotify:"):
        return False
    with _LOCK:
        if not _ACCESS["token"] or time.time() > _ACCESS["until"]:
            _refresh()
        token = _ACCESS["token"]
    req = urllib.request.Request(API + "/me/player/play" + _device_q(device), method="PUT",
                                 data=json.dumps({"context_uri": uri}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return _ok(r.status)
    except urllib.error.HTTPError as e:
        STATUS["error"] = f"play_context {e.code}"
        return False
    except OSError:
        return False


def _device_q(device):
    return "?device_id=" + urllib.parse.quote(device) if device else ""


def _player():
    """(is playing, device id, volume or None if Spotify cannot set it) of the active device."""
    code, state = _api("GET", "/me/player")
    if code != 200 or not state:
        return False, None, None
    dev = state.get("device") or {}
    vol = dev.get("volume_percent") if dev.get("supports_volume", True) else None
    return bool(state.get("is_playing")), dev.get("id"), vol


def active_device():
    """(is playing, {id, name, type}) of the account's active device; (False, {}) when nothing is active."""
    code, state = _api("GET", "/me/player")
    if code != 200 or not state:
        return False, {}
    dev = state.get("device") or {}
    return bool(state.get("is_playing")), {k: dev.get(k) for k in ("id", "name", "type")}


def playing():
    """(is playing, device id) of the account's active device; (False, None) when nothing is active."""
    on, device, _ = _player()
    return on, device


def _set_volume(pct, device):
    q = f"?volume_percent={int(pct)}" + ("&device_id=" + urllib.parse.quote(device) if device else "")
    return _ok(_api("PUT", "/me/player/volume" + q)[0])


def quiet_target(vol):
    """The volume while Jarvis talks: a share of the current one, capped, but never fully silent."""
    return max(1, min(QUIET_PCT, round(vol * QUIET_FACTOR)))


def hold(who=""):
    """Wake word: pause what plays so the microphone hears the request, remember to play on."""
    if not (HOLD and configured()):
        return False
    with _LOCK:
        try:
            if _HELD["device"] is not None:           # a second "Jarvis" in the same breath
                _arm_timer()
                return True
            on, device, vol = _player()
            if not on:
                _log(f"{who or '?'}: Jarvis gerufen, es lief keine Musik")
                return False
            target = quiet_target(vol) if vol is not None else None
            if HOLD_MODE == "leise" and vol is not None and target < vol:
                if _set_volume(target, device):
                    _HELD.update(device=device or "", at=time.time(), volume=vol, quiet=target)
                    STATUS.update(error="", holds=STATUS["holds"] + 1)
                    _save_held()
                    _arm_timer()
                    _log(f"{who or '?'}: leiser {vol} % → {target} % ({(device or '?')[:8]})")
                    return True
                _log(f"{who or '?'}: Lautstärke nicht setzbar, pausiere stattdessen")
            elif HOLD_MODE == "leise" and vol is not None:
                _log(f"{who or '?'}: läuft schon leise ({vol} %), nichts zu tun")
                return False
            code, _ = _api("PUT", "/me/player/pause" + _device_q(device))
            if not _ok(code):
                STATUS["error"] = f"Pause: HTTP {code}"
                _log(STATUS["error"])
                return False
            _HELD.update(device=device or "", at=time.time(), volume=None, quiet=None)
            STATUS.update(error="", holds=STATUS["holds"] + 1)
            _save_held()
            _arm_timer()
            _log(f"{who or '?'}: Pause ({(device or '?')[:8]})")
            return True
        except Exception as e:  # noqa: BLE001 — offline or no login: the request still works, just louder
            STATUS["error"] = str(e)[:160]
            _log("Pause fehlgeschlagen: " + STATUS["error"])
            return False


def _save_held():
    try:
        HELD_FILE.parent.mkdir(parents=True, exist_ok=True)
        HELD_FILE.write_text(json.dumps({"device": _HELD["device"], "at": _HELD["at"], "volume": _HELD["volume"],
                                         "quiet": _HELD["quiet"]}), encoding="utf-8")
    except OSError:
        pass


def _restore():
    """After a restart: take the hold back, or play on at once when it has run out meanwhile."""
    try:
        held = json.loads(HELD_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    _HELD.update(device=held.get("device") or "", at=float(held.get("at") or 0), volume=held.get("volume"),
                 quiet=held.get("quiet", 1 if held.get("volume") is not None else None))
    _log(("Leiser" if _HELD["volume"] is not None else "Pause") + " nach Neustart übernommen")
    _arm_timer(max(1.0, HOLD_MAX_S - (time.time() - _HELD["at"])))


def _arm_timer(seconds=None):
    if _HELD["timer"]:
        _HELD["timer"].cancel()
    _HELD["timer"] = threading.Timer(seconds or HOLD_MAX_S, release)
    _HELD["timer"].daemon = True
    _HELD["timer"].start()


def forget():
    """The user said what the music should do ("Musik aus", "nächster Titel") — no playing on after that."""
    with _LOCK:
        if _HELD["timer"]:
            _HELD["timer"].cancel()
        _HELD.update(device=None, at=0.0, timer=None, volume=None, quiet=None)
        HELD_FILE.unlink(missing_ok=True)


def release(who="", why=""):
    """Jarvis is done: the old volume back, or play on where the wake word paused it. Nothing if he
    did not do either."""
    with _LOCK:
        device, since, vol, quiet = _HELD["device"], _HELD["at"], _HELD["volume"], _HELD["quiet"]
        forget()
        if device is None:
            return False
        held_s = f"{who or 'Zeitlimit'}: weiter nach {time.time() - since:.0f} s" + (f" (wartete auf: {why})" if why else "")
        try:
            if vol is not None:
                return _loud_again(device, vol, quiet, held_s)
            # Someone started music elsewhere meanwhile: leave it. The paused iPhone itself said "playing"
            # 19 s after the pause and stayed silent — so on that device a play is sent regardless.
            on, now = playing()
            if on and now and device and now != device:
                _log(f"{held_s}: läuft schon auf einem anderen Gerät")
                return True
            code, _ = _api("PUT", "/me/player/play" + _device_q(device))
            if not _ok(code) and device:              # the phone went to sleep and left the device list
                code, _ = _api("PUT", "/me/player/play")
            STATUS["error"] = "" if _ok(code) else f"Weiter: HTTP {code}"
            _log(f"{held_s}: " + ("ok" if _ok(code) else f"HTTP {code} (iPhone eingeschlafen?)"))
            return _ok(code)
        except Exception as e:  # noqa: BLE001
            STATUS["error"] = str(e)[:160]
            _log(f"{held_s}, fehlgeschlagen: " + STATUS["error"])
            return False


def _loud_again(device, vol, quiet, held_s):
    # Turned up or down by hand meanwhile: that is the user's volume now, it stays.
    _, now, current = _player()
    target = QUIET_PCT if quiet is None else quiet
    if now == device and current is not None and min(abs(current - target), abs(current - vol)) > 1:
        _log(f"{held_s}: Lautstärke inzwischen von Hand auf {current} %, bleibt so")
        return True
    ok = _set_volume(vol, device)
    STATUS["error"] = "" if ok else "Lautstärke zurück: fehlgeschlagen"
    _log(f"{held_s}: " + (f"wieder {vol} %" if ok else "Lautstärke zurück fehlgeschlagen"))
    return ok


def control(action):
    """The music tool on any device: play, pause, play_pause, next, previous. True when Spotify took it."""
    if not configured():
        return False
    try:
        with _LOCK:
            held = _HELD["device"] is not None
            quiet_device, quiet_vol = _HELD["device"], _HELD["volume"]
            forget()
            if quiet_vol is not None:                 # turned down by the wake word: not still quiet after "Musik aus"
                _set_volume(quiet_vol, quiet_device)
                held = False                          # the music never stopped, so the toggle reads it as it is
            on, device = playing()
            if action == "play_pause":
                # Held by the wake word, the music was playing a moment ago — so the toggle means pause.
                action = "pause" if (on or held) else "play"
            if action == "pause":
                code = 204 if not on else _api("PUT", "/me/player/pause" + _device_q(device))[0]
            elif action == "play":
                code = _api("PUT", "/me/player/play" + _device_q(device))[0]
            elif action in ("next", "previous"):
                code = _api("POST", f"/me/player/{action}" + _device_q(device))[0]
                if held and _ok(code) and not on:     # "nächster Titel" during the hold: play that one
                    _api("PUT", "/me/player/play" + _device_q(device))
            else:
                return False
            return _ok(code)
    except Exception as e:  # noqa: BLE001 — the media keys take over
        STATUS["error"] = str(e)[:160]
        return False


def devices():
    """[{id, name, type, is_active}] of the account's Spotify Connect devices. Spotify sometimes leaves the
    one that plays right now (a Sonos, say) out of the list, so the active device is added from the player."""
    code, data = _api("GET", "/me/player/devices")
    devs = [d for d in ((data or {}).get("devices") or []) if d.get("id")] if code == 200 else []
    code, state = _api("GET", "/me/player")
    dev = (state or {}).get("device") if code == 200 else None
    if dev and dev.get("id") and dev["id"] not in {d["id"] for d in devs}:
        devs.append(dict(dev, is_active=True))
    return devs


def find_device(name, devs=None):
    """The device called `name` — same name ignoring case, else the only one whose name contains it or is
    contained in it ("Echo" for "Nutzer' Echo Dot"). None when nothing or more than one fits."""
    w = " ".join(str(name or "").split()).lower()
    if not w:
        return None
    devs = devices() if devs is None else devs
    exact = [d for d in devs if (d.get("name") or "").lower() == w]
    if exact:
        return exact[0]
    hits = [d for d in devs if w in (d.get("name") or "").lower() or (d.get("name") or "").lower() in w]
    return hits[0] if len(hits) == 1 else None


def now_playing():
    """(playlist/album URI or "", track URI or "", device id or None) of what the account plays right now."""
    code, state = _api("GET", "/me/player")
    if code != 200 or not state:
        return "", "", None
    ctx = ((state.get("context") or {}).get("uri") or "")
    ctx = re.sub(r"^spotify:user:[^:]+:", "spotify:", ctx)     # "spotify:user:spotify:playlist:x" → a plain playlist URI
    return ctx, ((state.get("item") or {}).get("uri") or ""), (state.get("device") or {}).get("id")


def pause_active():
    """Pause whatever device plays; True when done or nothing was playing."""
    on, device = playing()
    return True if not on else _ok(_api("PUT", "/me/player/pause" + _device_q(device))[0])


def transfer(name, track="", position_ms=0):
    """Move the playback to the device called `name` and play there: "die Musik vom Schlafzimmer ins Wohnzimmer".
    (True, device name) or (False, reason in German). Spotify only knows devices that are online and
    logged into the account — a Sonos or Echo that is not in the list cannot be reached this way.
    With `track` the device starts that track at `position_ms` instead: what a Sonos streams itself
    (the wake-up music, Alexa) is no Connect session that Spotify could hand over."""
    if not configured():
        return False, "Spotify ist nicht eingerichtet."
    try:
        with _LOCK:
            quiet_device, quiet_vol = _HELD["device"], _HELD["volume"]
            forget()                                  # an explicit wish: no playing on / volume back later
            if quiet_vol is not None:                 # turned down by the wake word: the old room stays loud, not quiet
                _set_volume(quiet_vol, quiet_device)
            devs = devices()
            dev = find_device(name, devs)
            if not dev:
                known = ", ".join(sorted(d.get("name", "") for d in devs if d.get("name")))
                return False, f"Spotify kennt kein Gerät „{name}“. Gerade erreichbar: {known or 'keins'}."
            if track:
                code, _ = _api("PUT", "/me/player/play" + _device_q(dev["id"]),
                               body={"uris": [track], "position_ms": max(0, int(position_ms or 0))})
            else:
                code, _ = _api("PUT", "/me/player", body={"device_ids": [dev["id"]], "play": True})
            if not _ok(code):
                STATUS["error"] = f"transfer {code}"
                return False, f"Spotify hat die Übergabe an „{dev['name']}“ nicht angenommen (HTTP {code})."
            _log(f"Wiedergabe → {dev['name']}")
            return True, dev["name"]
    except Exception as e:  # noqa: BLE001 — offline or no login
        STATUS["error"] = str(e)[:160]
        return False, f"Spotify antwortet nicht ({str(e)[:60]})."


if HOLD and configured():
    _restore()
