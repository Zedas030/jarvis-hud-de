"""
JARVIS — a memory HUD driven by Claude Code.

    python3 server.py

The brain is your own `claude` CLI, so this inherits your subscription, your
tools, your MCP servers and your CLAUDE.md. The memory is a folder of markdown
you can open in Obsidian. Nothing is hosted; it binds to localhost only.
"""
import json
import mimetypes
import os
import pathlib
import re
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import paths  # noqa: E402,F401 — the jarvis/<group> folders
import jarvis_log  # noqa: E402
import metrics  # noqa: E402

log = jarvis_log.get("server")
UI = ROOT / "ui"
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("application/wasm", ".wasm")        # the wake word runtime (ui/vendor); Windows often knows neither
mimetypes.add_type("text/javascript", ".mjs")

_env = ROOT / ".env"
_FROM_ENV_FILE = set()      # keys this process took from .env (not from the real environment)
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k = k.strip()
            if k not in os.environ:
                _FROM_ENV_FILE.add(k)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))

# Supervisor: the HUD runs as a child process. When the code changes the child
# exits with RESTART_CODE once nobody is talking, and is started again here.
# A crash is restarted too, with a growing pause; five crashes within ten
# minutes mean something is broken for good (port taken, bad .env) — then it stops.
RESTART_CODE = 3


