"""
Jarvis' voice on a Sonos speaker, chosen per device: the iPad types or talks, the bedroom Sonos answers.

The voice is made as usual (tts_chain); the clip is then handed to the speaker through Home Assistant
as an announcement (`media_player.play_media` with `announce`), so music on the Sonos dips and plays on
afterwards. A Sonos only plays what it can fetch itself, and the HUD server listens on 127.0.0.1 only —
so a second, tiny server on the LAN address hands out nothing but the clips: GET only, random names,
gone after CLIP_TTL_S, only to private addresses.

JARVIS_SONOS lists the speakers, "media_player.x=Label, media_player.y"; empty = feature off.
Which device speaks where lives in state.json -> speech_outputs {device id: entity}. The entry AUTO
("Wo ich bin") means the speaker of the room the presence sensors see the user in (whereabouts.py);
it is also the default once presence_room is configured. "" is the device itself, chosen on purpose.
"""
import ipaddress
import json
import os
import re
import secrets
import socket
import struct
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import alexa_voice
import commands
import whereabouts

HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123").strip().rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "").strip()
PORT = int(os.environ.get("JARVIS_SONOS_PORT", "8723"))
HOST = os.environ.get("JARVIS_SONOS_HOST", "").strip()           # empty: the address that reaches HA
VOLUME = float(os.environ.get("JARVIS_SONOS_VOLUME", "0.35"))
NIGHT_VOLUME = float(os.environ.get("JARVIS_SONOS_NIGHT_VOLUME", "0.2"))
# Like Alexa: while Jarvis listens, thinks and answers, the music on the device's speaker drops to this.
QUIET = float(os.environ.get("JARVIS_SONOS_QUIET", "0.12"))
HOLD_MAX_S = float(os.environ.get("JARVIS_SPOTIFY_HOLD_MAX_S", "120"))     # the HUD went silent: music back anyway
CLIP_TTL_S = 600
LEAD_S = 1.2             # Sonos fetches, buffers and dips the music before the first word
LIVE_AHEAD_S = 1.5       # live stream: sent this far ahead of the speaker (also how late a stop takes hold)
LIVE_IDLE_S = 4.0        # ... and ends the announcement after this long without a new sentence
LIVE_MAX_S = 300
EXT = {"audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/ogg": "ogg", "audio/aac": "aac"}
AUTO = "auto"            # the speaker of the room the user is in
AUTO_LABEL = "Wo ich bin"


def _speakers():
    out = []
    for part in os.environ.get("JARVIS_SONOS", "").split(","):
        eid, _, label = part.strip().partition("=")
        eid = eid.strip()
        if re.fullmatch(r"media_player\.[a-z0-9_]+", eid):
            out.append({"id": eid, "label": label.strip() or "Sonos " + eid.split(".", 1)[1].replace("_", " ").title()})
    return out


SPEAKERS = _speakers()


def outputs():
    """Every speaker a device can answer through: the Sonos and the Echos (alexa_voice.py), and — once the
    presence sensors are set up — "Wo ich bin", the speaker of whichever room the user is in."""
    out = SPEAKERS + alexa_voice.outputs()
    if out and whereabouts.configured():
        out = out + [{"id": AUTO, "label": AUTO_LABEL}]
    return out


def configured():
    return bool(outputs() and HA_TOKEN)


def label(eid):
    return next((s["label"] for s in outputs() if s["id"] == eid), "")


# ── which device speaks where ─────────────────────────────────
def chosen(device):
    """What is set for this device: a speaker, AUTO, or "" for the device itself. A device that never
    chose follows the user (AUTO) once the presence sensors are set up, else speaks itself."""
    if not configured():
        return ""
    outs = commands.load().get("speech_outputs") or {}
    eid = outs.get(device or "") if isinstance(outs, dict) else None
    if eid is None:
        eid = AUTO if whereabouts.configured() else ""
    return eid if label(eid) else ""


# "Wo ich bin" without a working source: the device answers itself and says why, once per outage.
_NOTE = {"why": "", "told": ""}
_PLAYER = {}             # entity -> (checked at, available)
PLAYER_TTL_S = 15


