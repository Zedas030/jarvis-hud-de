"""
Import this first in every test file: state, vault, lists, routines and the
learned files go to a temp folder, Home Assistant is switched off.

Modules read their paths once at import. If one of them was already imported
with the real paths (another test file imported it first), the tests would
write into the real state.json and vault — so that is refused outright.
"""
import os
import pathlib
import sys
import tempfile

_GUARDED = ("commands", "memory", "lists", "routines", "learning", "pronounce", "pc", "scheduler", "server",
            "proposals", "weekly_review", "homelab_watch", "suggestions", "reflection", "audit", "memory_tools",
            "emergency", "personality", "desktop", "infra", "assistant", "mail_events", "shipments", "spotify",
            "sonos", "energy_coach", "away_check", "homelab_review", "events", "house_habits", "weather_alerts",
            "alexa_voice", "morning_echo", "user_habits", "wellbeing", "maintenance", "focus", "selfcheck", "daytime",
            "episodes", "departure", "phone_actions", "proactive", "proactive_config", "proactive_sources")
_early = [m for m in _GUARDED if m in sys.modules and "isolate" not in sys.modules]
if _early:
    raise RuntimeError(f"tests/isolate.py must be imported before {', '.join(_early)} — real data at risk")

TMP = pathlib.Path(tempfile.mkdtemp(prefix="jarvis-test-"))
os.environ.update(JARVIS_STATE=str(TMP / "state.json"), JARVIS_VAULT=str(TMP / "vault"),
                  JARVIS_ROUTINES=str(TMP / "routines.json"), HA_TOKEN="", JARVIS_SHOPPING_LIST="",
                  JARVIS_TODO_LIST="", JARVIS_LOCAL_ROUTER="1", JARVIS_QUIET_HOURS="22-7",
                  JARVIS_PC_AUDIO="0",   # the PC's own sound (pc_audio.py) never makes a test strict
                  # no sign of life, no live HA, no NAS upload from a test; the index lives in the temp folder
                  JARVIS_HEARTBEAT="0", JARVIS_HA_WS="0", JARVIS_MEMORY_NAS="0",
                  JARVIS_MEMORY_INDEX=str(TMP / "memory_index.sqlite"),
                  JARVIS_LEARNED=str(TMP / "learned.json"), JARVIS_PRONOUNCE=str(TMP / "aussprache.json"),
                  JARVIS_APPS=str(TMP / "apps.json"), JARVIS_PROPOSALS=str(TMP / "proposals.json"),
                  JARVIS_HEALTH_DATA=str(TMP / "health.json"), JARVIS_HOMELAB_CONFIG=str(TMP / "homelab.json"),
                  JARVIS_HOMELAB_BASELINE=str(TMP / "baseline.json"), JARVIS_WORK_TERMS="Contoso",
                  JARVIS_AUDIT_LOG=str(TMP / "audit.jsonl"), JARVIS_LOG_FILE=str(TMP / "jarvis.log"), JARVIS_BACKUP_DIR=str(TMP / "backups"),
                  JARVIS_PROFILES=str(TMP / "profiles.json"), JARVIS_WORKSPACES=str(TMP / "workspaces.json"),
                  JARVIS_SCRIPTS=str(TMP / "scripts.json"), JARVIS_FILE_ROOTS=str(TMP / "files"),
                  JARVIS_PERM_URL="http://127.0.0.1:9/api/permission/request",
                  JARVIS_HOMELAB_HISTORY=str(TMP / "homelab_events.jsonl"), JARVIS_PHONE_NOTIFY="", JARVIS_TASKS=str(TMP / "tasks.json"), JARVIS_METRICS_DAILY=str(TMP / "metrics_daily.json"),
                  JARVIS_DOC_INDEX=str(TMP / "docindex.sqlite"), JARVIS_DOC_ROOTS="",
                  JARVIS_MAIL_EVENTS=str(TMP / "mail_events.json"), JARVIS_MAIL_EVENTS_DAILY="0",
                  JARVIS_SHIPMENTS_FILE=str(TMP / "shipments.json"), JARVIS_SHIPMENTS="0", DHL_API_KEY="", TRACK17_API_KEY="",
                  JARVIS_WAKE_LOG=str(TMP / "wake_device.log"), JARVIS_ENERGY_COACH="0", JARVIS_HOMELAB_REVIEW="0", JARVIS_HABITS="0", JARVIS_WEATHER_ALERTS="0",
                  JARVIS_NOTICES=str(TMP / "notices.jsonl"), JARVIS_POWER_PRICE="0.30",
                  JARVIS_PROACTIVE_CONFIG=str(TMP / "proactive.yaml"), JARVIS_PROACTIVE_MODE="",
                  JARVIS_PROACTIVE_HISTORY=str(TMP / "proactive_history.jsonl"),
                  JARVIS_ENERGY_COACH_STATE=str(TMP / "energy_coach.json"),
                  JARVIS_ENERGY_COACH_CONFIG=str(TMP / "energy_coach_config.json"),
                  JARVIS_SPOTIFY_CLIENT_ID="", JARVIS_SPOTIFY_TOKEN=str(TMP / "spotify_token.json"),
                  JARVIS_SPOTIFY_HELD=str(TMP / "spotify_held.json"), JARVIS_SPOTIFY_LOG=str(TMP / "spotify.log"),
                  JARVIS_SELFCHECK=str(TMP / "selfcheck.jsonl"), JARVIS_USER_HABITS="0", JARVIS_WELLBEING="0",
                  JARVIS_MAINTENANCE="0", JARVIS_POWER_BUDGET_EUR="0", JARVIS_WHISPER_HOURS="22-7",
                  PAPERLESS_URL="", PAPERLESS_TOKEN="", UNIFI_URL="", UNIFI_API_KEY="", UNIFI_USER="", UNIFI_PASSWORD="",
                  JELLYFIN_URL="", JELLYFIN_API_KEY="", JELLYFIN_USER="")
# The user's logins never reach a test: server.py and jarvis_mcp.py load .env with setdefault,
# so the empty values set here win. Before, the draft tests ran against the real mailboxes'
# settings and a router test logged in to the real NAS. A test that needs one brings a fake.
for _n in ("", *map(str, range(2, 10))):
    for _k in ("IMAP_HOST", "USER", "PASSWORD"):
        os.environ[f"MAIL{_n}_{_k}"] = ""
os.environ.update(SYNO_USER="", SYNO_PASSWORD="")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import paths  # noqa: E402,F401 — the jarvis/<group> folders

# The PC itself is never touched either. "Musik leiser" in the router tests reached jarvis_mcp._music,
# which pressed the real volume-down key five times, and pc.py's Core Audio calls set the real master
# volume — every test run turned the PC down by about 20 % (05.11.2026). Keys are recorded in KEYS
# instead; without an endpoint, pc.volume() reads None and pc.step falls back to those recorded keys.
KEYS = []
if sys.platform == "win32":
    import ctypes  # noqa: E402
    ctypes.windll.user32.keybd_event = lambda vk, scan=0, flags=0, extra=0: KEYS.append((vk, flags))
import pc  # noqa: E402


def _no_endpoint():
    raise RuntimeError("tests never touch the real speakers")


pc._endpoint = _no_endpoint