def _port_answers(port, host="127.0.0.1"):
    """True when something already accepts connections on host:port."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


if __name__ == "__main__" and os.environ.get("JARVIS_CHILD") != "1":
    import subprocess
    # One Jarvis per PC. A second start (autostart plus a double-click, an old window left open)
    # used to run every watcher, the wake word and the voice engine a second time — and he said
    # everything twice. If one already answers, only open its page and leave.
    _port = int(os.environ.get("JARVIS_PORT", "8720"))
    if _port_answers(_port):
        print(f"\n  Jarvis läuft schon auf http://127.0.0.1:{_port} — ich starte keinen zweiten.\n", flush=True)
        if os.environ.get("JARVIS_OPEN", "1") != "0":
            webbrowser.open(f"http://127.0.0.1:{_port}")
        sys.exit(0)
    # The child reads .env itself on every start. Handing it the values read here
    # would pin them for good: a changed key (another voice, a new password) only
    # took effect after closing the whole Jarvis window.
    child_env = {k: v for k, v in os.environ.items() if k not in _FROM_ENV_FILE}
    child_env["JARVIS_CHILD"] = "1"
    crashes = []
    while True:
        started = time.time()
        try:
            rc = subprocess.call([sys.executable, str(pathlib.Path(__file__).resolve())] + sys.argv[1:],
                                 env=child_env)
        except KeyboardInterrupt:
            sys.exit(0)
        child_env["JARVIS_OPEN"] = "0"   # the open page reloads itself
        if rc == RESTART_CODE:
            print("\n  Code geändert — Neustart mit frischer Sitzung.\n", flush=True)
            continue
        if rc == 0:
            sys.exit(0)
        crashes = [t for t in crashes if t > time.time() - 600] + [time.time()]
        try:
            (ROOT / "logs").mkdir(exist_ok=True)
            with (ROOT / "logs" / "crash.log").open("a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Exit-Code {rc} nach "
                         f"{int(time.time() - started)} s\n")
        except OSError:
            pass
        if len(crashes) >= 5:
            print(f"\n  Jarvis ist {len(crashes)}-mal in zehn Minuten abgestürzt — ich höre auf. "
                  "Details oben und in logs/crash.log.\n", flush=True)
            sys.exit(rc)
        pause = min(60, 2 ** len(crashes))
        print(f"\n  Jarvis ist abgestürzt (Code {rc}) — Neustart in {pause} s.\n", flush=True)
        try:
            time.sleep(pause)
        except KeyboardInterrupt:
            sys.exit(0)

import commands    # noqa: E402
import devices     # noqa: E402
import icloud_calendar  # noqa: E402
import billing  # noqa: E402
import briefing    # noqa: E402
import answer_cache  # noqa: E402
import dashboard   # noqa: E402
import events      # noqa: E402
import learning    # noqa: E402
import pc          # noqa: E402
import assistant  # noqa: E402
import docsearch  # noqa: E402
import audit  # noqa: E402
import fallback_llm  # noqa: E402
import memory_tools  # noqa: E402
import personality  # noqa: E402
import resources  # noqa: E402
import policy  # noqa: E402
import homelab_watch  # noqa: E402
import energy_coach  # noqa: E402
import homelab_review  # noqa: E402
import house_habits  # noqa: E402
import weather_alerts  # noqa: E402
import proactive  # noqa: E402
import reflection  # noqa: E402
import suggestions  # noqa: E402
import weekly_review  # noqa: E402
import heartbeat  # noqa: E402
import ha_events  # noqa: E402
import memory_index  # noqa: E402
import memory_nas  # noqa: E402
import memory_weekly  # noqa: E402
import birthdays  # noqa: E402
import night_report  # noqa: E402
import memory      # noqa: E402
import router      # noqa: E402
import runtime     # noqa: E402
import scheduler   # noqa: E402
import spotify     # noqa: E402
import voice       # noqa: E402
import wakeword    # noqa: E402
import ambience    # noqa: E402
import alexa_voice  # noqa: E402
import sonos       # noqa: E402
import overlay     # noqa: E402
import watchers    # noqa: E402
import daytime     # noqa: E402
import focus       # noqa: E402
import maintenance  # noqa: E402
import selfcheck   # noqa: E402
import user_habits  # noqa: E402
import wellbeing   # noqa: E402
import voice_config  # noqa: E402
import voice_metrics  # noqa: E402
import voice_ws    # noqa: E402

PORT = int(os.environ.get("JARVIS_PORT", "8720"))
# Remote access (iPhone, iPad): a second port, also on localhost only, that
# `tailscale serve` forwards HTTPS to. Everything arriving there needs a paired
# device (devices.py); the PC's own port stays as it was.
REMOTE = os.environ.get("JARVIS_REMOTE", "0").strip().lower() in ("1", "true", "yes", "on")
REMOTE_PORT = int(os.environ.get("JARVIS_REMOTE_PORT", str(PORT + 1)))
REMOTE_HOSTS = {h.strip().lower() for h in os.environ.get("JARVIS_REMOTE_HOSTS", "").split(",") if h.strip()}
REMOTE_PUBLIC = {"/pair.html", "/manifest.webmanifest", "/icon-180.png", "/icon-512.png",
                 "/icon-192.png", "/icon-32.png", "/favicon.svg"}   # what pair.html and the manifest name
LOCAL_ONLY = {"/api/permission/request", "/api/internal", "/api/pair/start", "/api/devices", "/api/devices/revoke",
              "/api/hud", "/api/proactive/trigger"}   # the desktop card belongs to the PC's own HUD, not a phone
API_TOKEN = secrets.token_urlsafe(32)
DASHBOARD_LAYOUTS = frozenset(("tablet", "stark", "classic", "aurora", "ivory", "midnight", "cockpit",
                               "bento", "horizon", "editorial", "neon", "zen", "orbit", "solstice",
                               "atelier", "mono", "nexus", "tide", "prism"))
DASHBOARD_DEFAULT = "solstice"


def _sonos_hold(speaker, on):
    if alexa_voice.is_alexa(speaker):
        return                         # Alexa lowers its own music while it speaks
    try:
        (sonos.hold if on else sonos.release)(speaker)
    except Exception as e:  # noqa: BLE001 — no house: the music just stays as loud as it was
        log.warning("Sonos leiser/lauter: %s", str(e)[:120])


def _saved_dashboard_layout(device="pc"):
    """The view this device picked last. Each device keeps its own: the iPad station must not follow
    whatever the PC tried out. Before a device has picked one, the old shared choice still counts."""
    state = commands.load()
    for layout in ((state.get("dashboard_layouts") or {}).get(device), state.get("dashboard_layout")):
        if isinstance(layout, str) and layout in DASHBOARD_LAYOUTS:
            return layout
    return DASHBOARD_DEFAULT


def _save_dashboard_layout(device, layout):
    def put(state):
        views = state.get("dashboard_layouts")
        state["dashboard_layouts"] = dict(views if isinstance(views, dict) else {}, **{device: layout})
    commands.update_state(put)
BOOT_ID = secrets.token_hex(4)          # the page reloads itself when this changes
LAST_POST = [time.time()]               # any talking, listening or speaking counts as activity
RUN_LOCK = threading.Lock()
# The voice engine cancels its run when the user keeps talking and asks again with the whole sentence.
# The old turn may still be fetching mail for its context or ending its Claude process: the new one
# waits for it instead of getting 409, which Jarvis read out as "ich komme nicht an mein Gehirn".
RUN_WAIT_S = float(os.environ.get("JARVIS_RUN_WAIT_S", "20"))
MAX_JSON = 1024 * 1024
MAX_AUDIO = 12 * 1024 * 1024
class _Session:
    """The Claude session id, persisted in state.json so a restart resumes the
    same conversation instead of starting from a blank slate."""

    @property
    def id(self):
        sid = commands.load().get("session_id")
        return sid if runtime.valid_session(sid) else None

    def set(self, sid):
        commands.update_state(lambda d: d.__setitem__("session_id", sid))

    def __getitem__(self, key):
        return self.id

    def __setitem__(self, key, value):
        self.set(value)


SESSION = _Session()

# Every turn re-sends the whole session, so a long one burns the usage limit.
# Start a fresh session once it gets heavy or after a pause; long-term memory
# and the "zuletzt besprochen" excerpt from the diary carry over regardless.
# A fresh session already weighs ~40k (persona, memory, tool definitions).
SESSION_MAX_TOKENS = int(os.environ.get("JARVIS_SESSION_MAX_TOKENS", "80000"))
SESSION_IDLE_MIN = int(os.environ.get("JARVIS_SESSION_IDLE_MIN", "20"))

# ── confirmation gate ──────────────────────────────────────────
# jarvis_mcp.py (Claude's --permission-prompt-tool) POSTs every gated tool call
# here; the request blocks until the user answers on the HUD card or it times out.
PERM_TOKEN = secrets.token_urlsafe(32)
os.environ["JARVIS_PERM_TOKEN"] = PERM_TOKEN
PERM_TIMEOUT = int(os.environ.get("JARVIS_PERM_TIMEOUT", "75"))
PENDING = {}                       # id -> {"event", "allow"}
ACTIVE = {"emit": None}            # emitter of the run currently streaming
EMIT_LOCK = threading.Lock()


def _prewarm_soon():
    """Have a Claude process ready for the next question. If this turn changed
    long-term memory, the old process is replaced now — while JARVIS is still
    talking — instead of on the next question."""
    def work():
        try:
            if runtime.LEAN:   # the most common lean setup: Haiku with memory tools
                runtime.lean_prewarm(router.MODEL_SIMPLE, ["memory"], commands.lean_system(["memory"]))
                return
            sid = SESSION["id"]
            runtime.prewarm(sid, commands.system_block(fresh=sid is None))
        except Exception as e:  # noqa: BLE001
            log.warning("prewarm failed: %s", e)
    threading.Thread(target=work, daemon=True).start()


def _tts_status():
    if not os.environ.get("TTS_CHAIN", "").strip():
        return None
    import tts_chain
    return tts_chain.status()


# ── usage log ────────────────────────────────────────────────
USAGE_LOG = ROOT / "logs" / "usage.jsonl"


def log_usage(route, model, groups, usage, system_chars=0, prefix_chars=0, message="", reply="", ms=0):
    """One line per request. Token figures come from the API; the split into
    system prompt / context / tool results is from character counts (≈ chars/3)."""
    u = usage or {}
    row = dict(
        at=time.strftime("%Y-%m-%d %H:%M:%S"), route=route, model=model, groups=list(groups or []),
        input=u.get("input_tokens", 0), cache_read=u.get("cache_read", 0), cache_write=u.get("cache_write", 0),
        output=u.get("output_tokens", 0), api_calls=u.get("api_calls", 0),
        total_in=u.get("input_tokens", 0) + u.get("cache_read", 0) + u.get("cache_write", 0),
        est=dict(system=system_chars // 3, context=prefix_chars // 3,
                 tool_results=u.get("tool_result_chars", 0) // 3, reply=len(reply or "") // 3),
        ms=ms, message=(message or "")[:80])
    kind = (route or "?").split(":", 1)[0]
    metrics.counter("turns_total", route=kind)
    if ms:
        metrics.observe("turn_ms", ms, route=kind)
    if model:
        metrics.counter("model_calls_total", model=model, route=kind)
        for k in ("input", "output", "cache_read", "cache_write"):
            if row[k]:
                metrics.counter("tokens_total", row[k], model=model, kind=k)
    try:
        USAGE_LOG.parent.mkdir(exist_ok=True)
        with USAGE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return row


# Jarvis' own voice in the quiet hours (JARVIS_QUIET_HOURS): this share of the slider value.
NIGHT_VOLUME = float(os.environ.get("JARVIS_NIGHT_VOLUME", "0.5"))


def _voice_factor():
    """Late evening (daytime.py) and a bad night (wellbeing.py) make the HUD a little softer; the
    quiet-hour share stays separate (night_volume)."""
    try:
        return round(daytime.volume_factor() * wellbeing.volume_factor(), 3)
    except Exception:  # noqa: BLE001
        return 1.0

BRIEFING_LLM = os.environ.get("JARVIS_BRIEFING_LLM", "1").strip() not in ("0", "false", "no", "off")
BRIEFING_PROMPT = (commands.LEAN_PERSONA + "\n\nFormuliere aus den Fakten ein gesprochenes Tagesbriefing: "
                   "Begrüßung passend zur Tageszeit, dann Termine, Wetter mit Golf-Einschätzung, Speicher und "
                   "Solar, Mails; fast leere Batterien nur wenn gemeldet. Vier bis sechs kurze Sätze. Keine Börse, "
                   "keine Nachrichten. Nichts erfinden, was nicht in den Fakten steht.")


def _voice_engine_status():
    """The HUD switches to the WebSocket client when the engine runs."""
    cfg = voice_config.load()
    s = voice_ws.status()
    return {"enabled": bool(cfg.engine.get("enabled", True)), "running": s["running"], "port": s["port"] or cfg.engine.port,
            "error": s["error"], "sessions": s["sessions"], "conversation_s": cfg.wake.conversation_s,
            "remote": bool(cfg.engine.get("remote", True))}


def notifications():
    """For the bell in the HUD: recent messages, homelab incidents, open proposals, pause state."""
    import emergency
    import homelab_watch
    import proposals
    with events._COND:
        said = [dict(text=e.get("text", ""), kind=e.get("kind") or e["t"], at=e["at"], t=e["t"])
                for e in events._LOG if e["t"] in ("say", "alarm")][-30:][::-1]
    paused = commands.load().get("paused_until") or 0
    import tasks
    return dict(items=said, incidents=homelab_watch.history(15),
                tasks=[dict(id=t["id"], title=t["title"], status=t["status"], report=t.get("report", ""),
                            done=sum(1 for s in t["steps"] if s["status"] not in ("pending", "running")),
                            total=len(t["steps"])) for t in tasks.get()[-6:][::-1]],
                proposals=[dict(id=p["id"], title=p["title"], why=p["why"]) for p in proposals.pending()],
                paused_until=paused if paused > time.time() else None, persona=personality.label())


def _deny_open_cards():
    """Emergency stop: every waiting confirmation card is answered with no."""
    n = 0
    for entry in list(PENDING.values()):
        entry["allow"] = False
        entry["event"].set()
        n += 1
    return n


def _describe_call(tool, data):
    if not isinstance(data, dict):
        return str(data)[:400]
    if tool.endswith("proposal_accept"):
        import proposals
        p = proposals.get(data.get("id"))
        return policy.describe(tool, {"begruendung": data.get("begruendung")}) + "\n\n" + (
            proposals.describe(p) if p else f"Vorschlag {data.get('id')} (nicht gefunden)")
    if tool.startswith("mcp__jarvis__") and not tool.endswith("calendar_add"):
        return policy.describe(tool, data)
    if tool.endswith("calendar_add"):
        parts = [f"⚠ {data['_hinweis']}" if data.get("_hinweis") else "",
                 f"Neuer Termin: {data.get('title', '?')}",
                 f"{data.get('start', '?')}" + (f" bis {data['end']}" if data.get("end") else ""),
                 data.get("location") or "", data.get("calendar") or ""]
        return "\n".join(p for p in parts if p)
    # Plain-language "what and why" first, the raw command below — the user has to
    # know what he is approving without reading shell syntax.
    kind = {"Bash": "Befehl ausführen", "PowerShell": "Befehl ausführen", "Write": "Datei schreiben",
            "Edit": "Datei ändern", "Read": "Datei lesen", "Glob": "Dateien suchen",
            "Grep": "Dateien durchsuchen", "WebFetch": "Webseite abrufen"}.get(tool, tool)
    why = data.get("description") or ""
    for key in ("command", "file_path", "path", "url", "pattern"):
        if isinstance(data.get(key), str):
            head = f"{kind}: {why}" if why else kind
            return f"{head}\n\n{data[key]}"[:700]
    return json.dumps(data, ensure_ascii=False)[:600]


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second socket bind a port that is already listening, so a
    # second Jarvis started silently next to the first instead of failing. Without it the second
    # bind fails as it should; the restart after a code change is unaffected (Windows does not
    # hold a closed listener's port the way Linux does).
    allow_reuse_address = os.name != "nt"

    def handle_error(self, request, client_address):
        # the browser gave up on a request (Jarvis interrupted, a new question, tab reloaded):
        # nothing went wrong here, so no traceback in the console
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    remote = False            # True on REMOTE_PORT (RemoteHandler)

    def log_message(self, fmt, *args):
        if os.environ.get("JARVIS_VERBOSE"):
            sys.stderr.write("  " + (fmt % args) + "\n")

    def log_request(self, code="-", size="-"):
        # every answer counts: API paths by name, the HUD and its files as one bucket
        p = urlparse(self.path).path
        metrics.counter("http_requests_total", path=p if p.startswith("/api/") or p == "/metrics" else "static",
                        status=str(code))
        super().log_request(code, size)

    def _json(self, body, code=200, headers=None):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to):
        self.send_response(302)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _bytes(self, data, ctype, code=200, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def _speak_stream(self, chunks, mime, provider):
        """Chunked transfer: the HUD starts playing while the provider is still
        speaking. Errors before the first chunk were already handled by the chain;
        if it breaks off later, the sentence simply ends early."""
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Jarvis-Voice", provider)
        self.end_headers()
        try:
            for c in chunks:
                if c:
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(c), c))
            self.wfile.write(b"0\r\n\r\n")
        except OSError:                    # cut off in the HUD: nobody listens any more
            self.close_connection = True
        except Exception as e:  # noqa: BLE001 — provider broke off mid-sentence
            log.warning("Stimme abgebrochen: %s", str(e)[:120])
            self.close_connection = True
        finally:
            close = getattr(chunks, "close", None)
            if close:
                close()

    def _read(self, limit):
        n = int(self.headers.get("Content-Length", 0))
        if n < 0 or n > limit:
            raise ValueError("body too large")
        self._body_read = True
        return self.rfile.read(n) if n else b""

    def _host_ok(self):
        host = self.headers.get("Host", "")
        if self.remote:
            name = host.lower().split(":")[0]
            return name in REMOTE_HOSTS if REMOTE_HOSTS else name.endswith(".ts.net")
        return host in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

    def _origin_ok(self):
        if self.remote:
            return self.headers.get("Origin") == "https://" + self.headers.get("Host", "")
        return self.headers.get("Origin") in {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}

    def _layout_device(self):
        """Whose view: the PC's own browser, or a paired phone/iPad by its device id."""
        if not self.remote:
            return "pc"
        return devices.check(devices.token_from_cookie(self.headers.get("Cookie"))) or "remote"

    def _device_ok(self):
        return bool(devices.check(devices.token_from_cookie(self.headers.get("Cookie"))))

    def _pair(self):
        """A device types the code from the PC's HUD and gets its cookie."""
        if not self._host_ok() or not self._origin_ok():
            return self._json({"error": "origin rejected"}, 403)
        try:
            req = json.loads(self._read(MAX_JSON) or b"{}")
        except (ValueError, TypeError):
            return self._json({"error": "bad json"}, 400)
        token = devices.pair(req.get("code"), req.get("name"))
        if not token:
            return self._json({"error": "Code falsch oder abgelaufen."}, 403)
        return self._json({"ok": True}, headers={"Set-Cookie": devices.cookie_header(token)})

    def _token_ok(self):
        supplied = self.headers.get("X-Jarvis-Token", "")
        return bool(supplied) and secrets.compare_digest(supplied, API_TOKEN)

    def _voice_tunnel(self):
        """iPad/iPhone → voice engine. tailscale serve only forwards the remote port, and the engine listens on
        127.0.0.1 alone; so a paired device's WebSocket upgrade on /voice-ws is passed through byte for byte.
        The engine still checks the page token in its hello and reads the device from the cookie."""
        import selectors
        import socket
        if self.headers.get("Upgrade", "").lower() != "websocket":
            return self._json({"error": "websocket only"}, 400)
        if not self._origin_ok():
            return self._json({"error": "origin rejected"}, 403)
        cfg = voice_config.load()
        s = voice_ws.status()
        if not (s["running"] and cfg.engine.get("remote", True)):
            return self._json({"error": "voice engine off"}, 503)
        try:
            up = socket.create_connection(("127.0.0.1", int(s["port"] or cfg.engine.port)), timeout=5)
        except OSError:
            return self._json({"error": "voice engine unreachable"}, 502)
        head = [f"GET / HTTP/1.1"] + [f"{k}: {v}" for k, v in self.headers.items()]
        self.close_connection = True
        client = self.connection
        try:
            up.settimeout(None)
            client.settimeout(None)
            up.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))
            sel = selectors.DefaultSelector()
            sel.register(client, selectors.EVENT_READ, up)
            sel.register(up, selectors.EVENT_READ, client)
            while True:
                for key, _ in sel.select(timeout=300):
                    data = key.fileobj.recv(65536)
                    if not data:
                        return None
                    key.data.sendall(data)
        except OSError:
            return None
        finally:
            up.close()

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "invalid host"}, 403)
        p = urlparse(self.path).path
        if self.remote and p not in REMOTE_PUBLIC and not self._device_ok():
            if p.startswith("/api/") or p == "/voice-ws":
                return self._json({"error": "device not paired"}, 401)
            return self._redirect("/pair.html")
        if p == "/voice-ws" and self.remote:
            return self._voice_tunnel()

        if p == "/api/layout":
            return self._json({"layout": _saved_dashboard_layout(self._layout_device())})

        if p == "/api/status":
            g = memory.build_graph()
            return self._json(dict(
                runtime=runtime.runtime_kind(), version=runtime.version(),
                model=runtime.MODEL or "Claude default", permission=runtime.PERMISSION,
                workdir=runtime.WORKDIR, vault=g["vault"], notes=g["total"],
                links=len(g["links"]), voice=voice.describe(),
                listener=voice.describe_stt(), stt=voice.stt_kind(),
                server_stt=voice.stt_kind() in ("whisper", "fish", "elevenlabs", "local"),
                server_voice=voice.available(), voice_name=voice.short_name(),
                tts=_tts_status(),
                usage=commands.load().get("usage"),
                billing=billing.summary(),
                wake=wakeword.status(), spotify=spotify.status(), ambience=ambience.status(), timers=scheduler.public(), quiet=events.quiet_now(),
                night_volume=NIGHT_VOLUME, voice_factor=_voice_factor(), voice_mode=daytime.mode(),
                focus=focus.state().get("until") if focus.active() else None,
                fallback=fallback_llm.status(),
                persona=personality.label(),
                sonos=sonos.status(self._layout_device()),
                voice_engine=_voice_engine_status(),
                ha_live=ha_events.status(), heartbeat=heartbeat.status(),
                session=SESSION["id"], boot=BOOT_ID, remote=self.remote, remote_enabled=REMOTE))

        if p == "/api/events":
            # EventSource cannot send headers, so the token comes as a query parameter.
            tok = parse_qs(urlparse(self.path).query).get("token", [""])[0]
            if not (tok and secrets.compare_digest(tok, API_TOKEN)):
                return self._json({"error": "unauthorized"}, 401)
            return self._events()

        if p == "/api/graph":
            force = parse_qs(urlparse(self.path).query).get("force", ["0"])[0] == "1"
            return self._json(memory.build_graph(force=force))

        if p == "/api/live":
            return self._json(briefing.live())

        if p == "/api/dashboard":
            return self._json(dashboard.data())

        if p == "/api/sysmon":
            # the top strip's live figures of this PC, polled every two seconds (sysmon.py)
            import sysmon
            try:
                return self._json(sysmon.sample())
            except Exception as e:  # noqa: BLE001 — psutil or nvidia-smi missing
                return self._json({"error": str(e)[:120]})

        if p == "/metrics":
            # Prometheus text format for Grafana on the Pi; no token (numbers only, no words)
            if not voice_config.load().metrics.get("prometheus", True):
                return self._bytes(b"disabled", "text/plain", 404)
            text = metrics.prometheus() + voice_metrics.prometheus() + metrics.tool_calls_from_audit(audit.PATH)
            return self._bytes(text.encode("utf-8"), "text/plain; version=0.0.4; charset=utf-8")

        if p == "/api/voice/metrics":
            return self._json(voice_metrics.snapshot())

        if p == "/api/notifications":
            return self._json(notifications())

        if p == "/api/proactive":
            # the proactive mode for the iPad: history of announcements, why one was held, learned feedback
            return self._json(proactive.status())

        if p == "/api/note":
            title = parse_qs(urlparse(self.path).query).get("id", [""])[0].lower()
            for n in memory.build_graph()["nodes"]:
                if n["id"] == title:
                    return self._json(n)
            return self._json({"error": "not found"}, 404)

        rel = "index.html" if p == "/" else p.lstrip("/")
        f = (UI / rel).resolve()
        if not str(f).startswith(str(UI.resolve())) or not f.is_file():
            return self._bytes(b"not found", "text/plain", 404)
        data = f.read_bytes()
        if rel == "index.html":
            data = data.replace(b"__JARVIS_TOKEN__", API_TOKEN.encode())
            data = data.replace(b"__JARVIS_LAYOUT__", _saved_dashboard_layout(self._layout_device()).encode("ascii"))
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        # ui/vendor/ holds versioned folders (ort-1.20.1, oww-v0.5.1): 14 MB the iPad keeps instead
        # of fetching them on every load; a new version is a new path
        cache = "private, max-age=31536000, immutable" if rel.startswith("vendor/") else "no-store"
        return self._bytes(data, ctype, cache=cache)

    def do_POST(self):
        # A POST answered without reading its body (rejected, wrong token, "only on the PC")
        # leaves the body in a kept-alive connection; the next request on it then starts
        # with that JSON and fails with 400 — on the iPad that was a bare error page, or a
        # HUD without its stylesheet. A small leftover body is read away, a big one closes the connection.
        self._body_read = False
        try:
            self._do_post()
        finally:
            n = int(self.headers.get("Content-Length", 0) or 0)
            if not self._body_read and n > 0:
                if n <= 1 << 20:
                    self.rfile.read(n)
                else:
                    self.close_connection = True

    def _do_post(self):
        p = urlparse(self.path).path
        # The restart after a code change waits for quiet. The HUD's sign of life (every few
        # seconds) and clips of room noise are no conversation — counting them kept the old
        # code running for good. A clip that held words counts once it is transcribed.
        if p not in ("/api/hud", "/api/listen"):
            LAST_POST[0] = time.time()
        if self.remote:
            if p in LOCAL_ONLY:
                return self._json({"error": "only on the PC"}, 403)
            if p == "/api/pair":
                return self._pair()
            if not self._device_ok():
                return self._json({"error": "device not paired"}, 401)
        if p == "/api/permission/request":
            return self._permission_request()
        if p == "/api/internal":
            return self._internal()
        if not self._host_ok() or not self._origin_ok():
            return self._json({"error": "origin rejected"}, 403)
        if not self._token_ok():
            return self._json({"error": "unauthorized"}, 401)
        ctype = self.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if p in {"/api/run", "/api/speak", "/api/new", "/api/cancel", "/api/balance", "/api/duck", "/api/musichold", "/api/hud", "/api/restart",
                 "/api/layout", "/api/output", "/api/speak/stop", "/api/pair/start", "/api/devices", "/api/devices/revoke", "/api/proactive/feedback",
                 "/api/proactive/trigger"} and ctype != "application/json":
            return self._json({"error": "application/json required"}, 415)
        try:
            raw = self._read(MAX_AUDIO if p == "/api/listen" else MAX_JSON)
        except (TypeError, ValueError):
            return self._json({"error": "body too large"}, 413)

        if p == "/api/layout":
            try:
                body = json.loads(raw or b"{}")
            except (ValueError, TypeError):
                return self._json({"error": "bad json"}, 400)
            layout = body.get("layout") if isinstance(body, dict) else None
            if layout not in DASHBOARD_LAYOUTS:
                return self._json({"error": "unknown layout"}, 400)
            _save_dashboard_layout(self._layout_device(), layout)
            return self._json({"ok": True, "layout": layout})

        if p == "/api/output":
            try:
                body = json.loads(raw or b"{}")
                eid = sonos.set_output(self._layout_device(), body.get("target") if isinstance(body, dict) else "")
            except (ValueError, TypeError):
                return self._json({"error": "unknown speaker"}, 400)
            return self._json({"ok": True, **sonos.status(self._layout_device())})

        if p == "/api/speak/stop":
            # Talking over him: the Sonos stops now; hold=True keeps further sentences back until he goes on.
            target = sonos.output_for(self._layout_device())
            if target and alexa_voice.is_alexa(target):
                alexa_voice.stop(target, hard=True)   # paused or cut off: Alexa goes quiet either way
            elif target:
                sonos.stop(target, hold=bool((json.loads(raw or b"{}") or {}).get("hold")))
            return self._json({"ok": True})

        if p == "/api/speak":
            try:
                body = json.loads(raw or b"{}")
                text = (body.get("text") or "").strip()
                target = sonos.output_for(self._layout_device())
                if target and alexa_voice.is_alexa(target):
                    # an Echo reads the text itself: no voice is made here, no credits used
                    return self._json({"sonos": sonos.label(target), "seconds": alexa_voice.say(target, text),
                                       "voice": "Alexa"})
                if target and sonos.hushed(target):
                    return self._json({"sonos": sonos.label(target), "seconds": 0, "held": True})
                if target:
                    # The device asked, the speaker answers — streamed, from the first sentence on. If the
                    # announcement cannot start, the sound is already made and the device plays it after all.
                    chunks, mime, provider = voice.speak_stream(text)
                    try:
                        secs = sonos.say(target, chunks, mime, text, quiet=events.quiet_now())
                        return self._json({"sonos": sonos.label(target), "seconds": secs, "voice": provider})
                    except sonos.Fallback as e:
                        log.warning("Sonos: %s", str(e.__cause__)[:160])
                        note = sonos.failed(target)
                        if note:         # said once per outage, so the user knows why the PC answers
                            try:
                                data, mime, _ = voice.speak_full(f"{note} {text}")
                                return self._bytes(data, mime)
                            except Exception:  # noqa: BLE001 — the answer itself is already made
                                pass
                        self._bytes(e.data, e.mime)
                        return
                note = sonos.fallback_note() if not target and sonos.chosen(self._layout_device()) == sonos.AUTO else ""
                if note:
                    text = f"{note} {text}"
                if body.get("stream"):
                    return self._speak_stream(*voice.speak_stream(text))
                data, mime, provider = voice.speak_full(text)
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Jarvis-Voice", provider)
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception as e:  # noqa: BLE001
                return self._json({"error": str(e)[:200]}, 503)

        if p == "/api/listen":
            try:
                text = voice.transcribe(raw, self.headers.get("Content-Type", "audio/webm"))
                if text:
                    LAST_POST[0] = time.time()
                return self._json({"text": text})
            except Exception as e:  # noqa: BLE001
                return self._json({"error": str(e)[:400], "text": ""}, 503)

        if p == "/api/pair/start":
            code, ttl = devices.start_pairing()
            return self._json({"code": code, "ttl": ttl, "remote": REMOTE})

        if p == "/api/devices":
            return self._json({"devices": devices.listing(), "remote": REMOTE})

        if p == "/api/devices/revoke":
            did = (json.loads(raw or b"{}") or {}).get("id")
            return self._json({"ok": devices.revoke(did)})

        if p == "/api/proactive/feedback":
            # the iPad's "unwichtig" button on one announcement, or the reset of what was learned
            try:
                body = json.loads(raw or b"{}") or {}
            except (ValueError, TypeError):
                return self._json({"error": "bad json"}, 400)
            kind = str(body.get("kind") or "")[:40]
            if body.get("reset"):
                proactive.reset_feedback(kind or None)
            elif kind in proactive.KIND_NAMES:
                proactive.feedback(kind)
            else:
                return self._json({"error": "unknown kind"}, 400)
            return self._json({"ok": True, "learned": proactive.learned()})

        if p == "/api/proactive/trigger":
            # diagnosis and acceptance on the PC: run arrival / morning / evening / instant now, with real data
            mode = str((json.loads(raw or b"{}") or {}).get("mode") or "")
            if mode not in ("arrival", "morning", "evening", "instant"):
                return self._json({"error": "unknown mode"}, 400)
            return self._json({"ok": True, "text": proactive.trigger(mode)})

        if p == "/api/wakelog":
            # the wake word on a phone or iPad reports what it did (wakeword.device_log)
            if self.remote:
                did = devices.check(devices.token_from_cookie(self.headers.get("Cookie")))
                name = next((d["name"] for d in devices.listing() if d["id"] == did and d["name"]), did)
            else:
                name = "PC"                    # barge-in lines from the PC's own HUD
            wakeword.device_log(name, (json.loads(raw or b"{}") or {}).get("text", ""))
            return self._json({"ok": True})

        if p == "/api/hud":
            # tab visibility and voice state, for the desktop card while the tab is hidden (overlay.py)
            overlay.report(json.loads(raw or b"{}") or {})
            return self._json({"ok": True})

        if p == "/api/restart":
            # The HUD's restart button: same exit as after a code change, so the supervisor
            # starts a fresh server at once (and .env is read again) — no crash entry.
            if os.environ.get("JARVIS_CHILD") != "1":
                return self._json({"ok": False, "error": "Nicht unter dem Supervisor gestartet (start-jarvis.bat)."})
            runtime.cancel_active()
            threading.Timer(0.5, lambda: os._exit(RESTART_CODE)).start()   # let this answer leave first
            return self._json({"ok": True})

        if p == "/api/duck":
            if self.remote:                    # the phone talks, the PC's Spotify stays as it is
                return self._json({"ok": True})
            # Spotify quieter while Jarvis talks (pc.py); runs off the request thread, COM can be slow.
            on = (json.loads(raw or b"{}") or {}).get("on") is True
            threading.Thread(target=pc.duck, args=(on,), daemon=True).start()
            return self._json({"ok": True})

        if p == "/api/musichold":
            # The wake word pauses Spotify on any device of the account (the phone on the living room
            # speaker too), so a remote HUD may ask as well. Off the request thread: the Web API takes a moment.
            body = json.loads(raw or b"{}") or {}
            who = "iPad/Handy" if self.remote else "PC"
            if body.get("on") is True:
                threading.Thread(target=spotify.hold, args=(who,), daemon=True).start()
            else:
                threading.Thread(target=spotify.release, args=(who, str(body.get("why", ""))[:120]),
                                 daemon=True).start()
            speaker = sonos.output_for(self._layout_device())
            if speaker:              # the speaker that will answer goes quiet too, whatever plays on it
                threading.Thread(target=_sonos_hold, args=(speaker, body.get("on") is True), daemon=True).start()
            return self._json({"ok": True})

        if p == "/api/balance":
            try:
                usd = float(str(json.loads(raw or b"{}").get("usd", "")).replace(",", ".").replace("$", ""))
            except (ValueError, json.JSONDecodeError):
                return self._json({"error": "Betrag ungültig"}, 400)
            return self._json(billing.set_balance(usd))

        if p == "/api/permission/answer":
            try:
                ans = json.loads(raw or b"{}")
            except ValueError:   # bad JSON or bytes that are not UTF-8 — both are ValueError
                return self._json({"error": "bad json"}, 400)
            entry = PENDING.get(str(ans.get("id")))
            if not entry:
                return self._json({"error": "no such request (expired?)"}, 404)
            entry["allow"] = ans.get("allow") is True
            entry["event"].set()
            return self._json({"ok": True})

        if p == "/api/new":
            runtime.cancel_active()
            SESSION["id"] = None
            router.forget_topic()
            return self._json({"ok": True})

        if p == "/api/cancel":
            # Keep the session: Esc / an interruption must not wipe the conversation.
            stopped = runtime.cancel_active()
            return self._json({"ok": True, "stopped": stopped})

        if p == "/api/run":
            got = RUN_LOCK.acquire(blocking=False)
            if not got and self.headers.get("X-Jarvis-Voice") == "1":
                runtime.cancel_active()          # the engine dropped that run already: make sure it ends
                got = RUN_LOCK.acquire(timeout=RUN_WAIT_S)
            if not got:
                return self._json({"error": "JARVIS is already processing a request"}, 409)
            try:
                return self._stream(raw)
            finally:
                RUN_LOCK.release()

        return self._json({"error": "no such endpoint"}, 404)

    def _perm_ok(self):
        supplied = self.headers.get("X-Jarvis-Perm", "")
        return (self.client_address[0] == "127.0.0.1"
                and bool(supplied) and secrets.compare_digest(supplied, PERM_TOKEN))

    def _events(self):
        """Server-Sent Events: timers ringing, notices, wake word. One open stream per HUD tab."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        def send(ev):
            self.wfile.write(f"id: {ev['id']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())

        events.CLIENTS[0] += 1
        try:
            self.wfile.write(b"retry: 3000\n\n")
            backlog, cursor = events.connect()
            for ev in backlog:
                send(ev)
            self.wfile.flush()
            while True:
                fresh = events.wait(cursor, 15)
                if not fresh:
                    self.wfile.write(b": ping\n\n")     # keeps the connection (and dead-tab detection) alive
                for ev in fresh:
                    send(ev)
                    cursor = max(cursor, ev["id"])
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            events.CLIENTS[0] -= 1

    def _internal(self):
        """Jarvis' own MCP server asks for things that live here (timers)."""
        if not self._perm_ok():
            return self._json({"error": "unauthorized"}, 401)
        try:
            req = json.loads(self._read(MAX_JSON) or b"{}")
        except (ValueError, TypeError):
            return self._json({"error": "bad json"}, 400)
        op, args = str(req.get("op") or ""), req.get("args") or {}
        if op in ("reminder_add", "reminder_list", "reminder_cancel"):
            return self._json({"text": scheduler.tool(op, args)})
        return self._json({"error": "unknown op"}, 404)

    def _permission_request(self):
        if not self._perm_ok():
            return self._json({"error": "unauthorized"}, 401)
        try:
            req = json.loads(self._read(MAX_JSON) or b"{}")
        except (ValueError, TypeError):
            return self._json({"allow": False})
        emit = ACTIVE["emit"]
        if not emit:   # nobody is watching the HUD -> nobody can confirm
            metrics.counter("cards_total", outcome="nobody")
            return self._json({"allow": False})
        rid = secrets.token_hex(6)
        entry = {"event": threading.Event(), "allow": False}
        PENDING[rid] = entry
        tool = str(req.get("tool_name", "?"))
        try:
            emit(dict(t="confirm", id=rid, tool=tool,
                      detail=_describe_call(tool, req.get("input")), timeout=PERM_TIMEOUT))
            answered = entry["event"].wait(PERM_TIMEOUT)
            metrics.counter("cards_total", outcome="allowed" if entry["allow"] else "denied" if answered else "timeout")
            emit(dict(t="confirm_done", id=rid, allow=entry["allow"]))
            audit.write("card", tool=tool, allow=entry["allow"], args=policy.redact(req.get("input") or {}))
        except OSError:
            entry["allow"] = False
        finally:
            PENDING.pop(rid, None)
        return self._json({"allow": entry["allow"]})

    def _lean(self, message, emit, typed=False, hint=""):
        """Lean path: local router first, otherwise one small stateless Claude call."""
        t0 = time.time()
        commands.update_state(lambda d: d.__setitem__("last_turn_at", time.time()))
        low = message.strip().lower()
        # "Kannst du mir das Tagesbriefing nochmal geben?" too, not only the HUD's start line:
        # as a normal turn it got no tools and Jarvis said he could not fetch anything.
        is_briefing = low.startswith(("/briefing", "tagesbriefing zum start")) or bool(
            re.search(r"\b(tages)?briefing\b", low) and not re.search(r"\b(aus|abschalt\w*|abbrech\w*|stopp?|nicht)\b", low))
        if is_briefing:
            facts = briefing.briefing_facts()
            today = time.strftime("%Y-%m-%d")
            commands.update_state(lambda d: d.__setitem__("briefing_day", today))
            if not BRIEFING_LLM:
                text = "Ihr Tagesbriefing, Sir. " + " ".join(ln.lstrip("- ") for ln in facts.splitlines())
                emit(dict(t="delta", text=text))
                emit(dict(t="complete", ms=0))
                log_usage("briefing-local", None, [], {}, message=message, reply=text)
                return
            route = {"kind": "llm", "groups": [], "model": router.MODEL_SIMPLE, "reason": "briefing"}
            system = BRIEFING_PROMPT
            prefix = f"<kontext>\nJetzt: {time.strftime('%A, %d.%m.%Y, %H:%M Uhr')}\nFakten:\n{facts}\n</kontext>"
            message = "Tagesbriefing bitte."
        else:
            route = router.route(message, ha_full=bool(commands.ha_request(message)))
            try:
                selfcheck.note(message, route)       # the same question again within seconds = a miss
            except Exception:  # noqa: BLE001
                pass
            if route["kind"] == "local":
                reply = route["reply"]
                emit(dict(t="note", message=f"lokal erledigt ({route['handler']}) — 0 Tokens"))
                emit(dict(t="route", model="lokal", groups=[route["handler"]], reason="Router, 0 Tokens"))
                emit(dict(t="delta", text=reply))
                emit(dict(t="complete", ms=int((time.time() - t0) * 1000)))
                try:
                    memory.log_turn(message, reply)
                except OSError:
                    pass
                commands.remember_turn(message, reply)
                log_usage("local:" + route["handler"], None, [], {}, message=message, reply=reply,
                          ms=int((time.time() - t0) * 1000))
                if route["handler"] not in ("time", "date", "sun", "weather", "solar", "nas", "pi", "home_state"):
                    audit.write("local", handler=route["handler"], message=message[:80])
                return
            cached = answer_cache.get(message, route["groups"])
            if cached:                                   # same question minutes ago: no second call
                emit(dict(t="note", message="aus dem Zwischenspeicher — 0 Tokens"))
                emit(dict(t="delta", text=cached))
                emit(dict(t="complete", ms=int((time.time() - t0) * 1000)))
                commands.remember_turn(message, cached)
                log_usage("cache", None, route["groups"], {}, message=message, reply=cached,
                          ms=int((time.time() - t0) * 1000))
                return
            system = commands.lean_system(route["groups"])
            prefix = commands.lean_prefix(message, route["groups"])
            if typed:                                    # typed: longer text, only the first paragraph is spoken
                prefix = prefix.replace("</kontext>", personality.TYPED_HINT + "\n</kontext>")
            if hint:                                     # the voice engine: "unterbrochen nach Satz N" is context, not the question
                prefix = prefix.replace("</kontext>", hint + "\n</kontext>")
        why = ("Claude nicht erreichbar" if runtime.runtime_kind() != "claude" else
               "Claude-Limit erreicht" if fallback_llm.claude_blocked(commands.load().get("usage")) else None)
        if why and fallback_llm.available():
            return self._local_llm(message, prefix, emit, t0, is_briefing, why, route["groups"])
        emit(dict(t="note", message=f"{route['model']} · {', '.join(route['groups']) or 'ohne Werkzeuge'}"))
        emit(dict(t="route", model=route["model"], groups=route["groups"], reason=route.get("reason", "")))
        hit = memory.top_match(message)
        if hit and not is_briefing:
            emit(dict(t="focus", id=hit))
        ACTIVE["emit"] = emit
        if runtime.cancelled_since(getattr(self, "_cancel_mark", runtime.cancel_mark())):
            return                               # cancelled while the context was fetched: nobody waits for it
        reply, usage, before = [], {}, memory.build_graph(force=True)["total"]
        failed, tools = None, []
        try:
            for ev in runtime.run_lean(message, route["model"], route["groups"], system, prefix):
                if ev.get("t") == "error" and not "".join(reply).strip() and fallback_llm.available():
                    failed = ev.get("message") or "Fehler"    # not a word said yet: the local model takes over
                    break
                if ev.get("t") == "tool" and ev.get("phase") == "use":
                    tools.append((ev.get("name") or "", ev.pop("args", None) or {}))
                if ev.get("t") == "delta":
                    reply.append(ev.get("text") or "")
                if ev.get("t") == "usage":
                    usage = ev
                if ev.get("t") == "ratelimit":   # remember for the usage panel after reloads
                    rl = dict(ev, at=time.time())
                    commands.update_state(lambda d: d.__setitem__("usage", rl))
                if ev.get("t") == "complete":
                    text = "".join(reply)
                    if not is_briefing:
                        try:
                            memory.log_turn(message, text)
                        except OSError as e:
                            emit(dict(t="note", message=f"Protokoll fehlgeschlagen: {e}"))
                    commands.remember_turn(message if not is_briefing else "(Tagesbriefing)", text)
                    if memory.build_graph(force=True)["total"] != before:
                        emit(dict(t="graph"))
                    if not is_briefing:
                        answer_cache.put(message, route["groups"], text, [n for n, _ in tools])
                        learned = learning.observe(message, tools)
                        if learned:
                            emit(dict(t="note", message=f"Entwurf gelernt: „{learned}“ — wartet auf Ihre Freigabe"))
                emit(ev)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            log.exception("run failed: %s", str(e)[:200])
            try:
                emit(dict(t="error", message=str(e)[:300]))
            except OSError:
                pass
        finally:
            ACTIVE["emit"] = None
            for entry in list(PENDING.values()):
                entry["event"].set()
            row = log_usage(("briefing" if is_briefing else "llm") + ":" + route.get("reason", ""),
                            route["model"], route["groups"], usage, len(system), len(prefix), message,
                            "".join(reply), int((time.time() - t0) * 1000))
            try:
                emit(dict(t="note", message=f"{row['total_in']:,} Tokens rein, {row['output']} raus, "
                                            f"{row['api_calls']} Aufruf(e)".replace(",", ".")))
            except OSError:
                pass
        if failed:
            self._local_llm(message, prefix, emit, t0, is_briefing, f"Claude-Fehler ({failed[:80]})",
                            route["groups"])

    def _local_llm(self, message, prefix, emit, t0, is_briefing, why, groups=()):
        """A free stand-in answers instead of Claude (fallback_llm.py): Groq, Gemini or
        Ollama, with the same context and the same Jarvis tools."""
        groups = [] if is_briefing else list(groups or [])
        system = BRIEFING_PROMPT if is_briefing else commands.lean_system(groups)
        reply, tools, usage, who = [], [], {}, None
        before = memory.build_graph(force=True)["total"]
        ACTIVE["emit"] = emit            # confirmation cards for tools like calendar_add
        try:
            # the briefing facts hold calendar and mail, so only private-safe providers
            for ev in fallback_llm.run(system, prefix, message, groups, private=is_briefing):
                t = ev.get("t")
                if t == "provider":
                    who = ev
                    emit(dict(t="note", message=f"{why} — antwortet {ev['label']} ({ev['model']})"
                                                + (f" · {', '.join(groups)}" if groups else "")))
                    emit(dict(t="route", model=f"{ev['label']} {ev['model']}", groups=groups, reason=why))
                    continue
                if t == "usage":
                    usage = ev
                    continue
                if t == "tool" and ev.get("phase") == "use":
                    tools.append((ev.get("name") or "", ev.pop("args", None) or {}))
                if t == "delta":
                    reply.append(ev.get("text") or "")
                if t == "error":
                    ev = dict(ev, message=f"{why}. {ev.get('message', '')}"[:300])
                emit(ev)
                if t == "error":
                    return
            emit(dict(t="complete", ms=int((time.time() - t0) * 1000)))
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:  # noqa: BLE001
            try:
                emit(dict(t="error", message=f"Auch das Ersatz-Modell antwortet nicht: {e}"[:300]))
            except OSError:
                pass
            return
        finally:
            ACTIVE["emit"] = None
            for entry in list(PENDING.values()):
                entry["event"].set()
        text = "".join(reply)
        if not is_briefing:
            try:
                memory.log_turn(message, text)
            except OSError:
                pass
            learned = learning.observe(message, tools)
            if learned:
                emit(dict(t="note", message=f"Entwurf gelernt: „{learned}“ — wartet auf Ihre Freigabe"))
        commands.remember_turn(message if not is_briefing else "(Tagesbriefing)", text)
        if memory.build_graph(force=True)["total"] != before:
            emit(dict(t="graph"))
        log_usage("fallback:" + (who["name"] if who else "?"), who["model"] if who else None, groups, usage,
                  len(system), len(prefix), message, text, int((time.time() - t0) * 1000))

    def _stream(self, raw):
        self._cancel_mark = runtime.cancel_mark()
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:   # bad JSON or bytes that are not UTF-8 — both are ValueError
            return self._json({"error": "bad json"}, 400)
        message = (payload.get("message") or "").strip()
        if not message:
            return self._json({"error": "empty message"}, 400)
        if payload.get("fresh"):
            SESSION["id"] = None

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(ev):
            with EMIT_LOCK:   # the permission gate writes from another thread
                self.wfile.write((json.dumps(ev) + "\n").encode())
                self.wfile.flush()

        cmd = commands.handle(message, runner=lambda m: runtime.run(m, None, commands.context_block(m)))
        if cmd:
            if cmd.get("note"):
                emit(dict(t="note", message=cmd["note"]))
            if cmd.get("fresh"):
                SESSION["id"] = None
            if cmd.get("focus"):
                emit(dict(t="focus", id=str(cmd["focus"]).lower()))
            if cmd.get("graph"):
                emit(dict(t="graph"))
            if cmd.get("message") is None:
                delay = float(cmd.get("delay") or 0)
                for event in cmd.get("events", []):
                    if delay and event.get("phase") == "result":
                        time.sleep(delay)
                        delay = 0
                    emit(event)
                emit(dict(t="delta", text=cmd.get("reply", "Done.")))
                emit(dict(t="complete", ms=0))
                return
            message = cmd["message"]

        if runtime.LEAN:
            return self._lean(message, emit, typed=bool(payload.get("typed")), hint=str(payload.get("hint") or "")[:300])

        last = commands.load().get("last_turn_at") or 0
        if SESSION["id"] and time.time() - last > SESSION_IDLE_MIN * 60:
            SESSION["id"] = None
            emit(dict(t="note", message="Neue Sitzung nach Pause (Gedächtnis bleibt)"))
        commands.update_state(lambda d: d.__setitem__("last_turn_at", time.time()))
        if not SESSION["id"]:
            runtime.HA["on"] = False   # a fresh session starts light
        want_ha = commands.ha_request(message)
        if want_ha is not None and want_ha != runtime.HA["on"]:
            runtime.HA["on"] = want_ha
            emit(dict(t="note", message="Home Assistant verbunden" if want_ha
                      else "Home Assistant getrennt"))
        heavy = False
        session = SESSION["id"]
        ACTIVE["emit"] = emit
        system = commands.system_block(fresh=session is None)
        prefix = commands.turn_prefix(message)
        hit = memory.top_match(message)
        if hit:
            emit(dict(t="focus", id=hit))
        reply, before = [], memory.build_graph(force=True)["total"]
        try:
            for ev in runtime.run(message, session, system, prefix):
                if ev.get("t") == "delta":
                    reply.append(ev.get("text") or "")
                if ev.get("t") == "ratelimit":   # remember for the usage panel after reloads
                    usage = dict(ev, at=time.time())
                    commands.update_state(lambda d: d.__setitem__("usage", usage))
                if ev.get("t") == "usage" and (ev.get("context_tokens") or 0) > SESSION_MAX_TOKENS:
                    heavy = True
                if ev.get("t") == "complete" and runtime.valid_session(ev.get("session_id")):
                    SESSION["id"] = None if heavy else ev["session_id"]
                    if heavy:
                        emit(dict(t="note", message="Sitzung zu schwer — die nächste startet frisch"))
                if ev.get("t") == "error":
                    SESSION["id"] = None
                if ev.get("t") == "complete":
                    try:
                        memory.log_turn(message, "".join(reply))
                    except OSError as e:
                        emit(dict(t="note", message=f"Protokoll fehlgeschlagen: {e}"))
                    # Claude may have written a memory note this turn: refresh the graph
                    if memory.build_graph(force=True)["total"] != before:
                        emit(dict(t="graph"))
                emit(ev)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            log.exception("run failed: %s", str(e)[:200])
            try:
                emit(dict(t="error", message=str(e)[:300]))
            except OSError:
                pass
        finally:
            ACTIVE["emit"] = None
            for entry in list(PENDING.values()):   # run ended: nothing left to confirm
                entry["event"].set()
            _prewarm_soon()