def _available(eid):
    """A Sonos HA lists as unavailable takes the announcement without a sound — check before using it."""
    if not eid.startswith("media_player."):
        return True                                # an Echo (notify.*) reports its own failures
    at, ok = _PLAYER.get(eid, (0.0, True))
    if time.time() - at < PLAYER_TTL_S:
        return ok
    try:
        ok = (json.loads(_ha(f"/api/states/{eid}") or b"{}") or {}).get("state") not in ("unavailable", None)
    except Exception:  # noqa: BLE001
        ok = False
    _PLAYER[eid] = (time.time(), ok)
    return ok


def resolve(eid):
    """AUTO -> the speaker of the room the user is in right now ("" when nobody is seen or the room has no
    speaker); any other entity unchanged."""
    if eid != AUTO:
        return eid
    room = whereabouts.room()
    if whereabouts.offline():
        _NOTE["why"] = "Ich sehe gerade nicht, in welchem Raum Sie sind, darum antworte ich hier."
        return ""
    target = whereabouts.speaker(room)
    target = target if target != AUTO and label(target) else ""
    if target and not _available(target):
        _NOTE["why"] = f"Der Lautsprecher {label(target)} ist gerade nicht erreichbar, darum antworte ich hier."
        return ""
    _NOTE.update(why="", told="")                  # all well again: the next outage is told again
    return target


def fallback_note():
    """The sentence to put before an answer the device speaks itself because "Wo ich bin" failed,
    once per outage; "" otherwise."""
    why = _NOTE["why"]
    if not why or why == _NOTE["told"]:
        return ""
    _NOTE["told"] = why
    return why


def failed(eid):
    """The announcement on eid did not start (sonos.Fallback): remember it, -> the sentence to say once."""
    _PLAYER[eid] = (time.time(), False)
    _NOTE["why"] = f"Der Lautsprecher {label(eid) or 'im Raum'} antwortet nicht, darum antworte ich hier."
    return fallback_note()


def output_for(device):
    """The speaker this device's answers go to right now, or "" for the device itself."""
    return resolve(chosen(device))


def set_output(device, eid):
    eid = eid or ""
    if eid and not label(eid):
        raise ValueError("unknown speaker")

    def put(state):
        outs = state.get("speech_outputs")
        outs = dict(outs if isinstance(outs, dict) else {})
        outs[device] = eid               # "" on purpose: the device itself, not the AUTO default
        state["speech_outputs"] = outs
    commands.update_state(put)
    return eid


def status(device):
    pick = chosen(device)
    eid = resolve(pick)
    text = label(eid)
    if pick == AUTO:
        text = f"{AUTO_LABEL} · {label(eid) if eid else 'hier'}"
    return {"speakers": outputs() if configured() else [], "output": pick, "output_label": text}


# ── how long a clip plays ─────────────────────────────────────
_MP3_KBPS = {  # (MPEG-1 layer III, MPEG-2/2.5 layer III) by bitrate index
    1: (32, 8), 2: (40, 16), 3: (48, 24), 4: (56, 32), 5: (64, 40), 6: (80, 48), 7: (96, 56),
    8: (112, 64), 9: (128, 80), 10: (160, 96), 11: (192, 112), 12: (224, 128), 13: (256, 144), 14: (320, 160)}


def duration(data, mime, text=""):
    """Seconds of speech in the clip; the HUD waits that long before it listens again.
    TTS providers send constant-bitrate MP3 or plain WAV, so the header is enough."""
    try:
        if mime in ("audio/wav", "audio/x-wav") and data[:4] == b"RIFF":
            byte_rate = struct.unpack("<I", data[28:32])[0]
            if byte_rate:
                return (len(data) - 44) / byte_rate
        if mime == "audio/mpeg":
            i = 0
            if data[:3] == b"ID3":
                i = 10 + ((data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F))
            while i < min(len(data) - 4, 64 * 1024):
                if data[i] == 0xFF and data[i + 1] & 0xE0 == 0xE0:
                    mpeg1 = (data[i + 1] >> 3) & 0x03 == 0x03
                    kbps = _MP3_KBPS.get(data[i + 2] >> 4)
                    if kbps:
                        return (len(data) - i) * 8 / (kbps[0 if mpeg1 else 1] * 1000)
                i += 1
    except (IndexError, struct.error):
        pass
    return len(text or "") / 14 + 0.5          # German TTS speaks about 14 characters a second


