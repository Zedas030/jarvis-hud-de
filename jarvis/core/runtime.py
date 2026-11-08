"""
Claude Code runtime for the JARVIS memory HUD.

The brain is your own `claude` CLI, running headless (`claude -p`) with
`--output-format stream-json`. That means it authenticates the same way your
normal Claude Code sessions do — your subscription, no API key, no per-token
billing — and it keeps the same tools, MCP servers, skills and CLAUDE.md
context that the interactive CLI has.

Never pass --bare: it forces ANTHROPIC_API_KEY auth and would bypass the
subscription this whole dashboard is built around.
"""
import collections
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid

import hostos

# Sonnet by default: plenty for everyday questions and much lighter on the
# subscription limit than Opus.
MODEL = os.environ.get("JARVIS_MODEL", "sonnet").strip()
WORKDIR = os.path.expanduser(os.environ.get("JARVIS_WORKDIR", os.getcwd()))
# bypassPermissions by default: a headless `claude -p` can't surface a permission
# prompt, so any gated tool (MCP connectors, web search) would silently fail
# otherwise. This also lets JARVIS take unattended actions (send/delete/shell) —
# see docs/sicherheit.md and SECURITY.md. Set JARVIS_PERMISSION=acceptEdits to narrow it.
PERMISSION = os.environ.get("JARVIS_PERMISSION", "acceptEdits").strip()
# Tools JARVIS may use without a prompt. No Bash: a voice-driven assistant should
# not run shell commands because something in the room said so. acceptEdits keeps
# file writes inside WORKDIR (where the vault lives).
ALLOWED_TOOLS = os.environ.get(
    "JARVIS_ALLOWED_TOOLS",
    "Read,Write,Edit,Glob,Grep,WebSearch,WebFetch,TodoWrite").strip()
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository
RUNTIME = os.environ.get("JARVIS_RUNTIME", "auto").strip().lower()   # auto|claude|mock
IDLE_TIMEOUT = int(os.environ.get("JARVIS_TIMEOUT", "180"))
RAW_LOG = os.environ.get("JARVIS_RAW_LOG", "").strip()
# Keep one claude process alive between turns (saves ~3 s per answer).
PERSISTENT = os.environ.get("JARVIS_PERSISTENT", "1").strip() not in ("0", "false", "no", "off")

# One Claude subprocess at a time: session state is shared, and the browser
# Escape key must be able to kill real backend work, not just the fetch.
EXECUTION_LOCK = threading.RLock()
_ACTIVE_PROC = None
_ACTIVE_LOCK = threading.Lock()

# Hook chatter and internal bookkeeping the HUD should never render.
_NOISE_SUBTYPES = {"hook_started", "hook_response", "post_turn_summary",
                   "compact_boundary", "mcp_status"}


# Counts cancels: a turn that was cancelled before its Claude process started (still fetching mail
# for the context, say) checks this before it starts one (cancelled_since).
_CANCELS = [0]


def cancel_mark():
    return _CANCELS[0]


def cancelled_since(mark):
    return _CANCELS[0] != mark


def cancel_active():
    with _ACTIVE_LOCK:
        _CANCELS[0] += 1
        proc = _ACTIVE_PROC
    if proc is None or proc.poll() is not None:
        return False
    try:
        proc.terminate()
        return True
    except Exception:
        return False


def valid_session(sid):
    """Claude Code session ids are UUIDs."""
    try:
        return bool(sid) and str(uuid.UUID(str(sid))) == str(sid).lower()
    except (ValueError, AttributeError, TypeError):
        return False


def _claude_base():
    configured = os.environ.get("CLAUDE_CMD", "").strip()
    if configured:
        if os.path.exists(os.path.expanduser(configured)):
            return [os.path.expanduser(configured)]
        return shlex.split(configured, posix=(os.name != "nt"))
    exe = shutil.which("claude")
    if exe and os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        # npm's claude.cmd shim goes through cmd.exe, which mangles long prompts
        # with quotes or & in them. Call the real binary it wraps instead.
        native = os.path.join(os.path.dirname(exe), "node_modules", "@anthropic-ai",
                              "claude-code", "bin", "claude.exe")
        if os.path.exists(native):
            return [native]
    if exe:
        return [exe]
    local = os.path.expanduser("~/.local/bin/claude")
    if os.path.exists(local):
        return [local]
    return ["claude"]