class RemoteHandler(Handler):
    remote = True


RESTART_QUIET_S = int(os.environ.get("JARVIS_RESTART_QUIET_S", "90"))


def _code_stamp():
    # jarvis/<group>/ too: the router and the voice live there, not only next to server.py
    files = (list(ROOT.glob("*.py")) + list((ROOT / "jarvis").glob("*/*.py"))
             + [f for f in UI.iterdir() if f.suffix in (".js", ".html", ".css")])
    return {str(f): f.stat().st_mtime for f in files if f.is_file()}


def _watch_code():
    """Restart (via the supervisor) after a code change, but only once nothing
    has been said, heard or confirmed for RESTART_QUIET_S seconds."""
    seen = _code_stamp()
    changed = False
    while True:
        time.sleep(5)
        try:
            changed = changed or _code_stamp() != seen
        except OSError:      # a file mid-save — look again next round
            continue
        idle = (ACTIVE["emit"] is None and not PENDING and not RUN_LOCK.locked()
                and time.time() - LAST_POST[0] > RESTART_QUIET_S)
        if changed and idle:
            print("\n  Code geändert — starte neu.", flush=True)
            SESSION["id"] = None       # new code, new conversation; memory carries over
            runtime.shutdown()
            runtime.lean_shutdown()
            os._exit(RESTART_CODE)