# ── the clip server on the LAN ────────────────────────────────
_CLIPS = {}              # token -> (bytes, mime, expires)
_LOCK = threading.Lock()
_SERVER = {"base": None}
_PATH = re.compile(r"^/sonos/([0-9a-f]{32})\.[a-z0-9]{2,4}$")
_LIVE_PATH = re.compile(r"^/sonos/live/([0-9a-f]{32})\.mp3$")


class _Clips(BaseHTTPRequestHandler):
    def _send(self, body):
        try:
            private = ipaddress.ip_address(self.client_address[0]).is_private
        except ValueError:
            private = False
        live = _LIVE_PATH.match(urlparse(self.path).path)
        if live and private:
            with _LOCK:
                stream = _STREAMS.get(live.group(1))
            if stream:
                return stream.serve(self, body)
        m = _PATH.match(urlparse(self.path).path)
        with _LOCK:
            clip = _CLIPS.get(m.group(1)) if (m and private) else None
        if not clip or clip[2] < time.time():
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data, mime, _ = clip
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(data)

    def do_GET(self):
        self._send(True)

    def do_HEAD(self):
        self._send(False)

    def log_message(self, *a):
        pass


def _lan_address():
    """The PC's address in the network the house (and the Sonos) is in."""
    if HOST:
        return HOST
    target = urlparse(HA_URL).hostname or "10.0.0.1"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((target, 9))           # UDP connect sends nothing, it only picks the route
        return s.getsockname()[0]


def start():
    """Starts the clip server once; -> base URL. Raises if the port is taken."""
    with _LOCK:
        if _SERVER["base"]:
            return _SERVER["base"]
        host = _lan_address()
        srv = ThreadingHTTPServer((host, PORT), _Clips)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True, name="sonos-clips").start()
        _SERVER["base"] = f"http://{host}:{srv.server_address[1]}"
        return _SERVER["base"]


def _keep(data, mime):
    tok = secrets.token_hex(16)
    now = time.time()
    with _LOCK:
        for k in [k for k, v in _CLIPS.items() if v[2] < now]:
            del _CLIPS[k]
        _CLIPS[tok] = (data, mime, now + CLIP_TTL_S)
    return f"{tok}.{EXT.get(mime, 'mp3')}"


# ── live: the announcement starts with the first sentence ─────
# Waiting for the whole answer and then its whole voice cost several seconds (5.6 s of voice for three
# sentences, against 0.7 s to its first sound). So the Sonos gets one endless MP3 instead: the first
# sentence starts the announcement, every further one is appended to the same stream while it is
# being spoken, and silent frames bridge the gaps — one announcement, no dip and rise between sentences.
_STREAMS = {}            # token -> _Live
_LIVE_BY_SPEAKER = {}    # entity -> _Live still open
_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _mp3_frame(data):
    """-> (bits per second, a silent frame of the same format), or None."""
    for i in range(min(len(data) - 3, 64 * 1024)):
        if data[i] != 0xFF or data[i + 1] & 0xE6 != 0xE2:          # frame sync, layer III
            continue
        version, rate_i = (data[i + 1] >> 3) & 0x03, (data[i + 2] >> 2) & 0x03
        kbps = _MP3_KBPS.get(data[i + 2] >> 4)
        if version not in _RATES or rate_i == 3 or not kbps:
            continue
        bps = kbps[0 if version == 3 else 1] * 1000
        size = (144 if version == 3 else 72) * bps // _RATES[version][rate_i]
        header = bytes([0xFF, data[i + 1] | 0x01, data[i + 2] & 0xFD, data[i + 3]])   # no CRC, no padding
        return bps, header + b"\0" * (size - 4)          # empty side info decodes to silence
    return None


def _skip_id3(chunk):
    """A tag in the middle of a stream is noise to the decoder."""
    if chunk[:3] != b"ID3" or len(chunk) < 10:
        return chunk
    return chunk[10 + ((chunk[6] & 0x7F) << 21 | (chunk[7] & 0x7F) << 14 | (chunk[8] & 0x7F) << 7 | (chunk[9] & 0x7F)):]


