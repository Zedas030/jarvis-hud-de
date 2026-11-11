"""
Uptime Kuma alarms at once, and "Soll ich den Container neu starten?" — the restart only after a card.

Before 10.11.2026 Jarvis learnt about a Kuma outage from the homelab watcher (status page every 5 minutes,
two rounds to confirm), so up to ten minutes late, and could only report it. Now:

  1. The HA automation "ORBIT: Uptime-Kuma-Alarm aufs Handy" (Kuma's webhook) also fires the HA event
     `jarvis_alert` {source: kuma, name, status, msg}; ha_events.py receives it over its WebSocket and
     calls on_event(). Kuma has already confirmed the outage with its own retries, so the watcher runs
     at once and announces it without its second round (pushed_down()).
  2. A monitor that maps to a Docker container on the Pi (config/homelab.json → "kuma_restart",
     {"part of the monitor name": "container"}) becomes a proposal "… neu starten" (proposals.py, kind
     "restart"). Jarvis asks once; a "ja" in the next turn goes through proposal_accept and its card, and
     only then `docker restart <container>` runs over the same SSH as pi_run. Back up = the proposal is
     withdrawn.

  JARVIS_KUMA_RESTART   1 / 0 = no restart offers (announcing stays)
"""
import os
import re
import threading
import time

import jarvis_log

log = jarvis_log.get(__name__)

ENABLED = os.environ.get("JARVIS_KUMA_RESTART", "1").strip().lower() not in ("0", "false", "no", "off")
PUSH_VALID_S = 15 * 60             # a pushed outage counts as confirmed this long
CONTAINER = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,60}$")
_PUSHED = {}                        # monitor name -> (status, time)
_LOCK = threading.Lock()


def on_event(data):
    """HA event jarvis_alert (from ha_events.py). Never raises."""
    try:
        if (data or {}).get("source") != "kuma":
            return
        name = str(data.get("name") or "").strip()
        status = data.get("status")
        if not name or status not in (0, 1, "0", "1"):
            return
        with _LOCK:
            _PUSHED[name] = (int(status), time.time())
        log.info("Uptime Kuma meldet %s: %s", name, "ausgefallen" if int(status) == 0 else "wieder da")
        threading.Thread(target=_check_now, daemon=True, name="kuma-alert").start()
    except Exception as e:  # noqa: BLE001
        log.warning("Kuma-Meldung nicht verarbeitet: %s", e)


def _check_now():
    import homelab_watch
    time.sleep(3)                   # the status page needs a moment to show the new heartbeat
    homelab_watch.check()


def pushed_down(name, now=None):
    now = now or time.time()
    with _LOCK:
        st = _PUSHED.get(name)
    return bool(st and st[0] == 0 and now - st[1] < PUSH_VALID_S)


def container_for(name, cfg):
    for part, container in (cfg.get("kuma_restart") or {}).items():
        if part.lower() in name.lower() and CONTAINER.match(str(container)):
            return str(container)
    return None


def allowed(container, cfg):
    """Only containers named in the config may be restarted through a proposal."""
    return bool(CONTAINER.match(container or "")) and container in (cfg.get("kuma_restart") or {}).values()


def sync(monitors, cfg):
    """Offers a restart for a mapped monitor that is down, withdraws it when it is back. Returns new offers."""
    if not ENABLED:
        return []
    import events
    import proposals
    open_by_source = {p.get("source"): p for p in proposals.pending() if p["kind"] == "restart"}
    offers = []
    for m in monitors:
        container = container_for(m["name"], cfg)
        if not container:
            continue
        source = f"kuma:{m['name']}"
        if m["up"]:
            if source in open_by_source:
                proposals.resolve(open_by_source[source]["id"], "obsolete")
            continue
        if source in open_by_source:
            continue
        proposals.add("restart", f"Container {container} auf dem Pi neu starten",
                      f"Uptime Kuma meldet {m['name']} als ausgefallen.",
                      {"container": container, "monitor": m["name"]}, source=source)
        text = (f"Sir, {m['name']} ist ausgefallen. Soll ich den Container {container} auf dem Pi neu "
                "starten? Dann sagen Sie einfach ja.")
        events.publish("say", text, kind="homelab", key=f"kuma-restart:{container}")
        offers.append(container)
    return offers


def restart(container):
    """Runs after the card of proposal_accept. Returns the result in German."""
    import homelab_watch
    import jarvis_mcp
    if not allowed(container, homelab_watch.config()):
        return f"Den Container „{container}“ starte ich nicht neu — er steht nicht in kuma_restart."
    out = jarvis_mcp._pi_run(f"docker restart {container}")
    ok = out.startswith("exit 0")
    log.info("Neustart %s auf dem Pi: %s", container, out.splitlines()[0] if out else "?")
    return (f"Container {container} ist neu gestartet." if ok
            else f"Neustart von {container} hat nicht geklappt: {out[:200]}")