def _open_hud_once():
    """Open the HUD in the browser — unless a tab of it is already there. After a reboot the browser
    restores the old Jarvis tab, and every start used to add another one (five were open). A
    restored tab reports itself within a few seconds (ui/pip.js → /api/hud), so wait that long.
    Tabs that slip through anyway close themselves (ui/tabguard.js)."""
    wait = float(os.environ.get("JARVIS_OPEN_WAIT_S", "12"))
    end = time.time() + wait
    while True:
        if overlay.open_tabs():
            print("  Jarvis-Tab ist schon offen — ich öffne keinen neuen.", flush=True)
            return
        if time.time() >= end:
            break
        time.sleep(0.5)
    url = f"http://127.0.0.1:{PORT}"
    browser = os.path.expandvars(os.environ.get("JARVIS_BROWSER", "").strip())
    if browser and os.path.exists(browser):   # e.g. a browser that should autoplay the voice
        import subprocess
        subprocess.Popen([browser, "--autoplay-policy=no-user-gesture-required", url])
    else:
        webbrowser.open(url)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:   # Windows consoles default to cp1252 and choke on the banner's box characters
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    jarvis_log.setup()             # logs/jarvis.log (JSON lines), warnings also on the console
    moved, deleted = memory.housekeeping()
    if moved or deleted:
        print(f"  Aufgeräumt: {len(moved)} in den Papierkorb, {deleted} endgültig gelöscht", flush=True)

    def daily_housekeeping():
        while True:
            time.sleep(6 * 3600)
            try:
                memory.housekeeping()
            except OSError:
                pass
    threading.Thread(target=daily_housekeeping, daemon=True).start()

    kind = runtime.runtime_kind()
    g = memory.build_graph()
    brain = (f"Claude Code {runtime.version()} — your subscription, no API key"
             if kind == "claude" else "MOCK — claude CLI not reachable")
    print(f"""
  JARVIS · Claude Code Memory HUD
  ────────────────────────────────────────────────
  brain        {brain}
  model        {runtime.MODEL or 'Claude default'}
  vault        {g['total']} notes · {len(g['links'])} links
               {g['vault']}
  voice        {voice.describe()}
  listening    {voice.describe_stt()}
  wake word    {wakeword.MODE}{' (' + wakeword.WORD_MODEL + ')' if wakeword.MODE == 'oww' else ''} · quiet hours {events.QUIET_HOURS or 'off'}
  fallback     {' → '.join(f"{r['name']}{'' if r['ok'] else ' (–)'}" for r in fallback_llm.status()['chain']) or 'off'}
  workdir      {runtime.WORKDIR}
  open         http://localhost:{PORT}
  remote       {f'127.0.0.1:{REMOTE_PORT} for tailscale serve · {len(devices.listing())} devices'
                if REMOTE else 'off (JARVIS_REMOTE=1)'}
""", flush=True)
    srv = None
    for attempt in range(20):      # right after a restart the old child may still be letting go
        try:
            srv = Server(("127.0.0.1", PORT), Handler)
            break
        except OSError:
            if attempt == 19:
                if _port_answers(PORT):
                    print(f"\n  Auf Port {PORT} läuft schon ein Jarvis — dieser hier hört auf.\n", flush=True)
                    sys.exit(0)
                raise
            time.sleep(0.5)
    if REMOTE:
        remote_srv = Server(("127.0.0.1", REMOTE_PORT), RemoteHandler)
        threading.Thread(target=remote_srv.serve_forever, daemon=True).start()
    _prewarm_soon()
    threading.Thread(target=icloud_calendar.agenda_block, daemon=True).start()
    threading.Thread(target=voice.preload_local_stt, daemon=True).start()
    threading.Thread(target=billing.summary, daemon=True).start()
    if os.environ.get("TTS_CHAIN", "").strip():
        import tts_chain
        tts_chain.start()          # provider balances in the background
    briefing.start_watchdog()
    scheduler.start()              # timers, alarms, reminders, planned commands
    ha_events.start()              # HA state over one WebSocket; watchers wake on changes
    watchers.start()               # proactive notices (appliances, appointments, storage, NAS)
    heartbeat.start(PORT)          # sign of life for the HA watchdog (a dead Jarvis gets noticed)
    reflection.start()             # learning loop: Lernprotokoll, weekly rules proposal; lasting facts it saves itself
    memory_index.start()           # full-text index over notes and every exchange (FTS5)
    memory_nas.start()             # once a day: vault zip and index to the NAS
    memory_weekly.start()          # Sunday: what Jarvis saved by himself this week, „passt“ / „streich …“
    birthdays.start()              # the day before a birthday from the memory, with what the person likes
    night_report.start()           # morning: the night's ghost pulses and lights, only when there were any
    weekly_review.start()          # Garmin once a day, weekly review on Sunday
    homelab_watch.start()          # outages and unusual values in the homelab
    energy_coach.start()           # learns the plugs, says when solar power is left over
    homelab_review.start()         # Sunday: outages, security and energy of the week
    weather_alerts.start()         # DWD warnings for here, each once
    house_habits.start()           # habits in the house -> routine proposals, lights left on
    user_habits.start()            # the user's own habits -> answers by himself or warmed up
    wellbeing.start()              # Garmin as a trigger: gentle mornings, early-night hint
    maintenance.start()            # certificates, docker images, backup age — daily
    focus.start_loop()             # focus sessions: held notices, distraction hint, summary
    import mail_events
    mail_events.start()            # bookings in mail -> calendar (trusted senders) or a proposal
    import shipments
    shipments.start()              # deliveries in mail -> expected day in the calendar, reminder
    suggestions.start()            # a new ability twice a week
    memory_tools.start()           # daily vault backup (backups/), audit log trimming
    assistant.start()              # watched topics (web search, at most a few a day)
    resources.start()              # VRAM budget: GPU voice, Whisper, Ollama
    docsearch.start()              # local full-text index of the shared folders (hourly, incremental)
    router.EMERGENCY_HOOKS.update(deny=_deny_open_cards, cancel=runtime.cancel_active)
    wakeword.start()               # openWakeWord, if JARVIS_WAKE=oww
    if voice_config.load().engine.get("enabled", True):
        voice_ws.start(PORT, API_TOKEN)   # full-duplex voice engine (config/voice.yaml), ws://127.0.0.1:8724
    ambience.start()               # music / sport / TV playing in the house (Home Assistant)
    if sonos.configured():
        try:
            print(f"  Sonos-Ausgabe: {sonos.start()}", flush=True)   # clip server on the LAN
        except OSError as e:
            print(f"  Sonos-Ausgabe aus: {e}", flush=True)
    overlay.start()                # desktop card while the HUD tab is hidden
    if os.environ.get("JARVIS_CHILD") == "1":    # only a supervised child can be restarted
        threading.Thread(target=_watch_code, daemon=True).start()
    if os.environ.get("JARVIS_OPEN", "1") != "0":
        threading.Thread(target=_open_hud_once, daemon=True, name="open-hud").start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  down.")
        srv.shutdown()


if __name__ == "__main__":
    main()