class _Live:
    def __init__(self, eid):
        self.eid, self.token = eid, secrets.token_hex(16)
        self.buf = bytearray()
        self.cond = threading.Condition()
        self.audio_s = 0.0           # seconds of sound written, silence included
        self.started = None          # when the Sonos began to read
        self.fed = time.time()       # last sound appended
        self.bps, self.silence, self.closed = 0, b"", False

    def feed(self, chunk, first):
        if first:
            chunk = _skip_id3(chunk)
        if not self.bps:
            self.bps, self.silence = _mp3_frame(chunk) or (0, b"")
        with self.cond:
            self.buf += chunk
            self.audio_s += len(chunk) * 8 / self.bps if self.bps else 0
            self.fed = time.time()
            self.cond.notify_all()

    def left(self):
        """Seconds the speaker still has to play of what is written."""
        if self.started is None:
            return self.audio_s + LEAD_S
        return max(0.0, self.audio_s - (time.time() - self.started))

    def serve(self, handler, body):
        handler.send_response(200)
        handler.send_header("Content-Type", "audio/mpeg")
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        if not body:
            return
        with self.cond:
            if self.started is None:
                self.started = time.time()
        pos = 0
        try:
            while True:
                with self.cond:
                    while not self.closed:
                        # Only a little ahead of the speaker: what it has buffered it plays even after a
                        # stop, so a pause (talking over him) would come seconds late.
                        budget = len(self.buf) - pos
                        if self.bps:
                            budget = min(budget, int(((time.time() - self.started + LIVE_AHEAD_S) * self.bps / 8))
                                         - pos)
                        if budget > 0:
                            break
                        if pos >= len(self.buf) and self.left() < LIVE_AHEAD_S:
                            now = time.time()
                            if not self.silence or now - self.fed > LIVE_IDLE_S or now - self.started > LIVE_MAX_S:
                                self._close()
                                break
                            self.buf += self.silence
                            self.audio_s += len(self.silence) * 8 / self.bps
                            continue
                        self.cond.wait(0.05)
                    if self.closed:
                        budget = 0           # stopped: nothing more goes out
                    data = bytes(self.buf[pos:pos + max(0, budget)])
                    pos += len(data)
                    done = self.closed
                if data:
                    handler.wfile.write(data)
                    handler.wfile.flush()
                if done:
                    return
        except OSError:          # the speaker hung up (stopped, or another announcement)
            with self.cond:
                self._close()

    def _close(self):
        self.closed = True
        with _LOCK:
            if _LIVE_BY_SPEAKER.get(self.eid) is self:
                del _LIVE_BY_SPEAKER[self.eid]
        self.cond.notify_all()


_HUSHED = {}             # entity -> until: paused for the user, sentences wait instead of starting a new announcement
HUSH_MAX_S = 30          # a HUD that never says "go on" does not keep the speaker silent for good


def stop(eid, hold=False):
    """Ends the running announcement (within LIVE_AHEAD_S). hold=True: the user is talking over him —
    further sentences are not played until stop(eid) again."""
    with _LOCK:
        live = _LIVE_BY_SPEAKER.pop(eid, None)
        if hold:
            _HUSHED[eid] = time.time() + HUSH_MAX_S
        else:
            _HUSHED.pop(eid, None)
    if live is not None:
        with live.cond:
            live.closed = True
            live.cond.notify_all()


def hushed(eid):
    with _LOCK:
        return _HUSHED.get(eid, 0) > time.time()


class Fallback(Exception):
    """The announcement could not start: the device plays the sound itself."""
    def __init__(self, data, mime):
        super().__init__("Sonos announcement failed")
        self.data, self.mime = data, mime


