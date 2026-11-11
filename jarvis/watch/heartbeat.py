"""
Sign of life for Home Assistant — so a dead Jarvis gets noticed.

On 29.10.2026 Jarvis crashed during a self-restart and stayed down for days without anyone noticing.
Jarvis cannot report his own death, so the check lives in Home Assistant on the Pi: every minute this
thread asks the own HTTP server for a page (proof that requests are really answered, not just that a
thread is alive) and then stamps the time into input_datetime.jarvis_lebenszeichen. The automation
"Jarvis: Lebenszeichen-Wächter" (scripts/heartbeat_ha_setup.py) sends a push to the phone when the PC
draws power for 10 minutes while the stamp is older than 10 minutes. A PC that is off draws nothing,
so a switched-off PC is never an alarm.

  JARVIS_HEARTBEAT          1 (default) / 0 = off
  JARVIS_HEARTBEAT_S        seconds between two stamps (60)
  JARVIS_HEARTBEAT_ENTITY   the helper in HA (input_datetime.jarvis_lebenszeichen)

Only a supervised child (JARVIS_CHILD=1) or the plain server sends it; the test instance on another port
must not, or it would cover for a dead live Jarvis — so it only runs on JARVIS_PORT 8720 unless
JARVIS_HEARTBEAT=1 is set explicitly.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

import jarvis_log

log = jarvis_log.get(__name__)

HA_URL = os.environ.get("HA_URL", "").strip().rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "").strip()
EVERY = max(15, int(os.environ.get("JARVIS_HEARTBEAT_S", "60") or 60))
ENTITY = os.environ.get("JARVIS_HEARTBEAT_ENTITY", "input_datetime.jarvis_lebenszeichen").strip()
STATE = {"last_ok": 0.0, "last_error": "", "fails": 0, "running": False}


def enabled(port):
    """On by default for the live instance (port 8720), off for test instances and without HA."""
    flag = os.environ.get("JARVIS_HEARTBEAT", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if not (HA_URL and HA_TOKEN and ENTITY):
        return False
    return flag in ("1", "true", "yes", "on") or int(port) == 8720


def self_answers(port, timeout=5):
    """True when the own server answers an HTTP request at all — any status code counts."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True          # 404 (metrics switched off) is still an answer
    except OSError:
        return False


def stamp(now=None):
    """Write the current time into the HA helper. Returns True on success."""
    body = json.dumps({"entity_id": ENTITY, "timestamp": int(now or time.time())}).encode("utf-8")
    req = urllib.request.Request(f"{HA_URL}/api/services/input_datetime/set_datetime", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {HA_TOKEN}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return 200 <= r.status < 300


def beat(port):
    """One round: check the own server, then stamp. Never raises."""
    if not self_answers(port):
        STATE["last_error"] = "eigener Server antwortet nicht"
        return False
    try:
        ok = stamp()
    except (OSError, ValueError) as e:
        ok = False
        STATE["last_error"] = str(e)[:160]
    if ok:
        if STATE["fails"] >= 3:
            log.info("Lebenszeichen an Home Assistant geht wieder durch")
        STATE.update(last_ok=time.time(), last_error="", fails=0)
    else:
        STATE["fails"] += 1
        if STATE["fails"] == 3:     # once per outage, not every minute
            log.warning("Lebenszeichen an Home Assistant schlägt fehl: %s", STATE["last_error"])
    return ok


def start(port):
    if not enabled(port) or STATE["running"]:
        return False
    STATE["running"] = True

    def loop():
        time.sleep(10)          # let the server bind its port first
        while True:
            beat(port)
            time.sleep(EVERY)
    threading.Thread(target=loop, daemon=True, name="heartbeat").start()
    return True


def status():
    return dict(STATE, entity=ENTITY, every=EVERY)
