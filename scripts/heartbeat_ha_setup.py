"""
Sets up the watchdog for Jarvis in Home Assistant (running it again only updates the automation):

  input_datetime.jarvis_lebenszeichen       "Jarvis Lebenszeichen": Jarvis stamps it every minute (heartbeat.py)
  automation "Jarvis: Lebenszeichen-Wächter" push to the phone when the PC draws power for 10 minutes
                                             while the stamp is older than 10 minutes

Why in HA: Jarvis cannot report his own death (29.10.2026 he was down for days unnoticed). Why the PC's power:
Jarvis does not run 24/7 — a PC that is off is not an alarm, a PC that runs without Jarvis is.

Reads from .env: HA_URL, HA_TOKEN, JARVIS_PHONE_NOTIFY (e.g. notify.mobile_app_phone) and
JARVIS_HEARTBEAT_PC_POWER (the power sensor of the PC's smart plug, in W; JARVIS_HEARTBEAT_PC_WATT is the
threshold, default 80 — standby stays below 40, a running PC above 100). Run from the repository root:

    .venv\\Scripts\\python.exe scripts\\heartbeat_ha_setup.py
"""
import json
import os
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER = "Jarvis Lebenszeichen"
ENTITY = "input_datetime.jarvis_lebenszeichen"
AUTOMATION_ID = "jarvis_lebenszeichen_waechter"


def env():
    dotenv = pathlib.Path(os.environ.get("JARVIS_ENV_FILE", ROOT / ".env"))
    for line in (dotenv.read_text(encoding="utf-8").splitlines() if dotenv.exists() else []):
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))
    return os.environ.get("HA_URL", "").rstrip("/"), os.environ.get("HA_TOKEN", "")


def automation(power, watt, notify):
    stale = (f"{{{{ states('{power}') | float(0) > {watt} and "
             f"(as_timestamp(now()) - (state_attr('{ENTITY}', 'timestamp') | float(0))) > 600 }}}}")
    age = f"{{{{ ((as_timestamp(now()) - (state_attr('{ENTITY}', 'timestamp') | float(0))) / 60) | round(0) | int }}}}"
    domain, _, service = notify.partition(".")
    return {
        "alias": "Jarvis: Lebenszeichen-Wächter",
        "description": ("Jarvis stempelt jede Minute input_datetime.jarvis_lebenszeichen (heartbeat.py). Zieht der PC "
                        f"10 min lang über {watt:g} W und ist der Stempel älter als 10 min, kommt eine Meldung aufs Handy, "
                        "einmal je Ausfall. PC aus = keine Meldung. Angelegt von scripts/heartbeat_ha_setup.py."),
        "mode": "single",
        "triggers": [{"trigger": "template", "value_template": stale, "for": {"minutes": 10}}],
        "conditions": [],
        "actions": [
            {"action": "logbook.log", "data": {"name": "Jarvis Watchdog", "entity_id": ENTITY,
                                               "message": f"Jarvis meldet sich seit {age} min nicht, der PC läuft."}},
            {"action": f"{domain}.{service}", "data": {
                "title": "Jarvis antwortet nicht",
                "message": (f"Der PC läuft, aber Jarvis hat sich seit {age} Minuten nicht gemeldet. "
                            "Am PC start-jarvis.bat starten; die Ursache steht in logs/jarvis.log."),
                "data": {"tag": "jarvis_heartbeat"}}},
        ],
    }


def main():
    from websockets.sync.client import connect
    url, token = env()
    notify = os.environ.get("JARVIS_PHONE_NOTIFY", "").strip()
    power = os.environ.get("JARVIS_HEARTBEAT_PC_POWER", "").strip()
    watt = float(os.environ.get("JARVIS_HEARTBEAT_PC_WATT", "80") or 80)
    if not url or not token:
        sys.exit("HA_URL / HA_TOKEN fehlen in .env")
    if not notify.startswith("notify.") or not power.startswith("sensor."):
        sys.exit("JARVIS_PHONE_NOTIFY (notify.…) und JARVIS_HEARTBEAT_PC_POWER (sensor.…) in .env eintragen")

    ws_url = url.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
    with connect(ws_url, open_timeout=10) as ws:
        json.loads(ws.recv())
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(ws.recv()).get("type") != "auth_ok":
            sys.exit("Anmeldung an Home Assistant fehlgeschlagen")
        ws.send(json.dumps({"id": 1, "type": "input_datetime/list"}))
        have = {h["name"] for h in json.loads(ws.recv()).get("result") or []}
        if HELPER in have:
            print(f"vorhanden: {HELPER}")
        else:
            ws.send(json.dumps({"id": 2, "type": "input_datetime/create", "name": HELPER,
                                "has_date": True, "has_time": True, "icon": "mdi:heart-pulse"}))
            r = json.loads(ws.recv())
            print(("angelegt: " if r.get("success") else "FEHLER: ") + HELPER, r.get("error") or "")
            if not r.get("success"):
                sys.exit(1)

    body = json.dumps(automation(power, watt, notify), ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{url}/api/config/automation/config/{AUTOMATION_ID}", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        print("Automation gespeichert:", r.status, r.read().decode("utf-8", "replace")[:80])


if __name__ == "__main__":
    main()