def say(eid, chunks, mime, text="", quiet=False):
    """One sentence on the speaker as soon as its first sound exists; -> seconds until the speaker has
    finished it. Joins the announcement still running there or starts one. A voice that is not MP3
    goes the old way, as one clip."""
    if not label(eid):
        raise ValueError("unknown speaker")
    if mime != "audio/mpeg":
        data = b"".join(chunks)
        try:
            return play(eid, data, mime, text, quiet)
        except Exception as e:  # noqa: BLE001
            raise Fallback(data, mime) from e
    with _LOCK:
        live = _LIVE_BY_SPEAKER.get(eid)
        fresh = live is None or live.closed or (live.started is not None and live.left() <= 0.2)
        if fresh:
            if live is not None and not live.closed:
                with live.cond:              # not _close(): _LOCK is held here
                    live.closed = True
                    live.cond.notify_all()
            for k in [k for k, v in _STREAMS.items() if v.closed]:
                del _STREAMS[k]
            live = _Live(eid)
            _STREAMS[live.token] = live
            _LIVE_BY_SPEAKER[eid] = live
    got, first = bytearray(), True
    for chunk in chunks:
        if not chunk:
            continue
        got += chunk
        live.feed(chunk, first)
        if first and fresh:
            try:
                _ha("/api/services/media_player/play_media", {
                    "entity_id": eid, "media_content_id": f"{start()}/sonos/live/{live.token}.mp3",
                    "media_content_type": "music", "announce": True,
                    "extra": {"volume": _percent(NIGHT_VOLUME if quiet else VOLUME)}})
            except Exception as e:  # noqa: BLE001
                with live.cond:
                    live._close()
                raise Fallback(bytes(got) + b"".join(chunks), mime) from e
        first = False
    return round(live.left(), 1)


# ── play ──────────────────────────────────────────────────────
def _ha(path, body=None):
    req = urllib.request.Request(HA_URL + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {HA_TOKEN}", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


# ── music down while Jarvis is busy ───────────────────────────
# Spotify's own hold (spotify.py) does not reach music sent by AirPlay or the Sonos app, so the
# speaker's volume itself goes down. Only what was lowered here is raised again, and not when
# someone turned the knob in between.
_HELD = {}               # entity -> {"volume": before, "timer": Timer}


def hold(eid):
    if not label(eid):
        return
    with _LOCK:
        if eid in _HELD:
            _HELD[eid]["timer"].cancel()
            _HELD[eid]["timer"] = _release_later(eid)
            return
    st = json.loads(_ha(f"/api/states/{eid}") or b"{}")
    vol = (st.get("attributes") or {}).get("volume_level")
    if st.get("state") != "playing" or not isinstance(vol, (int, float)) or vol <= QUIET:
        return
    _ha("/api/services/media_player/volume_set", {"entity_id": eid, "volume_level": QUIET})
    with _LOCK:
        _HELD[eid] = {"volume": vol, "timer": _release_later(eid)}


def _release_later(eid):
    t = threading.Timer(HOLD_MAX_S, release, args=(eid,))
    t.daemon = True
    t.start()
    return t


def release(eid):
    with _LOCK:
        held = _HELD.pop(eid, None)
    if not held:
        return
    held["timer"].cancel()
    st = json.loads(_ha(f"/api/states/{eid}") or b"{}")
    vol = (st.get("attributes") or {}).get("volume_level")
    if isinstance(vol, (int, float)) and abs(vol - QUIET) > 0.02:
        return               # turned by hand meanwhile: that is the volume wanted now
    _ha("/api/services/media_player/volume_set", {"entity_id": eid, "volume_level": held["volume"]})


def _percent(v):
    """HA hands the announce volume to the Sonos audio-clip API unchanged, and that wants 0-100:
    0.35 arrived as 0 — the music dipped for a silent clip."""
    return max(1, min(100, round(v * 100 if v <= 1 else v)))


def play(eid, data, mime, text="", quiet=False):
    """Announces the clip on the speaker; -> seconds until it has finished. Raises on failure,
    the caller then lets the device speak itself."""
    if not label(eid):
        raise ValueError("unknown speaker")
    url = start() + "/sonos/" + _keep(data, mime)
    _ha("/api/services/media_player/play_media", {
        "entity_id": eid, "media_content_id": url, "media_content_type": "music",
        "announce": True, "extra": {"volume": _percent(NIGHT_VOLUME if quiet else VOLUME)}})
    return round(duration(data, mime, text) + LEAD_S, 1)