def _clean_env():
    """Never let an API key or a parent Claude session leak into the child:
    ANTHROPIC_API_KEY would silently switch billing away from the subscription."""
    env = dict(os.environ)
    for k in list(env):
        if k == "ANTHROPIC_API_KEY" or k.startswith("CLAUDE_CODE_") or k in (
                "CLAUDECODE", "ANTHROPIC_BASE_URL"):
            env.pop(k, None)
    return env


def _can_launch():
    try:
        p = subprocess.run(_claude_base() + ["--version"], cwd=WORKDIR, text=True,
                           encoding="utf-8", errors="replace", env=_clean_env(),
                           **hostos.QUIET,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        return p.returncode == 0, (p.stdout or p.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return False, str(e)


_KIND = {"v": None, "at": 0.0}
# Home Assistant MCP server attached? Toggled by server.py when the user asks for
# it; a fresh session starts without it.
HA = {"on": False}


def runtime_kind():
    if RUNTIME in ("mock", "claude"):
        return RUNTIME
    if _KIND["v"] == "claude" and time.monotonic() - _KIND["at"] < 300:
        return "claude"
    ok, _ = _can_launch()
    _KIND.update(v="claude" if ok else "mock", at=time.monotonic())
    return _KIND["v"]


def version():
    ok, detail = _can_launch()
    return detail if ok else "unavailable"


def _mcp_config():
    """Write the config for JARVIS' own MCP server (permission gate + browser
    opener). The per-launch token lets the gate prove itself to the HUD."""
    script = os.path.join(REPO, "jarvis", "tools", "jarvis_mcp.py")
    if not os.path.exists(script):
        return None
    port = os.environ.get("JARVIS_PORT", "8720")
    cfg = {"mcpServers": {"jarvis": {
        "command": sys.executable, "args": ["-I", script],
        "env": {"JARVIS_PERM_URL": f"http://127.0.0.1:{port}/api/permission/request",
                "JARVIS_PERM_TOKEN": os.environ.get("JARVIS_PERM_TOKEN", ""),
                "JARVIS_PERM_TIMEOUT": os.environ.get("JARVIS_PERM_TIMEOUT", "75"),
                "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
                "JARVIS_BROWSER": os.environ.get("JARVIS_BROWSER", "")}}}}
    # ha-mcp, embedded in Home Assistant (custom component ha_mcp_tools). The
    # secret webhook URL is the credential, so it lives only in .env. Loaded only
    # on demand (HA["on"]): its ~40 tools and skill text weigh on every turn.
    ha_mcp = os.environ.get("HA_MCP_URL", "").strip()
    if ha_mcp and HA["on"]:
        cfg["mcpServers"]["homeassistant"] = {"type": "http", "url": ha_mcp}
    path = os.path.join(REPO, ".jarvis-mcp.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    return path


def build_command(message, session_id=None, system=None):
    head = ["-p", "--input-format", "stream-json"] if message is None else ["-p", message]
    cmd = _claude_base() + head + [
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
    ]
    if valid_session(session_id):
        cmd += ["--resume", str(session_id)]
    if MODEL:
        cmd += ["--model", MODEL]
    if PERMISSION:
        cmd += ["--permission-mode", PERMISSION]
    if ALLOWED_TOOLS and PERMISSION != "bypassPermissions":
        cmd += ["--allowedTools", ",".join([ALLOWED_TOOLS] + [f"mcp__jarvis__{t}" for t in sorted(NO_CONFIRM)])]
    mcp = _mcp_config()
    if mcp:
        cmd += ["--mcp-config", mcp]
        if os.environ.get("JARVIS_STRICT_MCP", "").strip() in ("1", "true", "yes"):
            # Only JARVIS' own tools: skips loading claude.ai connectors on every
            # turn, which costs seconds of startup.
            cmd += ["--strict-mcp-config"]
        if PERMISSION != "bypassPermissions":
            # Anything not pre-allowed is routed to the HUD for the user to confirm.
            cmd += ["--permission-prompt-tool", "mcp__jarvis__approve"]
    if system:
        cmd += ["--append-system-prompt", system]
    return cmd


def _short(value, limit=90):
    """Collapse a tool input dict into one readable line for the action log."""
    if isinstance(value, dict):
        for key in ("command", "query", "prompt", "file_path", "pattern", "path", "url"):
            if key in value and isinstance(value[key], str):
                value = value[key]
                break
        else:
            value = json.dumps(value)
    text = " ".join(str(value).split())
    return text[:limit] + ("…" if len(text) > limit else "")


def run_claude(message, session_id=None, system=None, prefix=""):
    with EXECUTION_LOCK:
        if PERSISTENT:
            yield from _run_warm(message, session_id, system, prefix)
        else:
            text = (prefix + "\n\n" + message) if prefix else message
            yield from _run_locked(text, session_id, system)


def _env_for_child():
    env = _clean_env()
    home = os.path.expanduser("~")
    env["PATH"] = os.pathsep.join(dict.fromkeys([
        env.get("PATH", ""), os.path.join(home, ".local", "bin"),
        "/opt/homebrew/bin", "/usr/local/bin",
        os.path.join(home, ".npm-global", "bin"),
        "/usr/bin", "/bin", "/usr/sbin", "/sbin",
    ]))
    return env


class _Proc:
    """A claude process plus the threads that drain its stdout/stderr."""

    def __init__(self, cmd, stdin=False):
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=WORKDIR, text=True, bufsize=1, encoding="utf-8", errors="replace",
                stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env_for_child(),
                **hostos.QUIET)
        except FileNotFoundError:
            raise RuntimeError("claude CLI not found. Install Claude Code or set CLAUDE_CMD.")
        self.q = collections.deque()
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.errbuf = collections.deque(maxlen=120)
        threading.Thread(target=self._pump_out, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    def _pump_out(self):
        try:
            for ln in self.proc.stdout:
                with self.lock:
                    self.q.append(ln)
        except Exception:
            pass
        finally:
            self.done.set()

    def _pump_err(self):
        try:
            for ln in self.proc.stderr:
                self.errbuf.append(ln.rstrip())
        except Exception:
            pass

    def alive(self):
        return self.proc.poll() is None

    def kill(self):
        if self.alive():
            try:
                self.proc.terminate()
            except Exception:
                pass


def _consume(p, started, session_id):
    """Map claude's stream-json lines to HUD events until one turn's `result`.
    Yields events; the final event is always `complete` or `error`."""
    emitted_session = session_id
    last_usage = {}
    call_ids, tool_chars = set(), [0]
    streamed_text = False       # partial deltas arrived -> ignore final text blocks
    text_seen = False
    first = True
    last = time.monotonic()
    while True:
        line = None
        with p.lock:
            if p.q:
                line = p.q.popleft()
        if line is None:
            if p.done.is_set() and not p.q:
                rc = p.proc.wait(timeout=5)
                if rc != 0 or not text_seen:
                    yield dict(t="error", message=(" | ".join(list(p.errbuf)[-6:])
                                                   or f"claude exited with code {rc}")[:500])
                    return
                yield dict(t="complete", session_id=emitted_session,
                           ms=int((time.monotonic() - started) * 1000))
                return
            if time.monotonic() - last > IDLE_TIMEOUT:
                p.kill()
                yield dict(t="error", message=(
                    f"Claude went quiet for {IDLE_TIMEOUT}s and was stopped. "
                    + " | ".join(list(p.errbuf)[-3:]))[:400])
                return
            time.sleep(0.03)
            continue
        last = time.monotonic()
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue

        kind = ev.get("type")
        sid = ev.get("session_id")
        if valid_session(sid):
            emitted_session = sid

        if kind == "system":
            if ev.get("subtype") == "init":
                yield dict(t="status", model=ev.get("model") or MODEL or "Claude default",
                           tools=len(ev.get("tools") or []),
                           mcp=[s.get("name") for s in (ev.get("mcp_servers") or [])],
                           permission=ev.get("permissionMode") or PERMISSION,
                           session_id=sid, runtime="claude")
            continue

        if kind == "rate_limit_event":
            info = ev.get("rate_limit_info") or {}
            wins = info.get("unifiedWindows") or {}
            yield dict(t="ratelimit", status=info.get("status"),
                       window=info.get("rateLimitType"), resets=info.get("resetsAt"),
                       five_hour=wins.get("five_hour"), seven_day=wins.get("seven_day"),
                       overage=info.get("overageStatus"),
                       overage_reason=info.get("overageDisabledReason"),
                       using_overage=info.get("isUsingOverage"))
            continue

        if kind == "stream_event":
            inner = ev.get("event") or {}
            if inner.get("type") == "content_block_delta":
                delta = inner.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    if first:
                        first = False
                        yield dict(t="latency", ms=int((time.monotonic() - started) * 1000))
                    streamed_text = True
                    text_seen = True
                    yield dict(t="delta", text=delta["text"])
            continue

        if kind == "assistant":
            u = (ev.get("message") or {}).get("usage") or {}
            if u:
                last_usage = u
                mid = (ev.get("message") or {}).get("id")
                if mid and mid not in call_ids:
                    call_ids.add(mid)
            for block in (ev.get("message") or {}).get("content") or []:
                btype = block.get("type")
                if btype == "text" and not streamed_text and block.get("text"):
                    if first:
                        first = False
                        yield dict(t="latency", ms=int((time.monotonic() - started) * 1000))
                    text_seen = True
                    yield dict(t="delta", text=block["text"])
                elif btype == "tool_use":
                    yield dict(t="tool", phase="use", name=block.get("name", "tool"),
                               input=_short(block.get("input", "")),
                               args=block.get("input") if isinstance(block.get("input"), dict) else None)
            continue

        if kind == "user":
            for block in (ev.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    c = block.get("content")
                    tool_chars[0] += len(c) if isinstance(c, str) else sum(
                        len(x.get("text", "")) for x in (c or []) if isinstance(x, dict))
                    yield dict(t="tool", phase="result",
                               ok=not block.get("is_error"),
                               name=block.get("name", ""))
            continue

        if kind == "result":
            usage = ev.get("usage") or last_usage or {}
            if ev.get("subtype") != "success" and not text_seen:
                yield dict(t="error", message=str(ev.get("result") or "Claude returned an error")[:400])
                return
            if not text_seen and ev.get("result"):
                yield dict(t="delta", text=str(ev["result"]))
                text_seen = True
            yield dict(t="usage",
                       input_tokens=usage.get("input_tokens", 0),
                       output_tokens=usage.get("output_tokens", 0),
                       cache_read=usage.get("cache_read_input_tokens", 0),
                       cache_write=usage.get("cache_creation_input_tokens", 0),
                       api_calls=len(call_ids), tool_result_chars=tool_chars[0],
                       cost_usd=ev.get("total_cost_usd"),
                       total_tokens=(usage.get("input_tokens", 0) + usage.get("output_tokens", 0)),
                       # what the last API call re-sent, cached or not — what the session weighs now
                       context_tokens=sum((last_usage or usage).get(k, 0) for k in (
                           "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")))
            yield dict(t="complete", session_id=emitted_session,
                       ms=ev.get("duration_ms") or int((time.monotonic() - started) * 1000))
            return


def _run_locked(message, session_id=None, system=None):
    """One process per turn (JARVIS_PERSISTENT=0): slow but simple."""
    global _ACTIVE_PROC
    started = time.monotonic()
    p = _Proc(build_command(message, session_id, system))
    with _ACTIVE_LOCK:
        _ACTIVE_PROC = p.proc
    try:
        yield from _consume(p, started, session_id)
    finally:
        p.kill()
        with _ACTIVE_LOCK:
            if _ACTIVE_PROC is p.proc:
                _ACTIVE_PROC = None


# ── warm process ────────────────────────────────────────────────
# Starting `claude` costs ~3 s per turn. Instead one process stays alive in
# stream-json input mode and every turn is written to its stdin. The static
# system prompt (persona, memory) is fixed per process; when it changes the
# process is replaced in the background right after the turn (prewarm), so the
# next question finds a ready process. Per-turn context (time, calendar,
# matching notes) travels in front of the user's message instead.
_WARM = {"p": None, "session": None, "system": None, "ha": False, "fresh": True}
_WARM_LOCK = threading.Lock()


def _spawn(session_id, system):
    p = _Proc(build_command(None, session_id, system), stdin=True)
    _WARM.update(p=p, session=session_id if valid_session(session_id) else None,
                 system=system, ha=HA["on"], fresh=True)
    return p


def shutdown():
    """Kill the warm and any running Claude process — before a restart."""
    cancel_active()
    with _WARM_LOCK:
        if _WARM["p"]:
            _WARM["p"].kill()
            _WARM["p"] = None


def prewarm(session_id, system):
    """Make sure a process for (session, system) is running. Cheap if it is."""
    if not PERSISTENT or runtime_kind() != "claude":
        return
    with _WARM_LOCK:
        p = _WARM["p"]
        if p and p.alive() and _WARM["system"] == system and _WARM["ha"] == HA["on"] and (
                _WARM["session"] == (session_id if valid_session(session_id) else None)):
            return
        if p:
            p.kill()
        _spawn(session_id, system)


def _run_warm(message, session_id, system, prefix):
    global _ACTIVE_PROC
    started = time.monotonic()
    want_session = session_id if valid_session(session_id) else None
    with _WARM_LOCK:
        p = _WARM["p"]
        if not (p and p.alive() and _WARM["system"] == system and _WARM["ha"] == HA["on"]
                and _WARM["session"] == want_session):
            if p:
                p.kill()
            p = _spawn(session_id, system)
        _WARM["fresh"] = False
    with _ACTIVE_LOCK:
        _ACTIVE_PROC = p.proc
    content = (prefix + "\n\n" + message) if prefix else message
    try:
        p.proc.stdin.write(json.dumps({"type": "user", "message": {
            "role": "user", "content": content}}) + "\n")
        p.proc.stdin.flush()
    except (OSError, ValueError) as e:
        p.kill()
        yield dict(t="error", message=f"Claude-Prozess nicht erreichbar: {e}")
        return
    ok = False
    try:
        for ev in _consume(p, started, session_id):
            if ev.get("t") == "complete":
                ok = True
                if valid_session(ev.get("session_id")):
                    _WARM["session"] = ev["session_id"]
            yield ev
    finally:
        if not ok:
            p.kill()
        with _ACTIVE_LOCK:
            if _ACTIVE_PROC is p.proc:
                _ACTIVE_PROC = None


def run_mock(message, session_id=None, system=None):
    ok, detail = _can_launch()
    yield dict(t="error", message=("Claude Code is not reachable from this process. "
                                   "Install it, or set CLAUDE_CMD to the executable. "
                                   f"Diagnostic: {detail[:200]}"))


def run(message, session_id=None, system=None, prefix=""):
    if runtime_kind() != "claude":
        yield from run_mock(message, session_id, system)
        return
    try:
        yield from run_claude(message, session_id, system, prefix)
    except Exception as e:  # noqa: BLE001
        yield dict(t="error", message=f"could not start Claude core: {e}")


# ── lean mode (JARVIS_LEAN=1) ───────────────────────────────────
# Every request is a fresh, stateless call with:
#   - our own short system prompt instead of Claude Code's default (--system-prompt)
#   - no built-in tools except the ones a group needs (--tools)
#   - only the Jarvis tool groups the router picked (JARVIS_TOOL_GROUPS)
#   - no claude.ai connectors, settings or CLAUDE.md (--strict-mcp-config, --setting-sources "")
#   - no session: the conversation travels as a short window in the prefix
# One idle process per configuration is kept warm, used once, then replaced.
LEAN = os.environ.get("JARVIS_LEAN", "1").strip() not in ("0", "false", "no", "off")
LEAN_EFFORT = os.environ.get("JARVIS_EFFORT", "low").strip()
MAX_OUTPUT = os.environ.get("JARVIS_MAX_OUTPUT_TOKENS", "1500").strip()
POOL_SIZE = int(os.environ.get("JARVIS_POOL_SIZE", "3"))
# built-in Claude Code tools a group brings along (everything else stays off)
GROUP_BUILTINS = {"web": ["WebSearch"], "research": ["WebSearch"], "pc": [hostos.shell_tool()], "ha_full": ["ToolSearch"]}
# tools that run without a confirmation card; the rest goes through mcp__jarvis__approve
import policy  # noqa: E402  — READ and LIMITED_WRITE run without a card; policy.py has the table
NO_CONFIRM = policy.no_confirm()

_POOL = {}            # key -> (_Proc, spawned_at)
_POOL_LOCK = threading.Lock()


def _lean_mcp_config(groups):
    script = os.path.join(REPO, "jarvis", "tools", "jarvis_mcp.py")
    port = os.environ.get("JARVIS_PORT", "8720")
    tool_groups = ",".join(g for g in groups if g != "ha_full")
    cfg = {"mcpServers": {"jarvis": {
        "command": sys.executable, "args": ["-I", script],
        "env": {"JARVIS_PERM_URL": f"http://127.0.0.1:{port}/api/permission/request",
                "JARVIS_PERM_TOKEN": os.environ.get("JARVIS_PERM_TOKEN", ""),
                "JARVIS_PERM_TIMEOUT": os.environ.get("JARVIS_PERM_TIMEOUT", "75"),
                "JARVIS_TOOL_GROUPS": tool_groups or "none",
                "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
                "JARVIS_BROWSER": os.environ.get("JARVIS_BROWSER", "")}}}}
    ha_mcp = os.environ.get("HA_MCP_URL", "").strip()
    if ha_mcp and "ha_full" in groups:
        cfg["mcpServers"]["homeassistant"] = {"type": "http", "url": ha_mcp}
    name = ".jarvis-mcp-" + ("-".join(sorted(groups)) or "none") + ".json"
    path = os.path.join(REPO, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    return path


def lean_command(model, groups, system):
    import jarvis_mcp
    builtins = sorted({b for g in groups for b in GROUP_BUILTINS.get(g, [])})
    allowed = [f"mcp__jarvis__{t}" for g in groups for t in jarvis_mcp.GROUPS.get(g, set()) if t in NO_CONFIRM]
    allowed += [b for b in builtins if b in ("WebSearch", "ToolSearch")]
    if "ha_full" in groups:
        allowed.append("mcp__homeassistant")
    cmd = _claude_base() + [
        "-p", "--input-format", "stream-json", "--output-format", "stream-json",
        "--include-partial-messages", "--verbose",
        "--model", model, "--system-prompt", system,
        "--tools", ",".join(builtins),
        "--strict-mcp-config", "--mcp-config", _lean_mcp_config(groups),
        "--setting-sources", "", "--no-session-persistence", "--disable-slash-commands",
        "--permission-mode", "default", "--permission-prompt-tool", "mcp__jarvis__approve",
    ]
    if LEAN_EFFORT:
        cmd += ["--effort", LEAN_EFFORT]
    if allowed:
        cmd += ["--allowedTools", ",".join(sorted(set(allowed)))]
    return cmd


class _LeanProc(_Proc):
    def __init__(self, cmd):
        env = _env_for_child()
        if MAX_OUTPUT:
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = MAX_OUTPUT   # set after cleaning, so it survives
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=WORKDIR, text=True, bufsize=1, encoding="utf-8", errors="replace",
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                **hostos.QUIET)
        except FileNotFoundError:
            raise RuntimeError("claude CLI not found. Install Claude Code or set CLAUDE_CMD.")
        self.q = collections.deque()
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.errbuf = collections.deque(maxlen=120)
        threading.Thread(target=self._pump_out, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()


def _lean_key(model, groups, system):
    import hashlib
    return (model, tuple(groups), hashlib.sha1(system.encode("utf-8")).hexdigest()[:12])


def lean_prewarm(model, groups, system):
    """Start an idle process for this configuration (if none is waiting)."""
    if runtime_kind() != "claude":
        return
    key = _lean_key(model, groups, system)
    with _POOL_LOCK:
        cur = _POOL.get(key)
        if cur and cur[0].alive():
            return
        while len(_POOL) >= POOL_SIZE:              # evict the oldest idle process
            old = min(_POOL, key=lambda k: _POOL[k][1])
            _POOL.pop(old)[0].kill()
        _POOL[key] = (_LeanProc(lean_command(model, groups, system)), time.time())


def lean_shutdown():
    with _POOL_LOCK:
        for p, _ in _POOL.values():
            p.kill()
        _POOL.clear()


def run_lean(message, model, groups, system, prefix=""):
    """One stateless request. Yields the same events as run()."""
    global _ACTIVE_PROC
    if runtime_kind() != "claude":
        yield from run_mock(message)
        return
    started = time.monotonic()
    key = _lean_key(model, groups, system)
    with EXECUTION_LOCK:
        with _POOL_LOCK:
            entry = _POOL.pop(key, None)
        p = entry[0] if entry and entry[0].alive() else None
        if p is None:
            p = _LeanProc(lean_command(model, groups, system))
        with _ACTIVE_LOCK:
            _ACTIVE_PROC = p.proc
        content = (prefix + "\n\n" + message) if prefix else message
        try:
            p.proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n")
            p.proc.stdin.flush()
            yield from _consume(p, started, None)
        except (OSError, ValueError) as e:
            yield dict(t="error", message=f"Claude-Prozess nicht erreichbar: {e}")
        finally:
            p.kill()                                   # stateless: never reuse a process
            with _ACTIVE_LOCK:
                if _ACTIVE_PROC is p.proc:
                    _ACTIVE_PROC = None
    threading.Thread(target=lean_prewarm, args=(model, groups, system), daemon=True).start()
