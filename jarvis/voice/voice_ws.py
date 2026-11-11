"""
WebSocket front door of the voice engine (package `websockets`, asyncio in its own thread).

One connection = one Session (voice_engine.py). Wire format:
  client → server   binary: one microphone frame, 16-bit mono PCM at engine.sample_rate (20 ms)
                    text (JSON): {"t":"hello","token":..., "ambience":...}   first message, the HUD's API token
                                 {"t":"text","message":...,"typed":true,"speak":true}
                                 {"t":"say","text":...}       a notice to read aloud
                                 {"t":"stop"}                 the Stop button / Escape
                                 {"t":"ptt","on":true|false}
                                 {"t":"mute","on":true|false}
                                 {"t":"playing","id":n} {"t":"ended","id":n} {"t":"idle"}   player reports
                                 {"t":"ambience","kind":"tv"} {"t":"wake_heard"}
  server → client   binary: playback PCM, 16-bit mono at engine.out_rate, belonging to the sentence of
                            the last {"t":"audio_begin","id":n,"text":...}; {"t":"audio_end","id":n} closes it
                    text: state / level / partial / final / run (the NDJSON events of /api/run) /
                          gain {"value","ms"} / flush / cue / chime / window / log / speak_done / ready

Who may connect: the HUD's token (the same the page uses for /api/*) — the socket listens on
127.0.0.1 like the HUD, a paired remote device reaches it through the same tailscale path later.
"""
import asyncio
import json
import threading
import time

import voice_engine
import jarvis_log

log = jarvis_log.get(__name__)

STATE = {"running": False, "error": "", "sessions": 0, "bind": "", "port": 0, "thread": None}
SOCKETS = {}                # id(ws) -> ws, so a newer session of the same device can close an older one


def status():
    return {k: v for k, v in STATE.items() if k != "thread"}


def start(port, token, cfg=None):
    """Starts the server thread once; returns the status dict."""
    if STATE["thread"] is not None:
        return status()
    try:
        import websockets  # noqa: F401
    except ImportError:
        STATE["error"] = "websockets fehlt (pip install websockets)"
        return status()
    shared = voice_engine.Shared(port, token, cfg)
    STATE.update(bind=shared.cfg.engine.bind, port=int(shared.cfg.engine.port))
    t = threading.Thread(target=_serve, args=(shared,), daemon=True, name="voice-ws")
    STATE["thread"] = t
    t.start()
    return status()


def _serve(shared):
    import websockets
    cfg = shared.cfg.engine

    async def handler(ws):
        loop = asyncio.get_running_loop()
        token_ok = False
        session = None
        device = "pc"

        def send(obj):
            if ws.close_code is None:
                asyncio.run_coroutine_threadsafe(_safe_send(ws, json.dumps(obj, ensure_ascii=False)), loop)

        def send_audio(data):
            if ws.close_code is None:
                asyncio.run_coroutine_threadsafe(_safe_send(ws, data), loop)

        try:
            # the first message must carry the token
            first = await asyncio.wait_for(ws.recv(), timeout=10)
            if isinstance(first, bytes):
                await ws.close(4001, "hello first")
                return
            hello = json.loads(first)
            if hello.get("t") != "hello" or not _token_ok(hello.get("token"), shared.token):
                await ws.close(4003, "unauthorized")
                return
            token_ok = True
            device = _device_of(ws, hello)
            session = voice_engine.Session(shared, send, send_audio, device=device)
            if hello.get("ambience"):
                session.client_event({"t": "ambience", "kind": hello["ambience"]})
            # One live session per device. Every extra HUD tab on the PC used to open its own
            # session on the same microphone, and each of them answered the same question.
            # The newest connection wins; the older ones are told why (4002) and stay quiet.
            with shared.lock:
                older = [k for k, s in shared.sessions.items() if getattr(s, "device", None) == device]
                shared.sessions[id(ws)] = session
                STATE["sessions"] = len(shared.sessions)
                SOCKETS[id(ws)] = ws
            for key in older:
                old_ws = SOCKETS.get(key)
                log.info("Sprach-Session von %s durch neuere Verbindung ersetzt", device)
                if old_ws is not None and old_ws.close_code is None:
                    await old_ws.close(4002, "replaced")
            async for msg in ws:
                if isinstance(msg, bytes):
                    session.feed(msg)
                    continue
                try:
                    ev = json.loads(msg)
                except ValueError:
                    continue
                t = ev.get("t")
                if t == "text":
                    session.text(str(ev.get("message") or ""), typed=bool(ev.get("typed", True)),
                                 speak=ev.get("speak", True) is not False)
                elif t == "say":
                    session.say(str(ev.get("text") or ""))
                elif t == "stop":
                    session.stop(str(ev.get("why") or "Stopp"))
                elif t == "ptt":
                    session.ptt_on() if ev.get("on") else session.ptt_off()
                else:
                    session.client_event(ev)
        except (asyncio.TimeoutError, websockets.ConnectionClosed, ValueError):
            pass
        except Exception as e:  # noqa: BLE001
            log.warning("Verbindung: %s", str(e)[:120])
        finally:
            if session is not None:
                session.close()
                with shared.lock:
                    shared.sessions.pop(id(ws), None)
                    SOCKETS.pop(id(ws), None)
                    STATE["sessions"] = len(shared.sessions)
            if not token_ok and ws.close_code is None:
                await ws.close()

    async def main():
        try:
            async with websockets.serve(handler, cfg.bind, int(cfg.port), max_size=4 * 1024 * 1024,
                                        ping_interval=20, ping_timeout=20, compression=None):
                STATE.update(running=True, error="")
                print(f"  Sprach-Engine: ws://{cfg.bind}:{cfg.port}  (Silero VAD, Partials, Barge-in)", flush=True)
                await asyncio.Future()
        except OSError as e:
            STATE.update(running=False, error=f"Port {cfg.port}: {e}")
            log.warning("Sprach-Engine aus: %s", e)

    asyncio.run(main())


async def _safe_send(ws, data):
    try:
        await ws.send(data)
    except Exception:  # noqa: BLE001 — closed meanwhile
        pass


def _token_ok(supplied, token):
    import secrets
    return bool(supplied) and bool(token) and secrets.compare_digest(str(supplied), token)


def _device_of(ws, hello):
    """The PC's own browser is "pc"; a paired device sends its cookie and gets its id."""
    try:
        headers = ws.request.headers
    except AttributeError:
        headers = getattr(ws, "request_headers", {}) or {}
    cookie = headers.get("Cookie") or headers.get("cookie") or ""
    if cookie:
        try:
            import devices
            dev = devices.check(devices.token_from_cookie(cookie))
            if dev:
                return dev
        except Exception:  # noqa: BLE001
            pass
    return str(hello.get("device") or "pc")[:40]
