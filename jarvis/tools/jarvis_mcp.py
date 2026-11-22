"""
JARVIS' own MCP server (stdio, standard library only). Claude Code starts it
for every turn via --mcp-config. Two tools:

- approve       — the --permission-prompt-tool. Every tool call that is not
                  pre-allowed (shell commands, writes outside the Jarvis folder,
                  ...) lands here. It asks the running HUD server, which shows a
                  confirmation card to the user and blocks until he answers.
                  No answer within the timeout means deny.
- open_website  — opens an http(s) URL in the configured browser. Harmless, so it is
                  pre-allowed and needs no confirmation.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository
sys.path.insert(0, HERE)
import paths  # noqa: E402,F401 — puts the jarvis/<group> folders on sys.path (this runs as its own process)
import hostos  # noqa: E402
import jarvis_log

log = jarvis_log.get(__name__)
_env = os.path.join(HERE, ".env")
if os.path.exists(_env):   # calendar credentials etc. — read directly, never via argv
    for _line in open(_env, encoding="utf-8"):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
sys.path.insert(0, HERE)

PERM_URL = os.environ.get("JARVIS_PERM_URL", "http://127.0.0.1:8720/api/permission/request")
PERM_TOKEN = os.environ.get("JARVIS_PERM_TOKEN", "")
PERM_TIMEOUT = int(os.environ.get("JARVIS_PERM_TIMEOUT", "75"))
# Browser for open_website: full path in JARVIS_BROWSER, empty = the system default browser.
BROWSER = os.path.expandvars(os.environ.get("JARVIS_BROWSER", "").strip())

TOOLS = [
    {
        "name": "approve",
        "description": "Permission gate: asks the user to confirm a tool call in the HUD.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string"},
                "input": {"type": "object"},
                "tool_use_id": {"type": "string"},
            },
            "required": ["tool_name", "input"],
        },
    },
    {
        "name": "open_website",
        "description": ("Open a website for the user in the configured browser. "
                        "Use this for every 'open/show me <site>' request instead of a shell command."),
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Full http(s) URL"}},
            "required": ["url"],
        },
    },
    {
        "name": "calendar_events",
        "description": ("Read the user's calendar (iCloud/CalDAV). Returns events from `start` "
                        "(YYYY-MM-DD, default today) for `days` days (default 1, max 62)."),
        "inputSchema": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "days": {"type": "integer"}},
        },
    },
    {
        "name": "calendar_add",
        "description": ("Create an event in the user's calendar. Needs their confirmation. "
                        "start/end as 'YYYY-MM-DD HH:MM' local time, or 'YYYY-MM-DD' for all-day. "
                        "end defaults to one hour after start."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
                "location": {"type": "string"}, "notes": {"type": "string"},
                "calendar": {"type": "string", "description": "calendar name, optional"},
            },
            "required": ["title", "start"],
        },
    },
]


TOOLS += [
    {
        "name": "network_devices",
        "description": ("List the devices currently seen on the user's home network (IP, MAC, hostname). "
                        "Read-only, no confirmation needed."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pi_status",
        "description": ("Health of the user's Raspberry Pi / home server: uptime, CPU temperature, "
                        "load, memory, disk, service states. Read-only, no confirmation needed. "
                        "For anything else on the Pi use pi_run."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pi_run",
        "description": ("Run one shell command on the user's Raspberry Pi / home server over ssh (e.g. "
                        "'docker ps', 'sudo systemctl restart pihole-FTL', 'tail -n 50 /var/log/syslog'). Shows a "
                        "confirmation card with the command; explain in German first what you are about to do and why, "
                        "`begruendung` = that explanation. Prefer harmless, reversible commands; output is truncated."),
        "inputSchema": {"type": "object",
                        "properties": {"command": {"type": "string"}, "begruendung": {"type": "string"}},
                        "required": ["command", "begruendung"]},
    },
]

TOOLS += [
    {
        "name": "home_states",
        "description": ("Read the user's smart home (Home Assistant): devices with name, entity_id and state. "
                        "Optional `search` filters by name or entity_id (e.g. 'wohnzimmer', 'light.'); with few hits each "
                        "switchable device also says since when it has its state ('seit 16:52 (51 Minuten)'). "
                        "Read-only, no confirmation needed."),
        "inputSchema": {"type": "object", "properties": {"search": {"type": "string"}}},
    },
    {
        "name": "home_control",
        "description": ("Control a Home Assistant device. `entity_id` must come from home_states. "
                        "Lights, switches, fans, media players and scenes support their usual services. "
                        "A direct user request to turn a socket (`switch.*`) on or off is already authorization: "
                        "execute switch.turn_on/off immediately; do not ask or call approve. If the target is unclear, "
                        "ask which socket. Only room-level climate entities listed in config/home.json → climate_groups can be controlled; "
                        "do not target their individual radiator entities. "
                        "climate.set_temperature with {\"temperature\": 20}, or climate.set_hvac_mode "
                        "with a supported heat/off/auto mode. No other climate entity or service is allowed."),
        "inputSchema": {
            "type": "object",
            "properties": {"entity_id": {"type": "string"}, "service": {"type": "string"},
                           "data": {"type": "object"}},
            "required": ["entity_id", "service"],
        },
    },
]

TOOLS += [
    {
        "name": "mail_inbox",
        "description": ("Read the user's mail inbox via IMAP (read-only): newest mails with sender, subject, date "
                        "and a short preview. Default period the last 24 hours; `days` looks further back (max 90) "
                        "whenever the user means older mail (\"seit gestern\", \"die letzten Tage\", \"while I was "
                        "away\", \"the 240 mails\"). `unread_only` (default true, false when `search` is given — set "
                        "false to see mails that are already read), `limit` (default 5; up to 15 with preview, "
                        "16–300 = compact list date · sender · subject to look a pile through), optional `search` "
                        "in subject/sender. `last_marked`=true lists the mails mail_mark_read marked as read last "
                        "time. For one sender/topic over the whole year use mail_search. Never sends, deletes or "
                        "marks anything."),
        "inputSchema": {
            "type": "object",
            "properties": {"unread_only": {"type": "boolean"}, "limit": {"type": "integer"},
                           "search": {"type": "string"}, "days": {"type": "number"},
                           "last_marked": {"type": "boolean"}},
        },
    },
    {
        "name": "mail_search",
        "description": ("Search ALL mail of the last months (read-only, headers only): did a sender ever write, when "
                        "was the last mail from X, mails about a topic. `query` = sender name/address or subject word "
                        "(e.g. 'stadtwerke'), `days` how far back (default and max 365), `in_text`=true also searches the "
                        "body, `limit` (default 10, max 25). Looks in the inbox and the user's own folders (Archiv …), "
                        "not Sent/Trash/Spam. Returns count, date range and the newest hits (date, sender, subject)."),
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "days": {"type": "integer"},
                           "in_text": {"type": "boolean"}, "limit": {"type": "integer"}},
            "required": ["query"],
        },
    },
    {
        "name": "mail_mark_read",
        "description": ("Mark unread inbox mails as read (only the read flag, nothing is moved or deleted). "
                        "`search`: substring of sender or subject (as mail_inbox showed it), or `all`=true for every "
                        "unread mail. With `all` and more than 15 mails nothing is marked at first: the tool returns "
                        "the list, you tell the user which ones look important, and only after his go call again "
                        "with `reviewed`=true. `unread`=true does the opposite: marks matching read mails as unread "
                        "again; `unread`=true + `last_batch`=true restores exactly the mails marked last time. "
                        "Harmless and reversible."),
        "inputSchema": {
            "type": "object",
            "properties": {"search": {"type": "string"}, "all": {"type": "boolean"}, "unread": {"type": "boolean"},
                           "reviewed": {"type": "boolean"}, "last_batch": {"type": "boolean"}},
        },
    },
]

TOOLS += [
    {
        "name": "weather_alerts",
        "description": "Official warnings of the Deutscher Wetterdienst for here (storm, thunderstorm, ice, heat) in force now.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "weather",
        "description": ("Weather forecast (Open-Meteo) for the user's home or any `place` (city name). Per day: sky, "
                        "min/max temperature, rain probability and amount, wind, UV index, sunshine hours, sunrise "
                        "and sunset; plus the current conditions. `days` (default 2 = today and tomorrow, max 14). "
                        "`hourly`=true adds the next 24 hours in 2-hour steps (temperature, rain chance, sky). "
                        "Read-only, no confirmation needed."),
        "inputSchema": {
            "type": "object",
            "properties": {"days": {"type": "integer"}, "place": {"type": "string"}, "hourly": {"type": "boolean"}},
        },
    },
]

TOOLS += [
    {
        "name": "music",
        "description": ("Control the user's Spotify: play/pause/next/previous reach whatever device plays "
                        "(phone on the speaker, PC) when the Spotify login is set up, else the PC's media keys. "
                        "`action`: play, pause, play_pause, next, previous, volume_up, volume_down, mute, search "
                        "(opens Spotify with `query` searched — he picks/plays from there), or transfer "
                        "(`room` = Wohnzimmer, Schlafzimmer, ... or a Spotify device name such as PC: the "
                        "music moves to that room's speaker and plays on there — 'Musik vom Schlafzimmer ins "
                        "Wohnzimmer', 'mach die Musik im Wohnzimmer an'; without `room`: the room the presence "
                        "sensors see the user in, 'hol die Musik zu mir'), or liked (the user's liked songs, "
                        "'Lieblingssongs', 'meine Playlist', as a whole list; optional `room`), or artist (all songs of the artist "
                        "in `query`, 'spiel alle Songs von Linkin Park' — plays right away, never tell him to do it "
                        "himself; optional `room`). Starts Spotify first if it is not "
                        "running. Music in a room always goes through this tool, never as an Alexa command to an "
                        "Echo (home_control play_media custom refuses that): it plays on the room's soundbar and "
                        "only falls back to the Echo when the soundbar stays unreachable. "
                        "Harmless, no confirmation needed."),
        "inputSchema": {
            "type": "object",
            "properties": {"action": {"type": "string"}, "query": {"type": "string"}, "room": {"type": "string"}},
            "required": ["action"],
        },
    },
]

TOOLS += [
    {
        "name": "nas_status",
        "description": ("Health of the user's Synology NAS: model, uptime, temperature, CPU/RAM, "
                        "volumes with used space and status, every disk with health and temperature. "
                        "Read-only, no confirmation needed."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "garmin_health",
        "description": ("The user's Garmin data: per day sleep (duration, phases, score), steps, "
                        "resting heart rate, stress and body battery. `days` (default 1 = today, max 14) "
                        "counts back from today; sleep of a day is the night that ended that morning. "
                        "Read-only, no confirmation needed."),
        "inputSchema": {"type": "object", "properties": {"days": {"type": "integer"}}},
    },
]

TOOLS += [
    {
        "name": "web_fetch",
        "description": ("Fetch a web page or JSON API and return its readable text, shortened. "
                        "Prefer the proven sources in the memory note 'Websites'."),
        "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    },
    {
        "name": "memory_search",
        "description": ("Search the user's notes and all past conversations, also months old (full-text index, "
                        "titles and snippets, conversations with their day)."),
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "memory_read",
        "description": "Read one note in full, by title (e.g. 'Golf', 'Smart Home', 'Gespräch 2026-10-13').",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
    },
    {
        "name": "memory_save",
        "description": ("Remember something lasting about the user. `note`: Profil, Befehle, Vorlieben, Websites, "
                        "Termine, Wissen, Personen/<Name> or Projekte/<Name>. `text`: one short bullet. "
                        "`replace` (optional): an existing bullet text to replace instead of appending (to correct). "
                        "`quelle`: 'gesagt' (the user said it), 'abgeleitet' (you concluded it), 'vermutet' (a guess). "
                        "`bis` (optional, YYYY-MM-DD): the fact expires then. A save that contradicts an existing "
                        "entry is refused — ask the user what is right, then save with `replace`."),
        "inputSchema": {"type": "object", "properties": {
            "note": {"type": "string"}, "text": {"type": "string"}, "replace": {"type": "string"},
            "quelle": {"type": "string", "enum": ["gesagt", "abgeleitet", "vermutet"]}, "bis": {"type": "string"}},
            "required": ["note", "text"]},
    },
]

TOOLS += [
    {
        "name": "reminder_add",
        "description": ("Set a timer, alarm clock or reminder; Jarvis rings and speaks at that moment. "
                        "`kind`: reminder (default), alarm or timer. `at`: 'YYYY-MM-DD HH:MM' local time. "
                        "`text`: what to remind of (reminder) or a label (timer). No confirmation needed."),
        "inputSchema": {"type": "object", "properties": {
            "kind": {"type": "string"}, "at": {"type": "string"}, "text": {"type": "string"}},
            "required": ["at"]},
    },
    {
        "name": "reminder_list",
        "description": "List the open timers, alarms and reminders with their ids.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "reminder_cancel",
        "description": "Delete a timer/alarm/reminder by `id` from reminder_list, or `id`='all' for everything.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    },
    {
        "name": "list_read",
        "description": "Read the shopping list (`list`='einkauf') or the to-do list (`list`='todo').",
        "inputSchema": {"type": "object", "properties": {"list": {"type": "string"}}, "required": ["list"]},
    },
    {
        "name": "list_add",
        "description": "Add entries to the shopping list ('einkauf') or the to-do list ('todo'). `items`: short entries.",
        "inputSchema": {"type": "object", "properties": {
            "list": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}},
            "required": ["list", "items"]},
    },
    {
        "name": "list_remove",
        "description": "Tick off one entry (`item`, as list_read shows it) on the shopping or to-do list.",
        "inputSchema": {"type": "object", "properties": {"list": {"type": "string"}, "item": {"type": "string"}},
                        "required": ["list", "item"]},
    },
    {
        "name": "routine_list",
        "description": "Show the user's routines (trigger phrases and steps) as JSON.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "routine_save",
        "description": (
            "Create or replace a routine: when the user says one of `triggers`, the steps run without the LLM. "
            "Steps, in order, each an object with exactly one of: "
            "{\"say\": text Jarvis speaks, or a list of texts — one of them, never the same twice in a row}, "
            "{\"do\": a short German command the local router understands, answer not spoken — e.g. "
            "'Licht Wohnzimmer aus', 'Steckdose Ventilator an', 'Wecker morgen um 7', 'Timer 10 Minuten'}, "
            "{\"tell\": a German question whose answer is spoken — e.g. 'Wann klingelt mein Wecker?', "
            "'Wie wird das Wetter morgen?', 'Was steht auf der Einkaufsliste?'}, "
            "{\"ha\": 'domain.service', \"entity_id\": ..., \"data\": {...}} for light/switch/fan/media_player/scene "
            "(entity_id 'all' only for light and media_player; look up ids with home_states), "
            "{\"wait\": seconds}. Triggers: short phrases in lower case, e.g. 'gute nacht'."),
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string"},
            "triggers": {"type": "array", "items": {"type": "string"}},
            "steps": {"type": "array", "items": {"type": "object"}}},
            "required": ["name", "triggers", "steps"]},
    },
    {
        "name": "routine_delete",
        "description": "Delete a routine by `name`.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
]

TOOLS += [
    {
        "name": "memory_overview",
        "description": ("What Jarvis has stored about the user: without `note` a list of notes with counts, with "
                        "`note` its entries with source (gesagt/abgeleitet) and date."),
        "inputSchema": {"type": "object", "properties": {"note": {"type": "string"}}},
    },
    {
        "name": "memory_why",
        "description": "Why a fact is stored: its note, source, date and the matching line of that day's conversation. `text`: words from the fact.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "memory_forget",
        "description": ("Forget one stored fact (moves it to the trash). `text`: words from the fact, `note` optional. "
                        "Shows a confirmation card; explain in German first, `begruendung` = that explanation."),
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "note": {"type": "string"},
                                                         "begruendung": {"type": "string"}},
                        "required": ["text", "begruendung"]},
    },
    {
        "name": "memory_review",
        "description": "Check the stored memory for duplicates and contradictions inside each note.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "memory_export",
        "description": "Export the whole memory vault as a zip file in the backups folder; returns the path.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "audit_today",
        "description": "What Jarvis did today (actions, confirmation cards). `day` optional as YYYY-MM-DD.",
        "inputSchema": {"type": "object", "properties": {"day": {"type": "string"}}},
    },
]

TOOLS += [
    {
        "name": "screen_describe",
        "description": ("Describe the screen with an image model (layout, windows, where a button is) — only when the "
                        "user asks and when screen_read (local text recognition) is not enough. Sends ONE screenshot "
                        "to Claude after a confirmation card; explain first, `begruendung` = that explanation. "
                        "`frage`: what to look for; `ganz`: whole screen instead of the active window."),
        "inputSchema": {"type": "object", "properties": {"frage": {"type": "string"}, "ganz": {"type": "boolean"},
                                                         "begruendung": {"type": "string"}},
                        "required": ["begruendung"]},
    },
]

TOOLS += [
    {
        "name": "doc_search",
        "description": ("Full-text search in the user's local documents (shared folders, manuals): returns passages "
                        "with file and position. Cite them as [n] with the file name. `query`: words to find."),
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "doc_status",
        "description": "How many documents are indexed, which folders, whether PDFs are read.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

TOOLS += [
    {
        "name": "forecast_status",
        "description": ("Storage forecasts for NAS, PC system disk and Pi SD card from daily values: percent used, "
                        "growth per day, days until full, with data basis and confidence."),
        "inputSchema": {"type": "object", "properties": {}},
    },
]

TOOLS += [
    {
        "name": "task_run",
        "description": ("Start a step-by-step check with a report (read-only, runs in the background, result is "
                        "spoken when done): `recipe` nas, pi, netz, pc, haus or system. Use for 'prüf …' requests."),
        "inputSchema": {"type": "object", "properties": {"recipe": {"type": "string",
                                                                    "enum": ["nas", "pi", "netz", "pc", "haus", "system"]}},
                        "required": ["recipe"]},
    },
    {
        "name": "task_status",
        "description": "Status and report of checks (the last five, or one by `id`).",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}},
    },
    {
        "name": "task_cancel",
        "description": "Cancel running checks (all, or one by `id`).",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}},
    },
]

TOOLS += [
    {
        "name": "context_snapshot",
        "description": ("Current context with age and trust per source: zeit, kalender, wetter, timer, plaene, "
                        "warnungen, pc, haus. `sources` optional list; stale or missing sources are marked."),
        "inputSchema": {"type": "object", "properties": {"sources": {"type": "array", "items": {"type": "string"}}}},
    },
]

TOOLS += [
    {
        "name": "skill_list",
        "description": "What the router has learned: active rules (with version) and drafts waiting for approval.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "skill_undo",
        "description": ("Undo a learned router rule: back to its previous version, or remove it. `text`: words of the "
                        "wording. Shows a confirmation card; explain first, `begruendung` = that explanation."),
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "begruendung": {"type": "string"}},
                        "required": ["text", "begruendung"]},
    },
]

TOOLS += [
    {
        "name": "resource_status",
        "description": ("Graphics memory and RAM budget of this PC: VRAM used/free, whether the GPU voice, Whisper "
                        "and the local Ollama model sit on the GPU, and what Jarvis gave back recently."),
        "inputSchema": {"type": "object", "properties": {}},
    },
]

TOOLS += [
    {
        "name": "mail_draft",
        "description": ("Create an email DRAFT in the user's mailbox (Drafts folder) — never sends. `to`, `subject`, "
                        "`body` (German unless asked otherwise). Shows a confirmation card; explain first, "
                        "`begruendung` = that explanation. `account`: which mailbox when there are several "
                        "(name or address; default the first)."),
        "inputSchema": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"},
                                                         "body": {"type": "string"}, "account": {"type": "string"},
                                                         "begruendung": {"type": "string"}},
                        "required": ["to", "subject", "body", "begruendung"]},
    },
    {
        "name": "day_prep",
        "description": ("Prepare a day: appointments with travel time and leave time, matching notes. `day` as "
                        "YYYY-MM-DD (default tomorrow)."),
        "inputSchema": {"type": "object", "properties": {"day": {"type": "string"}}},
    },
    {
        "name": "research_save",
        "description": ("Save a researched answer as a note: `title`, `summary` (with dates; mark assumptions as such), "
                        "`sources` (list of 'Title – URL (date)')."),
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "summary": {"type": "string"},
                                                         "sources": {"type": "array", "items": {"type": "string"}}},
                        "required": ["title", "summary"]},
    },
    {
        "name": "watch_add",
        "description": "Watch a topic and tell the user when there is news. `topic`, `every_days` (default 7).",
        "inputSchema": {"type": "object", "properties": {"topic": {"type": "string"}, "every_days": {"type": "integer"}},
                        "required": ["topic"]},
    },
    {
        "name": "watch_list",
        "description": "List the watched topics.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "watch_remove",
        "description": "Stop watching a topic (`topic`: words from it).",
        "inputSchema": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]},
    },
    {
        "name": "plan_create",
        "description": ("Plan something over several days: `title`, `steps` = [{text, date 'YYYY-MM-DD'}]. Creates a "
                        "note Projekte/<title> with a checklist and a reminder per step."),
        "inputSchema": {"type": "object", "properties": {
            "title": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "object", "properties": {
                "text": {"type": "string"}, "date": {"type": "string"}}, "required": ["text", "date"]}}},
            "required": ["title", "steps"]},
    },
    {
        "name": "plan_list",
        "description": "Open steps of all plans.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "plan_check",
        "description": "Tick off a step of a plan: `title` of the plan, `step` = words from the step.",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "step": {"type": "string"}},
                        "required": ["title", "step"]},
    },
]

TOOLS += [
    {
        "name": "mail_events_scan",
        "description": ("Find bookings in the mails of the last 24 hours: calendar attachments of "
                        "confirmations (PC CADDIE tee times, golftimer, Golf House ...). Only future ones; "
                        "cancelled ones and those already in the calendar (same day, similar title) left out, "
                        "an overlap with another entry named. Lists an id per event."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "shipments_status",
        "description": ("Orders on their way, from the shipping mails of every account: shop, carrier, expected "
                        "day (from the mail or DHL), status (unterwegs, heute, abholbereit, zugestellt)."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mail_events_undo",
        "description": ("Take what Jarvis entered automatically from mail (bookings of trusted senders, "
                        "expected deliveries) out of the calendar again — the user said 'rückgängig'. "
                        "`hours`: how far back, default 24."),
        "inputSchema": {"type": "object", "properties": {"hours": {"type": "number"}}},
    },
    {
        "name": "mail_events_add",
        "description": ("Enter events from mail_events_scan in the calendar: `ids` = list of ids, or \"alle\". "
                        "One confirmation card lists them all; explain in German first, `begruendung`."),
        "inputSchema": {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}},
                                                         "begruendung": {"type": "string"}},
                        "required": ["ids", "begruendung"]},
    },
]

TOOLS += [
    {
        "name": "outage_impact",
        "description": ("What fails if a device or service fails, from Blast-Radius (dependency map of Pi, network "
                        "and smart home, read only). `question` in plain German, e.g. 'Was passiert, wenn die "
                        "Fritzbox ausfällt?'. Returns the functions that stop working or are degraded."),
        "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
    },
    {
        "name": "pi_reports",
        "description": ("Status of the homelab tools on the Pi, read only: `topic` backups (Restore Probe: were last "
                        "night's backups really restorable), updates (Update-Schleuse: container updates waiting, "
                        "held back for breaking changes, failed), strom (Stromfresser-Detektiv: open findings on "
                        "the power plugs, e.g. standby too high, plug sends no values), zigbee (Zigbee-Platzberater: "
                        "where a new Zigbee router would help, channel overlap with WLAN), akku (Akku-Doktor: health "
                        "of the home battery: usable kWh, SOH, cycles, warranty), automationen (Automations-Prüfer: "
                        "broken, looping or conflicting Home Assistant automations), drift (Homelab-Drift: unexpected "
                        "changes on Pi and FRITZ!Box, scan health), or alles."),
        "inputSchema": {"type": "object", "properties": {"topic": {"type": "string",
                                                                   "enum": ["backups", "updates", "strom", "zigbee",
                                                                            "akku", "automationen", "drift",
                                                                            "alles"]}}},
    },
    {
        "name": "homelab_status",
        "description": ("Live figures from Prometheus (what Grafana shows): `topic` internet (throughput now, "
                        "today's volume and peak, line rate, FRITZ!Box uptime), wlan (devices per band), pi "
                        "(temperature, CPU, RAM, disk), pc (CPU, RAM, free disk), or alles."),
        "inputSchema": {"type": "object", "properties": {"topic": {"type": "string",
                                                                   "enum": ["internet", "wlan", "pi", "pc", "alles"]}}},
    },
    {
        "name": "metrics_query",
        "description": ("Free PromQL against Prometheus, read only, for questions over time ('wann war die Last "
                        "heute am höchsten'). `expr` PromQL; `hours` > 0 = the last hours as ~8 points per series. "
                        "Metrics: node_* (Pi), windows_* (this PC), fritz_* (FRITZ!Box: fritz_wan_datarate_bytes "
                        "{direction=rx|tx} in bytes/s — not the wrapping byte counter), homeassistant_*."),
        "inputSchema": {"type": "object", "properties": {"expr": {"type": "string"}, "hours": {"type": "number"}},
                        "required": ["expr"]},
    },
    {
        "name": "pihole_status",
        "description": "Pi-hole: queries and share blocked (24 h), filter on/off, top blocked domains, busiest devices.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "security_findings",
        "description": ("Open findings of the security centre on the Pi with their ids, why they matter, what to do "
                        "and how long it takes; plus the accepted exceptions."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "security_accept",
        "description": ("Accept one open finding as a reasoned exception (always a confirmation card): `id` from "
                        "security_findings, `grund` = one German sentence why it is acceptable, `tage` 30, 90 or 365. "
                        "Explain in German first, `begruendung`. Prefer 30 days for findings that cover a whole device."),
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "grund": {"type": "string"},
                                                         "tage": {"type": "integer", "enum": [30, 90, 365]},
                                                         "begruendung": {"type": "string"}},
                        "required": ["id", "grund", "tage", "begruendung"]},
    },
    {
        "name": "security_scan",
        "description": "Start a quick scan of the security centre (about 15 s, read-only on the Pi).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "homelab_report",
        "description": ("Lagebericht from the Pi: Uptime Kuma (which services are down, since when), the ORBIT "
                        "security centre (score, open findings by severity) and the zero-feed-in energy loop "
                        "(automations and data in Home Assistant). `topic` alles | kuma | sicherheit | energie; "
                        "`detail` true lists more findings."),
        "inputSchema": {"type": "object", "properties": {
            "topic": {"type": "string", "enum": ["alles", "kuma", "sicherheit", "energie"]},
            "detail": {"type": "boolean"}}},
    },
]

TOOLS += [
    {
        "name": "updates_check",
        "description": ("Check every device Jarvis reaches for updates: Home Assistant update entities (FRITZ!Box, "
                        "add-ons, firmware), the Synology NAS (DSM, packages), the Raspberry Pi (apt, Pi-hole, "
                        "Docker images incl. Home Assistant itself), FRITZ! repeaters, and smart-home devices whose "
                        "firmware is older than the same model elsewhere in the house. Lists one id per update; "
                        "rows marked 'nur Anzeige' cannot be installed by Jarvis."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "update_install",
        "description": ("Install ONE update by `target` = an id from updates_check (e.g. ha:update.fritz_box, "
                        "nas:pkg:CMS, pi:apt, pi:pihole). Shows a confirmation card; explain in German first what "
                        "restarts, `begruendung` = that explanation. One call per device."),
        "inputSchema": {"type": "object", "properties": {"target": {"type": "string"}, "begruendung": {"type": "string"}},
                        "required": ["target", "begruendung"]},
    },
    {
        "name": "update_status",
        "description": "How a running update on the Raspberry Pi is going (last log lines, reboot wanted).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

TOOLS += [
    {
        "name": "pc_status",
        "description": "This PC: CPU, RAM, GPU and VRAM (with the programs using it), free disk space, uptime, biggest programs.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "net_diagnose",
        "description": "Diagnose the internet connection step by step (router, internet, DNS, web) with a verdict. Takes ~15 s.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "backup_status",
        "description": "Status of the Hyper Backup tasks on the Synology NAS (last result, last and next run).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "home_check",
        "description": "Home Assistant health: unavailable devices, low batteries, temperature/humidity sensors silent for a day.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "energy_coach",
        "description": ("Energy coach: storage charge, solar now, house load, when today/tomorrow solar is left "
                        "over once the battery is full, and which shiftable appliances fit (rarely used plugs are "
                        "never suggested). Read only — it never switches anything."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "announce",
        "description": ("Speak an announcement in a room (\"Sag im Wohnzimmer: Essen ist fertig\"): `raum` = room "
                        "name from config/home.json speakers, `text` = what to say, in German. Without `raum` "
                        "(or `raum` = hier) the room the presence sensors see the user in."),
        "inputSchema": {"type": "object", "properties": {"raum": {"type": "string"}, "text": {"type": "string"}},
                        "required": ["text"]},
    },
    {
        "name": "tv_control",
        "description": ("Fire TV through Alexa: `befehl` an, aus, pause, weiter, lauter, leiser, stumm, laut, zurueck, "
                        "home, or app with `app` = the app's name (e.g. Netflix, YouTube, ARD Mediathek)."),
        "inputSchema": {"type": "object", "properties": {
            "befehl": {"type": "string", "enum": ["an", "aus", "pause", "weiter", "lauter", "leiser", "stumm", "laut",
                                                  "zurueck", "home", "app"]},
            "app": {"type": "string"}}, "required": ["befehl"]},
    },
    {
        "name": "power_costs",
        "description": ("Electricity costs: the house and the biggest plugs this month with the projection for the "
                        "whole month; `geraet` = one plug (e.g. 'PC'); `standby` true = which plugs draw power while "
                        "idle and what that costs a year. Price from JARVIS_POWER_PRICE."),
        "inputSchema": {"type": "object", "properties": {"geraet": {"type": "string"}, "standby": {"type": "boolean"}}},
    },
    {
        "name": "energy_device_usage",
        "description": ("Remember how often the user uses a plug, for the energy coach: `geraet` = plug name "
                        "(e.g. 'Boiler'), `oft` = true (suggest it again) or false (rarely used, never suggest)."),
        "inputSchema": {"type": "object", "properties": {"geraet": {"type": "string"}, "oft": {"type": "boolean"}},
                        "required": ["geraet", "oft"]},
    },
    {
        "name": "phone_notify",
        "description": "Send a short push message to the user's phone (Home Assistant app). `text`: the message.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
]

TOOLS += [
    {
        "name": "window_arrange",
        "description": ("Move an app's window: `app` (e.g. spotify, chrome, code, discord), `position`: links, "
                        "rechts, oben links, oben rechts, unten links, unten rechts, mitte, voll; `monitor` 1, 2, ..."),
        "inputSchema": {"type": "object", "properties": {"app": {"type": "string"}, "position": {"type": "string"},
                                                         "monitor": {"type": "integer"}}, "required": ["app", "position"]},
    },
    {
        "name": "file_search",
        "description": "Find files by name in the user's shared folders (Desktop, Documents, Downloads). Returns paths, size, date.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "file_read",
        "description": "Read a text, Markdown, CSV, log, code or Word file from the shared folders (path from file_search).",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "clipboard_read",
        "description": "Read the text the user just copied (only when they ask about it).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "screen_read",
        "description": ("Read the text on the user's screen via local OCR (only when they ask): the active window, "
                        "or the whole screen with `ganz`=true. Refused while a password, banking or chat window is visible."),
        "inputSchema": {"type": "object", "properties": {"ganz": {"type": "boolean"}}},
    },
    {
        "name": "script_list",
        "description": "List the scripts the user has allowed Jarvis to run.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "script_run",
        "description": ("Run one allowed script by `name` (from script_list). Shows a confirmation card; explain in "
                        "German first, `begruendung` = that explanation."),
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "begruendung": {"type": "string"}},
                        "required": ["name", "begruendung"]},
    },
]

TOOLS += [
    {
        "name": "health_log",
        "description": ("Note what Garmin cannot know, for the weekly review: a meal, alcohol, coffee, training. "
                        "`what`: short German text ('Abendessen', 'zwei Bier'); `at`: 'HH:MM', 'gestern HH:MM' or "
                        "'YYYY-MM-DD HH:MM' (empty = now). No confirmation needed."),
        "inputSchema": {"type": "object", "properties": {"what": {"type": "string"}, "at": {"type": "string"}},
                        "required": ["what"]},
    },
    {
        "name": "proposal_list",
        "description": "List Jarvis' open proposals (learned rules, suggested routines/reminders/ideas) with ids.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "proposal_accept",
        "description": ("Carry out an open proposal after the user said yes. Shows a confirmation card. Before "
                        "calling, explain in German what will happen and why. `begruendung`: that explanation "
                        "in one or two German sentences (shown on the card)."),
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "begruendung": {"type": "string"}},
                        "required": ["id", "begruendung"]},
    },
    {
        "name": "proposal_decline",
        "description": "Discard an open proposal by `id` when the user does not want it.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    },
    {
        "name": "rule_remove",
        "description": ("Remove a learned rule from the note Vorlieben (section 'Gelernte Regeln'). `text`: words "
                        "from the rule. Shows a confirmation card; explain in German first. `begruendung` as above."),
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "begruendung": {"type": "string"}},
                        "required": ["text", "begruendung"]},
    },
]


TOOLS += [
    {
        "name": "memory_timeline",
        "description": ("What happened on which day, from every dated source joined by date: the conversation log "
                        "(what the user asked and what Jarvis answered), Garmin (sleep, steps, stress, Body Battery, "
                        "logged meals), Jarvis' own notices, homelab incidents, storage levels. `start`/`end` as "
                        "YYYY-MM-DD (default: the last `days`, 7), `query`: words that the conversation must mention "
                        "('Drucker', 'krank'). Use for 'letzte Woche', 'neulich', 'als ich … war'. Read-only."),
        "inputSchema": {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"},
                                                         "query": {"type": "string"}, "days": {"type": "integer"}}},
    },
    {
        "name": "maintenance_status",
        "description": ("Maintenance calendar of the homelab: TLS certificates about to expire, Docker images on the "
                        "Pi older than their limit, the age of the last NAS backup, disks not normal. Read-only."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "paperless_search",
        "description": ("Full-text search in the user's document archive (Paperless-ngx: scanned letters, contracts, "
                        "insurance, invoices). `query`: words. Returns documents with number, title, correspondent, "
                        "date, tags and the matching passage. Read-only."),
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "paperless_read",
        "description": "The text of one archived document by its number (`id` from paperless_search), shortened. Read-only.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    },
    {
        "name": "unifi_status",
        "description": ("UniFi network controller, read-only: `topic` clients (who is connected, per WLAN, weak "
                        "signals), geraete (access points, switches, gateway: online or not) or alles."),
        "inputSchema": {"type": "object", "properties": {"topic": {"type": "string", "enum": ["clients", "geraete", "alles"]}}},
    },
    {
        "name": "media_recent",
        "description": ("Jellyfin media library: what plays right now, what the user started and did not finish "
                        "(with number and percentage), what came in lately. Read-only."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "media_search",
        "description": "Find films, series and episodes in the Jellyfin library by `query`. Read-only.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "media_play",
        "description": ("Play something from Jellyfin on a device running the Jellyfin app: `item` = number from "
                        "media_recent/media_search or a title (empty = continue the last thing), `device` = part of "
                        "the device name (default: the TV). No confirmation needed."),
        "inputSchema": {"type": "object", "properties": {"item": {"type": "string"}, "device": {"type": "string"}}},
    },
]


# Which tools each group brings. The router in the server picks the groups per
# request and passes them in JARVIS_TOOL_GROUPS, so a question about the weather
# does not carry the definitions of mail, NAS and calendar tools along.
GROUPS = {
    "memory": {"memory_search", "memory_read", "memory_save"},
    "web": {"web_fetch", "open_website"},
    "calendar": {"calendar_events", "calendar_add", "mail_events_scan", "mail_events_add", "mail_events_undo"},
    "home": {"home_states", "home_control", "home_check", "energy_coach", "energy_device_usage", "power_costs", "announce", "tv_control"},
    "weather": {"weather", "weather_alerts"},
    "music": {"music"},
    "systems": {"pi_status", "pi_run", "nas_status", "network_devices", "pc_status", "net_diagnose", "backup_status",
                "resource_status", "forecast_status", "maintenance_status"},
    "health": {"garmin_health", "health_log", "memory_timeline"},
    "episodes": {"memory_timeline"},
    "paperless": {"paperless_search", "paperless_read"},
    "unifi": {"unifi_status"},
    "media": {"media_recent", "media_search", "media_play"},
    "timers": {"reminder_add", "reminder_list", "reminder_cancel"},
    "lists": {"list_read", "list_add", "list_remove"},
    "routines": {"routine_list", "routine_save", "routine_delete", "home_states"},
    "proposals": {"proposal_list", "proposal_accept", "proposal_decline", "rule_remove", "skill_list", "skill_undo"},
    "memory_admin": {"memory_overview", "memory_why", "memory_forget", "memory_export", "memory_save", "memory_search",
                     "memory_review", "memory_timeline"},
    "audit": {"audit_today"},
    "phone": {"phone_notify"},
    "mail": {"mail_inbox", "mail_search", "mail_mark_read", "mail_draft", "mail_events_scan", "mail_events_add",
             "shipments_status", "mail_events_undo"},
    "dayprep": {"day_prep", "calendar_events", "context_snapshot"},
    "tasks": {"task_run", "task_status", "task_cancel"},
    "research": {"web_fetch", "research_save", "watch_add", "watch_list", "watch_remove"},
    "plans": {"plan_create", "plan_list", "plan_check", "reminder_list"},
    "windows": {"window_arrange"},
    "files": {"file_search", "file_read", "doc_search", "doc_status"},
    "clipboard": {"clipboard_read"},
    "screen": {"screen_read", "screen_describe"},
    "scripts": {"script_list", "script_run"},
    "updates": {"updates_check", "update_install", "update_status"},
    "homelab": {"homelab_status", "outage_impact", "pi_reports", "metrics_query", "pihole_status", "homelab_report", "security_findings",
                "security_accept", "security_scan", "maintenance_status"},
}
ACTIVE_GROUPS = [g.strip() for g in os.environ.get("JARVIS_TOOL_GROUPS", "").split(",") if g.strip()]
# Hard cap per tool result — results are sent again with every later API call of the turn.
MAX_RESULT = int(os.environ.get("JARVIS_TOOL_RESULT_MAX", "2500"))


def listed_tools():
    if not ACTIVE_GROUPS:          # legacy mode: everything
        return TOOLS
    names = {"approve"}
    for g in ACTIVE_GROUPS:
        names |= GROUPS.get(g, set())
    return [t for t in TOOLS if t["name"] in names]


def _web_fetch(url):
    import html as _html
    import re as _re
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return "Nur http(s)-Adressen."
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 JARVIS", "Accept-Language": "de,en"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(600_000)
            ctype = r.headers.get("content-type", "")
            charset = r.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code} bei {url}"
    except Exception as e:  # noqa: BLE001
        return f"Nicht erreichbar: {e}"
    text = raw.decode(charset, "replace")
    if "json" in ctype or text.lstrip()[:1] in "{[":
        try:
            return json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))
        except ValueError:
            pass
    text = _re.sub(r"(?is)<(script|style|noscript|svg|head|nav|footer|form)[^>]*>.*?</\1>", " ", text)
    text = _re.sub(r"(?s)<[^>]+>", " ", text)
    return " ".join(_html.unescape(text).split())


def _memory(name, args):
    import re as _re
    import memory
    if name == "memory_search":
        # The FTS5 index first: it also holds conversations older than the diary keeps (memory_index.py).
        try:
            import memory_index
            found = memory_index.describe(args.get("query") or "")
            if found:
                return found
        except Exception:  # noqa: BLE001 — a broken index must not cost the memory: plain scan below
            pass
        # Full-text, word-wise: return the matching lines, not whole notes.
        words = [w for w in _re.split(r"[\W_]+", (args.get("query") or "").lower()) if len(w) > 2]
        if not words:
            return "Suchbegriff fehlt."
        scored = []
        for path in memory._notes():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            low = text.lower()
            pats = [_re.compile(r"\b" + _re.escape(w)) for w in words]   # word starts, not "auto" in "Automatisch"
            whole = [_re.compile(r"\b" + _re.escape(w) + r"\b") for w in words]
            # capped per word: a long note must not win just by repeating a word
            score = (sum(min(len(p.findall(low)), 2) for p in pats)
                     + 3 * sum(min(len(p.findall(low)), 2) for p in whole)
                     + 3 * sum(w in path.stem.lower() for w in words))
            if not score:
                continue
            if "Gespraeche" in path.parts:
                score *= 0.2          # facts before chat logs (they are long and match everything)
            lines = [ln.strip() for ln in text.splitlines()
                     if any(p.search(ln.lower()) for p in pats) and not ln.startswith(("type:", "updated:"))]
            scored.append((score, path.stem, lines[:3]))
        scored.sort(key=lambda x: -x[0])
        out = [f"· {t}: " + " | ".join(ln[:160] for ln in lines) for _, t, lines in scored[:6]]
        return "\n".join(out) or "Nichts gefunden."
    if name == "memory_read":
        title = (args.get("title") or "").strip().lower()
        for path in memory._notes():
            if path.stem.lower() == title:
                _, body = memory._parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
                return body.strip()
        return f"Keine Notiz '{args.get('title')}'. memory_search hilft beim Finden."
    # memory_save
    note = (args.get("note") or "").strip().strip("/\\")
    text = " ".join((args.get("text") or "").split())
    if not note or not text or ".." in note or not _re.fullmatch(r"[\w äöüÄÖÜß.\-/!]+", note):
        return "Ungültige Notiz oder leerer Text."
    import privacy
    if privacy.is_work(note, text):
        return "Nicht gespeichert: Arbeitsthemen merke ich mir nicht."
    import memory_tools as mt
    import memory_intel as mi
    path = memory.note_path(note)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = mi.link_entities(text, note)
    stamp = mt.tag(args.get("quelle"), until=args.get("bis"))
    related = None
    if path.exists() and not (args.get("replace") or "").strip():
        hit = mi.compare(text, mt._bullets(path))
        if hit and hit[0] == "duplicate":
            return f"Steht so schon drin: „{mt.split_tag(hit[1])[0]}“."
        if hit and hit[0] == "conflict":
            old_text, old_src, old_day = mt.split_tag(hit[1])
            return (f"Nicht gespeichert — möglicher Widerspruch ({hit[2]}) zu „{old_text}“"
                    + (f" ({old_src}, {old_day})" if old_src else "") + ". Frag den User, was stimmt; "
                    "dann memory_save mit replace.")
        if hit and hit[0] == "related":
            related = mt.split_tag(hit[1])[0]
    import time as _t
    today = _t.strftime("%Y-%m-%d")
    if path.exists():
        body = path.read_text(encoding="utf-8")
        old = (args.get("replace") or "").strip()
        if old and old in body:
            line = next((ln for ln in body.splitlines() if old in ln), "")
            rest = line[line.index(old) + len(old):] if line else ""
            if mt.TAG.search(rest):                       # correcting: the old tag goes, a fresh one comes
                body = body.replace(old + rest, text + stamp, 1)
            else:
                body = body.replace(old, text + stamp, 1)
        elif text in body:
            return "Steht schon drin."
        else:
            body = body.rstrip() + f"\n- {text}{stamp}\n"
        body = _re.sub(r"(?m)^updated: .*$", f"updated: {today}", body, count=1)
    else:
        body = (f"---\ntype: {'person' if note.lower().startswith('personen/') else 'erinnerung'}\n"
                f"wichtigkeit: mittel\nupdated: {today}\n---\n\n# {note.split('/')[-1]}\n\n"
                f"Gehört zu [[Profil]].\n\n- {text}{stamp}\n")
    path.write_text(body, encoding="utf-8")
    return "Gespeichert." + (f" (ergänzt „{related}“)" if related else "")


# Virtual-key codes of the media keys — they reach whatever player is active.
MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1,
              "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD}


def _spotify_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Spotify.exe", "/NH"], capture_output=True,
                         text=True, **hostos.QUIET)
    return "Spotify.exe" in out.stdout


MUSIC_ROOM_TRIES = int(os.environ.get("JARVIS_MUSIC_ROOM_TRIES", "3") or 3)       # attempts before the fallback speaker
MUSIC_ROOM_PAUSE_S = float(os.environ.get("JARVIS_MUSIC_ROOM_PAUSE_S", "3") or 3)   # seconds between the attempts


def _music_room(room):
    """The music into another room: Spotify Connect moves the playback to that room's speaker.
    No room given: the one the presence sensors see the user in."""
    import speakers
    import spotify
    if not room:
        import whereabouts
        room = whereabouts.room()
    if not room:
        return "In welchen Raum soll die Musik?"
    if not spotify.configured():
        return "Dafür brauche ich das Spotify-Login (docs/installation.md → Konfiguration → Spotify), die Medientasten erreichen nur den PC."
    name, device, player = speakers.spotify_device(room)
    src = _speaker_stream(player)
    if src and src[0] == player:
        return f"Die Musik läuft schon im {name}."
    if src and spotify.playing()[0]:
        src = None                                  # a real Connect session plays as well: that one moves
    extra = {"track": src[1], "position_ms": src[2]} if src else {}
    ok, text = spotify.transfer(device or room, **extra)   # an unknown room may still be a device name ("PC")
    fallback = speakers.spotify_fallback(room) if name else ""
    for _ in range(MUSIC_ROOM_TRIES - 1):
        # the soundbar drops out of Spotify's list now and then and is back a moment later: ask again
        # before settling for the worse speaker (same rule as the HA automation "Musik wandert mit")
        if ok or not fallback or not text.startswith("Spotify kennt kein Gerät"):
            break
        time.sleep(MUSIC_ROOM_PAUSE_S)
        ok, text = spotify.transfer(device, **extra)
    if not ok and fallback and text.startswith("Spotify kennt kein Gerät"):
        ok, text = spotify.transfer(fallback, **extra)
        if ok:
            _pause_speaker(src)
            return (f"„{device}“ ist für Spotify gerade nicht erreichbar, die Musik läuft ersatzweise auf "
                    f"„{text}“ im {name}.")
    if ok:
        _pause_speaker(src)
    via_ha = False
    if not ok and player and not src and text.startswith("Spotify kennt kein Gerät"):
        ok, text, via_ha = _music_room_ha(player), "", True
        if not ok:
            return f"Der Lautsprecher im {name} ist für Spotify gerade nicht erreichbar, und auch über Home Assistant hat es nicht geklappt."
    if not ok:
        return text
    where = name or text
    article = "in der" if where.lower().endswith("e") else "im"    # Küche, Diele / Wohnzimmer, Bad, Flur
    if not name:
        return f"Die Musik läuft jetzt auf „{text}“."
    return f"Die Musik läuft jetzt {article} {where}." + (" Dort fängt die Playlist von vorne an." if via_ha else "")


def _room_device(room):
    """The Spotify device for music in `room` (else the room the user is in): the room's soundbar, asked again a
    few times when Spotify misses it, the Echo only when it stays away (19.11.2026).
    -> (device dict or None, room name or "", room's HA player, stand-in name or "", German error or "")."""
    import speakers
    import spotify
    if not room:
        import whereabouts
        room = whereabouts.room() or ""
    if not room:
        return None, "", "", "", ""
    name, device, player = speakers.spotify_device(room)
    fallback = speakers.spotify_fallback(room)
    devs = spotify.devices()
    dev = spotify.find_device(device or room, devs)
    for _ in range(MUSIC_ROOM_TRIES - 1):
        # the soundbar drops out of Spotify's list now and then: ask again before the Echo (as _music_room)
        if dev or not fallback:
            break
        time.sleep(MUSIC_ROOM_PAUSE_S)
        devs = spotify.devices()
        dev = spotify.find_device(device or room, devs)
    stand_in = ""
    if not dev and fallback:
        dev = spotify.find_device(fallback, devs)
        stand_in = f"„{device}“ ist für Spotify gerade nicht erreichbar, ersatzweise auf „{dev.get('name', fallback)}“. " if dev else ""
    if not dev:
        return None, name or room, player, "", f"Der Lautsprecher im {name or room} ist für Spotify gerade nicht erreichbar."
    return dev, name or dev.get("name", ""), player, stand_in, ""


def _in_room(where):
    return ("in der " if where.lower().endswith("e") else "im ") + where     # Küche, Diele / Wohnzimmer, Bad


def _music_liked(room):
    """The liked songs as a whole list, in `room` (else the room the user is in, else where Spotify plays now).
    Before 16.11.2026 "Spiel meine Lieblingssongs im Wohnzimmer" only moved what played — a Sonos stream
    as one single track, so the music stopped after the first song."""
    import spotify
    if not spotify.configured():
        return "Dafür brauche ich das Spotify-Login (docs/installation.md → Konfiguration → Spotify)."
    dev, where, player, stand_in, err = _room_device(room)
    if err:
        return err
    src = _speaker_stream(player)
    if not spotify.play_liked(dev["id"] if dev else None):
        return "Spotify hat die Lieblingssongs nicht gestartet."
    if src and src[0] != player:
        _pause_speaker(src)                           # a Sonos elsewhere streaming its own track would play on
    if not where:
        return "Ihre Lieblingssongs laufen."
    return stand_in + f"Ihre Lieblingssongs laufen jetzt {_in_room(where)}."


def _music_artist(artist, room):
    """All songs of an artist, in `room` (else where the user is, else where Spotify plays). Before 19.11.2026
    Jarvis could only open Spotify's search and said he had to start it himself."""
    import spotify
    artist = " ".join(str(artist or "").split())
    if not artist:
        return "Welchen Künstler soll ich spielen?"
    if not spotify.configured():
        return "Dafür brauche ich das Spotify-Login (docs/installation.md → Konfiguration → Spotify)."
    dev, where, player, stand_in, err = _room_device(room)
    if err:
        return err
    src = _speaker_stream(player)
    found = spotify.play_artist(artist, dev["id"] if dev else None)
    if not found:
        return f"Bei Spotify habe ich „{artist}“ nicht gefunden oder es ließ sich nicht starten."
    if src and src[0] != player:
        _pause_speaker(src)
    return stand_in + f"{found} läuft jetzt" + (f" {_in_room(where)}." if where else ".")


def _speaker_stream(target_player=""):
    """(HA player, spotify:track:…, position in ms) of a room speaker that streams Spotify itself — the Sonos with
    the wake-up music or a song asked of Alexa. Spotify's API does not see that playback, so moving it means
    starting the same track elsewhere. The target room's own player wins (the music is already there); None
    when no speaker plays such a track or Home Assistant cannot be asked."""
    import speakers
    from datetime import datetime, timezone
    players = [p for p in (speakers._cfg()[0].get(r, {}).get("player") for r in speakers.rooms()) if p]
    players.sort(key=lambda p: p != target_player)
    for p in players:
        try:
            s = _ha(f"/api/states/{p}") or {}
        except Exception:  # noqa: BLE001 — HA down: nothing known to move
            return None
        a = s.get("attributes") or {}
        m = re.search(r"spotify:track:([A-Za-z0-9]{22})", urllib.parse.unquote(str(a.get("media_content_id") or "")))
        if s.get("state") != "playing" or not m:
            continue
        pos = float(a.get("media_position") or 0)
        try:
            pos += (datetime.now(timezone.utc) - datetime.fromisoformat(a["media_position_updated_at"])).total_seconds()
        except (KeyError, TypeError, ValueError):
            pass
        return p, "spotify:track:" + m.group(1), int(max(0.0, pos) * 1000)
    return None


def _pause_speaker(src):
    """Stop the speaker the music came from, once it plays in the new room."""
    if not src:
        return
    try:
        _ha("/api/services/media_player/media_pause", {"entity_id": src[0]})
    except Exception:  # noqa: BLE001 — the account plays one stream at a time: Spotify stops it anyway
        pass


def _music_room_ha(player):
    """The way round Spotify Connect: the Sonos gets the playlist (else the track) through Home Assistant and
    plays it itself, the old device is paused. The position in the playlist is lost — Sonos starts it anew."""
    import spotify
    ctx, track, _ = spotify.now_playing()
    uri = ctx if ctx.startswith(("spotify:playlist:", "spotify:album:", "spotify:artist:")) else track
    if not uri:
        return False
    try:
        _ha("/api/services/media_player/play_media",
            {"entity_id": player, "media_content_type": "music", "media_content_id": uri, "enqueue": "replace"})
    except Exception:  # noqa: BLE001 — HA down or the Sonos has no Spotify account linked
        return False
    spotify.pause_active()
    return True


def _music(args):
    import ctypes
    import time
    import urllib.parse
    action = (args.get("action") or "").strip().lower()
    if action == "search":
        q = (args.get("query") or "").strip()
        if not q:
            return "Wonach soll ich suchen?"
        hostos.open_target("spotify:search:" + urllib.parse.quote(q))
        return f"Spotify zeigt die Suche nach „{q}“."
    import spotify
    room = " ".join(str(args.get("room") or "").split())
    if action in ("liked", "favorites", "lieblingssongs"):
        return _music_liked(room)
    if action in ("artist", "kuenstler", "künstler"):
        return _music_artist(args.get("query"), room)
    if action in ("transfer", "move", "room") or (room and action in ("play", "play_pause")):
        return _music_room(room)
    if action in ("play", "pause", "play_pause", "next", "previous"):
        if spotify.control(action):
            return f"Erledigt: {action}."
        spotify.forget()                  # the keys below decide; the wake word hold must not undo them
        action = "play_pause" if action in ("play", "pause") else action
    if action not in MEDIA_KEYS:
        return f"Unbekannte Aktion. Möglich: play, pause, {', '.join(MEDIA_KEYS)}, search."
    if action == "play_pause" and not _spotify_running():
        hostos.open_target("spotify:")
        time.sleep(4)   # let the app come up before it gets the key
    times = 5 if action.startswith("volume") else 1   # one key press is only ~2 %
    for _ in range(times):
        ctypes.windll.user32.keybd_event(MEDIA_KEYS[action], 0, 1, 0)   # KEYEVENTF_EXTENDEDKEY
        ctypes.windll.user32.keybd_event(MEDIA_KEYS[action], 0, 3, 0)   # ... | KEYUP
    return f"Erledigt: {action}."


MAIL_HOST = os.environ.get("MAIL_IMAP_HOST", "").strip()
MAIL_USER = os.environ.get("MAIL_USER", "").strip()
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "").strip()


MAIL_MARKED = os.environ.get("JARVIS_MAIL_MARKED", os.path.join(HERE, "logs", "mail_marked.json"))
# Marking more unread mails than this at once first returns the list instead (the user wants to hear
# the important ones before a whole pile disappears as "read" — 19.11.2026, 240 mails marked unseen).
MAIL_REVIEW_MIN = max(1, int(os.environ.get("JARVIS_MAIL_REVIEW_MIN", "15")))
_MAIL_HEADERS = "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"


def _mail_dec(v):
    import email.header
    try:
        return str(email.header.make_header(email.header.decode_header(v or ""))).strip()
    except Exception:  # noqa: BLE001 — a broken header must not stop the list
        return (v or "").strip()


def _mail_heads(box, ids, uid=False):
    """[(id, from, subject, date-or-None)] for `ids` in one round trip, headers only, nothing marked."""
    import email
    import email.utils
    if not ids:
        return []
    joined = b",".join(i if isinstance(i, bytes) else str(i).encode() for i in ids).decode()
    rows = (box.uid("fetch", joined, _MAIL_HEADERS) if uid else box.fetch(joined, _MAIL_HEADERS))[1]
    out = []
    for row in rows or []:
        if not isinstance(row, tuple):
            continue
        meta, raw = row[0], row[1]
        m = re.search(rb"UID (\d+)", meta) if uid else None
        key = m.group(1) if m else meta.split()[0]
        msg = email.message_from_bytes(raw)
        try:
            sent = email.utils.parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
        except Exception:  # noqa: BLE001
            sent = None
        out.append((key, _mail_dec(msg.get("From")), _mail_dec(msg.get("Subject")) or "(ohne Betreff)", sent))
    return out


def _mail_line(tag, frm, subj, sent):
    return f"{tag}{sent.strftime('%d.%m. %H:%M') if sent else '?'} · {frm[:50]} · {subj[:90]}"


def _mail_marked_load():
    try:
        with open(MAIL_MARKED, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _mail(args):
    import email
    import email.utils
    import mailboxes
    if not mailboxes.configured():
        return ("Postfach noch nicht verbunden: In .env fehlen MAIL_USER und MAIL_PASSWORD (weitere Konten als "
                "MAIL2_…), und beim Mail-Anbieter muss der IMAP-Zugriff aktiviert sein. Das trägt der User selbst ein.")
    if args.get("last_marked"):
        batch = _mail_marked_load()
        mails = batch.get("mails") or []
        if not mails:
            return "Ich habe mir noch keine Mails gemerkt, die ich als gelesen markiert habe."
        lines = [f"{m.get('date') or '?'} · {m.get('from', '')[:60]} · {m.get('subject', '')[:110]}" for m in mails[:300]]
        return (f"{len(mails)} Mails habe ich am {batch.get('at', '?')} als gelesen markiert (mit mail_mark_read "
                f"unread=true last_batch=true wieder ungelesen):\n" + "\n".join(lines))[:30000]

    def preview(msg):
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                body = part.get_payload(decode=True) or b""
                text = body.decode(part.get_content_charset() or "utf-8", "replace")
                return " ".join(text.split())[:120]
        return ""

    # up to 15 mails with a preview; more (up to 300) as a header list to look a pile through
    limit = max(1, min(int(args.get("limit") or 5), 300))
    full = limit <= 15
    days = max(0.0, min(float(args.get("days") or 0), mailboxes.READ_DAYS))
    hours = days * 24 if days else mailboxes.LOOKBACK_H
    q = (args.get("search") or "").strip().lower()
    # "kam heute was von X?" means read ones too; before, the search only saw the newest unread mails
    unread_only = args.get("unread_only", not q)
    many = len(mailboxes.accounts()) > 1
    crit = ("UNSEEN",) if unread_only else ()
    if q.isascii() and q:   # the server narrows to sender/subject first, so `limit` counts hits
        quoted = '"' + q.replace("\\", "").replace('"', "") + '"'
        crit += ("OR", "FROM", quoted, "SUBJECT", quoted)

    def read(acc, box):
        # box is read-only: nothing gets marked; never further back than the user asked (at most READ_DAYS)
        ids = mailboxes.recent(box, hours=hours, criteria=crit, asked=bool(days))
        tag = f"[{acc['name']}] " if many else ""
        rows = []
        if not full:
            for _, frm, subj, sent in _mail_heads(box, ids[-limit:]):
                if q and q not in frm.lower() and q not in subj.lower():
                    continue
                rows.append((sent.timestamp() if sent else 0, _mail_line(tag, frm, subj, sent)))
            return acc["name"], len(ids), rows
        for i in ids[-limit:][::-1]:
            _, parts = box.fetch(i, "(BODY.PEEK[])")
            msg = email.message_from_bytes(parts[0][1])
            frm, subj = _mail_dec(msg.get("From")), _mail_dec(msg.get("Subject"))
            if q and q not in frm.lower() and q not in subj.lower():
                continue
            sent = email.utils.parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
            rows.append((sent.timestamp() if sent else 0,
                         f"{tag}{sent.strftime('%d.%m. %H:%M') if sent else '?'} · {frm}\n  {subj}\n  {preview(msg)}"))
        return acc["name"], len(ids), rows

    results, errors = mailboxes.each(read, timeout=30 if days else 20)
    if not results:
        return "Postfach nicht erreichbar: " + "; ".join(errors)
    kind = "ungelesene " if unread_only else ""
    span = (f"{days:g} Tage" if days != 1 else "24 Stunden") if days else f"{mailboxes.LOOKBACK_H} Stunden"
    head = ", ".join(f"{n}: {c}" for n, c, _ in results) if many else str(results[0][1])
    head = (f"{head} {kind}Mails der letzten {span} im Posteingang."
            + (f" Nicht erreichbar: {'; '.join(errors)}." if errors else "")
            + ("" if days else f" Weiter zurück (bis {mailboxes.READ_DAYS} Tage) mit `days`.") + "\n")
    rows = sorted((r for _, _, rs in results for r in rs), key=lambda r: -r[0])[:limit]
    cap = 6000 if full else 30000
    return (head + "\n".join(text for _, text in rows))[:cap] if rows else head + "Keine passenden Mails."


def _mail_search(args):
    import mailboxes
    if not mailboxes.configured():
        return _mail({})
    q = " ".join(str(args.get("query") or args.get("search") or "").split())
    if not q:
        return "Wonach suchen? `query` = Absender oder Betreff-Wort angeben."
    days = max(1, min(int(args.get("days") or mailboxes.SEARCH_DAYS), mailboxes.SEARCH_DAYS))
    limit = max(1, min(int(args.get("limit") or 10), 25))
    in_text = bool(args.get("in_text"))
    many = len(mailboxes.accounts()) > 1

    def look(acc, box):
        return [dict(h, account=acc["name"]) for h in mailboxes.search(box, q, days, in_text=in_text)]

    results, errors = mailboxes.each(look, timeout=30)
    if not results:
        return "Postfach nicht erreichbar: " + "; ".join(errors)
    hits = [h for rs in results for h in rs]
    where = "Absender, Betreff oder Text" if in_text else "Absender oder Betreff"
    missing = f" Nicht erreichbar: {'; '.join(errors)}." if errors else ""
    if not hits:
        more = "" if in_text else " Mit in_text=true kann ich auch im Text suchen."
        return f"Keine Mail mit „{q}“ in {where} in den letzten {days} Tagen.{missing}{more}"
    hits.sort(key=lambda h: h["date"].timestamp() if h["date"] else 0, reverse=True)
    dated = [h["date"] for h in hits if h["date"]]
    span = (f", von {min(dated).strftime('%d.%m.%Y')} bis {max(dated).strftime('%d.%m.%Y')}" if dated else "")
    per = {}
    for h in hits:
        k = (f"{h['account']}/" if many else "") + h["folder"]
        per[k] = per.get(k, 0) + 1
    head = (f"{len(hits)} Mails mit „{q}“ in {where} (letzte {days} Tage{span}; "
            + ", ".join(f"{k}: {n}" for k, n in per.items()) + f").{missing}\nNeueste:\n")
    lines = []
    for h in hits[:limit]:
        tag = f"[{h['account']}/{h['folder']}] " if many else (f"[{h['folder']}] " if h["folder"] != "INBOX" else "")
        when = h["date"].strftime("%d.%m.%Y %H:%M") if h["date"] else "?"
        lines.append(f"{tag}{when} · {h['from'][:80]}\n  {h['subject'][:140]}")
    return (head + "\n".join(lines))[:6000]


def _mail_mark(args):
    import datetime as dt
    import mailboxes
    if not mailboxes.configured():
        return _mail({})
    q = (args.get("search") or "").strip().lower()
    unread = bool(args.get("unread"))
    last = bool(args.get("last_batch"))
    if not q and not args.get("all") and not last:
        return "Welche Mails? `search` (Absender/Betreff), `all`=true oder `last_batch`=true angeben."
    many = len(mailboxes.accounts()) > 1

    if last:   # undo: exactly the mails of the last "als gelesen markiert"
        batch = _mail_marked_load()
        want = {}
        for m in batch.get("mails") or []:
            want.setdefault(m.get("account"), []).append(str(m.get("uid")).encode())
        if not want:
            return "Ich habe mir keine zuletzt markierten Mails gemerkt."

    def find(acc, box):
        if last:
            uids = want.get(acc["key"]) or []
        else:
            uids = box.uid("search", None, "SEEN" if unread else "UNSEEN")[1][0].split()
            if unread:
                uids = uids[-200:]          # only look at the newest read mails
        hit = [h for h in _mail_heads(box, uids, uid=True)
               if last or not q or q in h[1].lower() or q in h[2].lower()]
        if unread and not last and not args.get("all"):
            hit = hit[-5:]              # "wieder ungelesen" means the latest few, not the whole archive
        return acc, hit

    found, errors = mailboxes.each(find, timeout=30)
    note = f" Nicht erreichbar: {'; '.join(errors)}." if errors else ""
    total = sum(len(h) for _, h in found)
    if not total:
        return "Keine passenden " + ("gelesenen" if unread else "ungelesenen") + " Mails gefunden." + note
    if not unread and args.get("all") and total > MAIL_REVIEW_MIN and not args.get("reviewed"):
        # a pile: the user hears what looks important first — nothing is marked yet
        rows = sorted(((s.timestamp() if s else 0, _mail_line(f"[{acc['name']}] " if many else "", f, subj, s))
                       for acc, hits in found for _, f, subj, s in hits), key=lambda r: -r[0])
        return (f"NICHTS markiert: {total} ungelesene Mails passen.{note} Sag dem User zuerst kurz, welche davon "
                "wichtig aussehen (Rechnungen, Mahnungen, Bank, Behörden, Verträge, Termine, Pakete, Mails von "
                "Menschen statt Newslettern) — Werbung und Newsletter nur als Anzahl. Erst wenn er dann sagt, dass "
                "markiert werden soll, mail_mark_read noch einmal mit denselben Angaben und reviewed=true.\n"
                + "\n".join(t for _, t in rows[:300]))[:30000]
    by_key = {acc["key"]: hits for acc, hits in found}

    def store(acc, box):
        hits = by_key.get(acc["key"]) or []
        if hits:
            box.uid("STORE", b",".join(u for u, *_ in hits).decode(), "-FLAGS" if unread else "+FLAGS", "(\\Seen)")
        return acc, hits

    done, errors2 = mailboxes.each(store, readonly=False, timeout=30)
    note = f" Nicht erreichbar: {'; '.join(errors + errors2)}." if errors or errors2 else ""
    hit = [(acc, h) for acc, hits in done for h in hits]
    if not hit:
        return "Markieren hat nicht geklappt." + note
    if not unread:   # remember the batch, so "welche hast du markiert?" and "mach die wieder ungelesen" work
        mails = [{"account": acc["key"], "uid": u.decode(), "from": f, "subject": subj,
                  "date": s.strftime("%d.%m.%Y %H:%M") if s else ""} for acc, (u, f, subj, s) in hit]
        try:
            os.makedirs(os.path.dirname(MAIL_MARKED), exist_ok=True)
            with open(MAIL_MARKED, "w", encoding="utf-8") as fh:
                json.dump({"at": dt.datetime.now().strftime("%d.%m.%Y %H:%M"), "mails": mails}, fh, ensure_ascii=False)
        except OSError as e:
            log.warning("mail_marked not saved: %s", e)
    what = "als ungelesen" if unread else "als gelesen"
    return (f"{len(hit)} Mails {what} markiert: " + "; ".join(h[2] for _, h in hit[:10]))[:1500] + note


def _weather(args):
    import datetime as dt
    import urllib.parse
    import briefing
    import jarvis_profile as profile
    lat, lon, where = briefing.LAT, briefing.LON, profile.place_suffix().strip(" ()") or "zu Hause"
    place = (args.get("place") or "").strip()
    if place:
        hits = briefing._get("https://geocoding-api.open-meteo.com/v1/search?count=1&language=de&name="
                             + urllib.parse.quote(place)).get("results") or []
        if not hits:
            return f"Ort '{place}' nicht gefunden."
        lat, lon = hits[0]["latitude"], hits[0]["longitude"]
        where = ", ".join(x for x in (hits[0].get("name"), hits[0].get("country")) if x)
    days = max(1, min(int(args.get("days") or 2), 14))
    d = briefing._get(
        "https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&timezone=%s&forecast_days=%d"
        "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,uv_index"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
        "precipitation_sum,wind_speed_10m_max,uv_index_max,sunshine_duration,sunrise,sunset"
        "&hourly=temperature_2m,precipitation_probability,weather_code"
        % (lat, lon, briefing.TZ_Q, max(days, 2)), timeout=15)
    sky = briefing.WEATHER_CODES
    wd = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
    c = d.get("current") or {}
    out = [f"Wetter {where}. Jetzt: {sky.get(c.get('weather_code'), 'wechselhaft')}, {c.get('temperature_2m')} °C "
           f"(gefühlt {c.get('apparent_temperature')}), Wind {c.get('wind_speed_10m')} km/h, UV {c.get('uv_index')}."]
    day = d["daily"]
    for i in range(days):
        date = dt.date.fromisoformat(day["time"][i])
        out.append(f"{wd[date.weekday()]} {date:%d.%m.}: {sky.get(day['weather_code'][i], 'wechselhaft')}, "
                   f"{day['temperature_2m_min'][i]:.0f} bis {day['temperature_2m_max'][i]:.0f} °C, "
                   f"Regen {day['precipitation_probability_max'][i] or 0} % ({day['precipitation_sum'][i] or 0} mm), "
                   f"Wind bis {day['wind_speed_10m_max'][i] or 0:.0f} km/h, UV {day['uv_index_max'][i] or 0:.0f}, "
                   f"Sonne {(day['sunshine_duration'][i] or 0) / 3600:.1f} h, "
                   f"Aufgang {day['sunrise'][i][-5:]}, Untergang {day['sunset'][i][-5:]}.")
    if args.get("hourly"):
        h, now = d["hourly"], dt.datetime.now().strftime("%Y-%m-%dT%H:00")
        start = next((k for k, t in enumerate(h["time"]) if t >= now), 0)
        out.append("Stündlich: " + "; ".join(
            f"{h['time'][k][-5:]} {h['temperature_2m'][k]:.0f}° {h['precipitation_probability'][k] or 0}% "
            f"{sky.get(h['weather_code'][k], 'wechselhaft')}"
            for k in range(start, min(start + 24, len(h["time"])), 2)))
    return "\n".join(out)


HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123").strip().rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "").strip()
# Climate control is limited below to room entities configured by the user.
HA_DOMAINS = {"light", "switch", "fan", "media_player", "scene", "climate"}
HA_SHOWN = HA_DOMAINS | {"sensor", "binary_sensor", "climate", "cover", "weather", "person"}
SINCE_ROWS = 6       # a search with at most this many hits also says since when (ha_since, one history call each)


def _ha(path, body=None):
    req = urllib.request.Request(HA_URL + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {HA_TOKEN}",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"null")


VERIFY_S = float(os.environ.get("JARVIS_VERIFY_SECONDS", "2.5"))


def _verify(eid, domain, service, data=None, wait=None):
    """Reads the state back after switching: only a confirmed state counts as done."""
    want = {"turn_on": "on", "turn_off": "off"}.get(service)
    if domain == "climate":
        import time as _t
        data = data or {}
        if service == "set_temperature":
            key, target = "temperature", data.get("temperature")
        elif service == "set_hvac_mode":
            key, target = "state", data.get("hvac_mode")
        else:
            return f"Abgelehnt: {service} ist für dieses Thermostat nicht freigegeben."
        end = _t.time() + (VERIFY_S if wait is None else wait)
        state = {}
        while True:
            try:
                state = _ha(f"/api/states/{eid}") or {}
                actual = state.get("state") if key == "state" else (state.get("attributes") or {}).get(key)
                if key == "temperature" and actual is not None and abs(float(actual) - float(target)) <= 0.1:
                    name = (state.get("attributes") or {}).get("friendly_name", eid)
                    return f"Erledigt: {name} steht auf {target:g} °C."
                if key == "state" and actual == target:
                    name = (state.get("attributes") or {}).get("friendly_name", eid)
                    return f"Erledigt: {name} ist auf {target}."
            except Exception:  # noqa: BLE001
                pass
            if _t.time() >= end:
                break
            _t.sleep(0.3)
        return (f"Gesendet, aber Home Assistant bestätigt {key}={target} für {eid} noch nicht. "
                "Nicht als erledigt melden.")
    if domain not in ("light", "switch", "fan") or not want:
        return f"Erledigt: {domain}.{service} für {eid}."
    import time as _t
    end, state = _t.time() + (VERIFY_S if wait is None else wait), "?"
    while True:
        try:
            state = (_ha(f"/api/states/{eid}") or {}).get("state", "?")
        except Exception:  # noqa: BLE001
            state = "?"
        if state == want or _t.time() >= end:
            break
        _t.sleep(0.3)
    if state == want:
        return f"Erledigt: {eid} ist jetzt {'an' if want == 'on' else 'aus'}."
    return (f"Gesendet, aber {eid} meldet noch '{state}' statt '{want}' — vielleicht offline oder träge. "
            "Nicht als erledigt melden.")


_ALEXA_MUSIC = re.compile(r"musik|spotify|song|lied|playlist|album|radio|podcast|hörbuch|\bspiel", re.I)


def _alexa_music_refused(eid, service, data):
    """Music never goes as a spoken Alexa command to an Echo: "Spiele meine Lieblingssongs im Wohnzimmer"
    landed on the Echo Dot instead of the soundbar (19.11.2026). The music tool picks the room's speaker
    (the soundbar, the Echo only when the soundbar stays unreachable). The Fire TV's own commands stay."""
    if not (eid.startswith("media_player.") and service == "play_media"):
        return ""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            data = {}
    data = data if isinstance(data, dict) else {}
    if str(data.get("media_content_type") or "").lower() != "custom":
        return ""
    import speakers
    if eid == speakers._cfg()[1]:
        return ""
    if not _ALEXA_MUSIC.search(str(data.get("media_content_id") or "")):
        return ""
    return ("Abgelehnt: Musik nicht als Alexa-Befehl an ein Echo schicken. Nimm das Werkzeug music "
            "(action liked für die Lieblingssongs, transfer oder play mit room): es spielt im Raum auf der Soundbar "
            "und nur ersatzweise auf dem Echo.")


def _home(name, args):
    if not HA_TOKEN:
        return ("Home Assistant noch nicht verbunden: In .env fehlt HA_TOKEN (langlebiges Zugriffstoken "
                "aus dem Home-Assistant-Profil). Das muss der User selbst eintragen.")
    try:
        if name == "home_states":
            q = (args.get("search") or "").lower()
            rows = []
            for s in _ha("/api/states"):
                eid = s["entity_id"]
                if eid.split(".")[0] not in HA_SHOWN or s["state"] in ("unavailable", "unknown"):
                    continue
                attrs = s.get("attributes", {})
                label = attrs.get("friendly_name", eid)
                if q and q not in eid.lower() and q not in label.lower():
                    continue
                if not q and eid.split(".")[0] not in HA_DOMAINS:
                    continue   # unfiltered: switchable devices only — sensors via `search`
                unit = attrs.get("unit_of_measurement", "")
                detail = f"{s['state']} {unit}".rstrip()
                if eid.startswith("climate."):
                    if attrs.get("current_temperature") is not None:
                        detail += f", Raum {attrs['current_temperature']} °C"
                    if attrs.get("temperature") is not None:
                        detail += f", Soll {attrs['temperature']} °C"
                rows.append((f"{label} ({eid}): {detail}".rstrip(), s))
            if q and len(rows) <= SINCE_ROWS:
                # a few devices asked for: say since when, so "seit wann ist das Licht an?" has an answer
                import ha_since
                for i, (row, s) in enumerate(rows):
                    if s["entity_id"].split(".")[0] in HA_DOMAINS:
                        try:
                            told = ha_since.stamp(s["entity_id"], s["state"], s.get("last_changed"))
                        except Exception:  # noqa: BLE001
                            told = ""
                        if told:
                            rows[i] = (f"{row}, {told}", s)
            return "\n".join(sorted(r for r, _ in rows))[:6000] or "Nichts gefunden."
        eid = (args.get("entity_id") or "").strip()
        domain, service = eid.split(".")[0], (args.get("service") or "").strip()
        if domain == "climate":
            import jarvis_profile
            home = jarvis_profile._load_home()
            groups = home.get("climate_groups") or {}
            allowed = {str(entity).strip() for entities in groups.values() if isinstance(entities, list)
                       for entity in entities if str(entity).startswith("climate.")}
            if not groups:
                allowed = {str(row[0]).strip() for row in home.get("rooms", [])
                           if isinstance(row, (list, tuple)) and len(row) >= 2
                           and str(row[0]).startswith("climate.")}
            if eid not in allowed:
                return "Abgelehnt: Dieses Thermostat ist nicht in config/home.json unter climate_groups freigegeben."
            state = _ha(f"/api/states/{eid}") or {}
            attrs = state.get("attributes") or {}
            data = dict(args.get("data") or {})
            if service in {"turn_off", "turn_on"}:
                if data:
                    return "Abgelehnt: turn_on/turn_off für Thermostate darf keine Zusatzwerte enthalten."
                service, data = "set_hvac_mode", {"hvac_mode": "off" if service == "turn_off" else "heat"}
            if service == "set_temperature":
                if set(data) != {"temperature"}:
                    return "Abgelehnt: Für ein Thermostat ist nur eine einzelne Solltemperatur erlaubt."
                try:
                    target = float(data["temperature"])
                    lo = max(5.0, float(attrs.get("min_temp", 5)))
                    hi = min(30.0, float(attrs.get("max_temp", 30)))
                except (TypeError, ValueError):
                    return "Abgelehnt: Bitte eine gültige Solltemperatur in °C angeben."
                if not lo <= target <= hi:
                    return f"Abgelehnt: Die Solltemperatur muss zwischen {lo:g} und {hi:g} °C liegen."
            elif service == "set_hvac_mode":
                if set(data) != {"hvac_mode"} or data.get("hvac_mode") not in {"heat", "off", "auto"}:
                    return "Abgelehnt: Erlaubt sind nur die Heizmodi heat, off oder auto."
                modes = attrs.get("hvac_modes") or []
                if modes and data["hvac_mode"] not in modes:
                    return f"Abgelehnt: Home Assistant meldet diese Modi nicht als verfügbar ({', '.join(map(str, modes))})."
            else:
                return "Abgelehnt: Für Thermostate sind nur Solltemperatur und Heizmodus freigegeben."
            _ha(f"/api/services/climate/{service}", dict(data, entity_id=eid))
            return _verify(eid, domain, service, data)
        if domain not in HA_DOMAINS:
            return f"Abgelehnt: {domain} darf ich nicht schalten, nur {', '.join(sorted(HA_DOMAINS))}."
        if not service.replace("_", "").isalnum():
            return "Abgelehnt: ungültiger Dienst."
        refused = _alexa_music_refused(eid, service, args.get("data"))
        if refused:
            return refused
        _ha(f"/api/services/{domain}/{service}", dict(args.get("data") or {}, entity_id=eid))
        return _verify(eid, domain, service)
    except urllib.error.HTTPError as e:
        return f"Home Assistant meldet Fehler {e.code}" + (" (Token ungültig?)" if e.code == 401 else "")
    except Exception as e:  # noqa: BLE001
        return f"Home Assistant nicht erreichbar: {e}"


SYNO_URL = os.environ.get("SYNOLOGY_URL", "").strip().rstrip("/")
SYNO_USER = os.environ.get("SYNOLOGY_USER", "").strip()
SYNO_PASSWORD = os.environ.get("SYNOLOGY_PASSWORD", "").strip()


def _syno(params, sid=None):
    import urllib.parse
    if sid:
        params = dict(params, _sid=sid)
    with urllib.request.urlopen(f"{SYNO_URL}/webapi/entry.cgi?{urllib.parse.urlencode(params)}",
                                timeout=20) as r:
        data = json.loads(r.read())
    if not data.get("success"):
        raise RuntimeError(f"DSM-Fehler {data.get('error', {}).get('code', '?')} bei {params.get('api')}")
    return data.get("data") or {}


def _nas_status():
    if not (SYNO_USER and SYNO_PASSWORD):
        return ("NAS noch nicht verbunden: In .env fehlen SYNOLOGY_USER und SYNOLOGY_PASSWORD (eigener DSM-Benutzer "
                "in der Gruppe administrators, ohne Zwei-Faktor). Das trägt der User selbst ein.")
    try:
        sid = _syno({"api": "SYNO.API.Auth", "version": 6, "method": "login", "account": SYNO_USER,
                     "passwd": SYNO_PASSWORD, "session": "jarvis", "format": "sid"})["sid"]
    except Exception as e:  # noqa: BLE001
        return f"NAS-Anmeldung fehlgeschlagen ({e}). 400/401 = Benutzer oder Passwort falsch, 403 = Zwei-Faktor aktiv."
    gb = lambda b: f"{int(b) / 1e9:.0f} GB" if int(b) < 1e12 else f"{int(b) / 1e12:.1f} TB"  # noqa: E731
    rows = []
    try:
        for label, call in (
            ("system", {"api": "SYNO.Core.System", "version": 1, "method": "info"}),
            ("last", {"api": "SYNO.Core.System.Utilization", "version": 1, "method": "get"}),
            ("speicher", {"api": "SYNO.Storage.CGI.Storage", "version": 1, "method": "load_info"}),
        ):
            try:
                d = _syno(call, sid)
            except Exception as e:  # noqa: BLE001
                rows.append(f"{label}: nicht lesbar ({e})")
                continue
            if label == "system":
                rows.append(f"modell: {d.get('model', '?')}, {d.get('firmware_ver', '?')}, "
                            f"temperatur {d.get('sys_temp', '?')} C, laufzeit {d.get('up_time', '?')}")
            elif label == "last":
                cpu, mem = d.get("cpu", {}), d.get("memory", {})
                rows.append(f"cpu: {int(cpu.get('user_load', 0)) + int(cpu.get('system_load', 0))} %, "
                            f"ram: {mem.get('real_usage', '?')} %")
            else:
                for v in d.get("volumes", []):
                    size = v.get("size", {})
                    used, total = int(size.get("used", 0)), int(size.get("total", 0)) or 1
                    rows.append(f"volume {v.get('display_name', v.get('id'))}: {gb(used)} von {gb(total)} "
                                f"belegt ({used * 100 // total} %), status {v.get('status', '?')}")
                for k in d.get("disks", []):
                    rows.append(f"platte {k.get('name', k.get('id'))} ({k.get('model', '').strip()}, "
                                f"{gb(k.get('size_total', 0))}): status {k.get('status', '?')}, "
                                f"smart {k.get('smart_status', '?')}, {k.get('temp', '?')} C")
    finally:
        try:
            _syno({"api": "SYNO.API.Auth", "version": 6, "method": "logout", "session": "jarvis"}, sid)
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(rows)[:4000]


GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL", "").strip()
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD", "").strip()
GARMIN_TOKENS = os.path.join(HERE, ".garmin_tokens")   # login once, then reuse the session tokens


def _garmin(args):
    import datetime as dt
    if not (GARMIN_EMAIL and GARMIN_PASSWORD):
        return "Garmin noch nicht verbunden: In .env fehlen GARMIN_EMAIL und GARMIN_PASSWORD. Das trägt der User selbst ein."
    try:
        from garminconnect import Garmin
    except ImportError:
        return "Das Python-Paket garminconnect fehlt noch (pip install garminconnect in der .venv)."
    try:
        g = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD, return_on_mfa=True)
        needs_mfa, _ = g.login(GARMIN_TOKENS)   # reuses stored tokens, else logs in with the password
        if needs_mfa:
            return "Garmin verlangt Zwei-Faktor-Anmeldung — die kann ich nicht beantworten. Bitte dort abschalten."
        os.makedirs(GARMIN_TOKENS, exist_ok=True)
        g.client.dump(GARMIN_TOKENS)
    except Exception as e:  # noqa: BLE001
        return f"Garmin-Anmeldung fehlgeschlagen ({e}). Passwort richtig, Zwei-Faktor aus?"
    hm = lambda s: f"{int(s or 0) // 3600}:{int(s or 0) % 3600 // 60:02d} h"  # noqa: E731
    rows = []
    days = max(1, min(int(args.get("days") or 1), 14))
    for i in range(days):
        day = (dt.date.today() - dt.timedelta(days=i)).isoformat()
        parts = [day]
        try:
            s = (g.get_sleep_data(day) or {}).get("dailySleepDTO") or {}
            if s.get("sleepTimeSeconds"):
                score = ((s.get("sleepScores") or {}).get("overall") or {}).get("value", "?")
                parts.append(f"schlaf {hm(s['sleepTimeSeconds'])} (tief {hm(s.get('deepSleepSeconds'))}, "
                             f"REM {hm(s.get('remSleepSeconds'))}, wach {hm(s.get('awakeSleepSeconds'))}), score {score}")
        except Exception as e:  # noqa: BLE001
            parts.append(f"schlaf nicht lesbar ({e})")
        try:
            st = g.get_stats(day) or {}
            parts.append(f"schritte {st.get('totalSteps', '?')}, ruhepuls {st.get('restingHeartRate', '?')}, "
                         f"stress {st.get('averageStressLevel', '?')}, body battery "
                         f"{st.get('bodyBatteryLowestValue', '?')}–{st.get('bodyBatteryHighestValue', '?')}, "
                         f"jetzt {st.get('bodyBatteryMostRecentValue', '?')}")
        except Exception as e:  # noqa: BLE001
            parts.append(f"tageswerte nicht lesbar ({e})")
        rows.append(" · ".join(parts))
    return "\n".join(rows)[:5000]


PI_HOST = os.environ.get("JARVIS_PI_HOST", "").strip()
PI_USER = os.environ.get("JARVIS_PI_USER", "pi").strip()
# Fixed, read-only command — the model cannot change what runs here.
PI_STATUS_CMD = (
    "echo host: $(hostname); echo uptime: $(uptime -p); "
    "echo temp: $(awk '{printf \"%.1f C\", $1/1000}' /sys/class/thermal/thermal_zone0/temp 2>/dev/null); "
    "echo load: $(cut -d' ' -f1-3 /proc/loadavg); "
    "free -m | awk '/Mem:/{print \"ram: \" $3 \" von \" $2 \" MB belegt\"}'; "
    "df -h / | awk 'NR==2{print \"disk: \" $3 \" von \" $2 \" belegt (\" $5 \")\"}'; "
    "for s in pihole-FTL docker home-assistant@homeassistant hassio-supervisor; do "
    "st=$(systemctl is-active $s 2>/dev/null); [ \"$st\" != inactive ] && [ -n \"$st\" ] && echo \"dienst $s: $st\"; done; "
    "command -v docker >/dev/null && docker ps --format 'container {{.Names}}: {{.Status}}' 2>/dev/null | head -12; "
    "true"
)


def _network_devices():
    ps = ("Get-NetNeighbor -AddressFamily IPv4 | Where-Object { $_.State -ne 'Unreachable' -and "
          "$_.LinkLayerAddress -and $_.LinkLayerAddress -notmatch '^(00-00-00-00-00-00|FF-FF-FF-FF-FF-FF|01-00-5E)' } | "
          "ForEach-Object { $n = try { [System.Net.Dns]::GetHostEntry($_.IPAddress).HostName } catch { '' }; "
          "'{0,-16} {1,-18} {2}' -f $_.IPAddress, $_.LinkLayerAddress, $n }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         timeout=60, **hostos.QUIET)
    return (out.stdout.strip() or out.stderr.strip() or "Keine Geräte gefunden.")[:4000]


def _pi_status():
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", f"{PI_USER}@{PI_HOST}",
                          PI_STATUS_CMD], capture_output=True, text=True, timeout=40,
                         **hostos.QUIET)
    if out.returncode != 0 and not out.stdout.strip():
        return f"Pi nicht erreichbar ({PI_USER}@{PI_HOST}): {out.stderr.strip()[:300]}"
    return out.stdout.strip()[:4000]


# Commands pi_run refuses whatever the card said: these are not "a command on the Pi" but its end.
_PI_RUN_FORBIDDEN = re.compile(r"(rm\s+(-\w*\s+)*-\w*[rR]\w*\s+/(\s|$|\*)|mkfs|dd\s.*of=/dev/|:\(\)\s*\{|"
                               r">\s*/dev/(sd|mmcblk)|shred|wipefs)")


def _pi_run(command):
    cmd = " ".join(str(command or "").split())
    if not cmd:
        return "Kein Befehl angegeben."
    if not PI_HOST:
        return "Pi nicht konfiguriert: JARVIS_PI_HOST fehlt in .env."
    if _PI_RUN_FORBIDDEN.search(cmd):
        return f"Abgelehnt: '{cmd[:80]}' würde den Pi zerstören — das führe ich grundsätzlich nicht aus."
    try:
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", f"{PI_USER}@{PI_HOST}", cmd],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                             **hostos.QUIET)
    except subprocess.TimeoutExpired:
        return f"Zeitüberschreitung: '{cmd[:80]}' lief länger als 120 s (läuft eventuell noch weiter)."
    text = (out.stdout or "").strip()
    err = (out.stderr or "").strip()
    if out.returncode == 255 and not text:
        return f"Pi nicht erreichbar ({PI_USER}@{PI_HOST}): {err[:300]}"
    parts = [f"exit {out.returncode}"]
    if text:
        parts.append(text[:3500])
    if err:
        parts.append("stderr: " + err[:800])
    if not text and not err:
        parts.append("(keine Ausgabe)")
    return "\n".join(parts)


def _calendar(name, args):
    import datetime as dt
    import icloud_calendar as cal
    if not cal.configured():
        return ("Kalender noch nicht eingerichtet: In .env fehlen ICLOUD_USER und ICLOUD_APP_PASSWORD "
                "(app-spezifisches Passwort von account.apple.com). Das muss der User selbst eintragen.")
    try:
        if name == "calendar_events":
            start = args.get("start")
            day = dt.datetime.strptime(start[:10], "%Y-%m-%d").date() if start else None
            names = ", ".join(n for n, _ in cal.calendars())
            return f"Kalender: {names}\n" + cal.describe(cal.events(day, args.get("days") or 1))
        if _calendar_past(args):
            return f"Nicht eingetragen — der Termin ({args.get('start')}) liegt in der Vergangenheit."
        same = _calendar_clash(args).get("same")
        if same:
            return f"Nicht eingetragen — {cal.clash_text({'same': same})}."
        return cal.add_event(args.get("title", ""), args.get("start", ""), args.get("end"),
                             args.get("location", ""), args.get("notes", ""), args.get("calendar", ""))
    except Exception as e:  # noqa: BLE001
        return f"Kalenderfehler: {e}"


def _internal(op, args):
    """Timers live in the HUD server (it rings them), so their tools ask it over
    the same local, per-launch token as the permission gate."""
    url = PERM_URL.replace("/api/permission/request", "/api/internal")
    body = json.dumps({"op": op, "args": args}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "content-type": "application/json", "X-Jarvis-Perm": PERM_TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("text") or "?"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return f"Jarvis-Server nicht erreichbar ({e})."


def _calendar_past(args):
    """True when the new event starts before now (an all-day one: before today). An odd date is
    left to the calendar to refuse."""
    import datetime as dt
    s = str(args.get("start") or "").strip().replace("T", " ")
    try:
        if len(s) <= 10:
            return dt.date.fromisoformat(s) < dt.date.today()
        return dt.datetime.strptime(s[:16], "%Y-%m-%d %H:%M") < dt.datetime.now()
    except ValueError:
        return False


def _calendar_clash(args):
    """What the calendar already holds around a new event ({} when it cannot be read)."""
    import icloud_calendar as cal
    if not cal.configured():
        return {}
    try:
        return cal.check(args.get("title", ""), args.get("start", ""), args.get("end"))
    except Exception:  # noqa: BLE001 — unreadable calendar or odd date: the card still comes
        return {}


def _approve(tool_name, tool_input):
    """The card for a tool. A new calendar event is checked against the calendar first: one that
    is already there is refused before any card, an overlap is named on the card."""
    if tool_name.endswith("calendar_add"):
        import icloud_calendar as cal
        if _calendar_past(tool_input):   # a booking mail from last week is no reason to fill the past
            return {"behavior": "deny", "message": f"Nicht vorgeschlagen — der Termin ({tool_input.get('start')}) liegt "
                                                   "in der Vergangenheit. Dem User nur sagen, dass er schon vorbei ist."}
        clash = _calendar_clash(tool_input)
        if clash.get("same"):
            return {"behavior": "deny", "message": f"Nicht vorgeschlagen — {cal.clash_text(dict(clash, overlap=[]))}. "
                                                   "Dem User sagen, dass der Termin schon eingetragen ist."}
        hint = cal.clash_text(clash) if clash else ""
        if hint:
            verdict = _ask(tool_name, dict(tool_input, _hinweis=hint))
            if verdict.get("behavior") == "allow":
                verdict["updatedInput"] = tool_input
            return verdict
    return _ask(tool_name, tool_input)


def _ask(tool_name, tool_input):
    body = json.dumps({"tool_name": tool_name, "input": tool_input}).encode()
    req = urllib.request.Request(PERM_URL, data=body, method="POST", headers={
        "content-type": "application/json", "X-Jarvis-Perm": PERM_TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=PERM_TIMEOUT + 15) as r:
            allowed = bool(json.loads(r.read()).get("allow"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"behavior": "deny", "message": f"Bestätigung nicht möglich ({e}). Abgelehnt."}
    if allowed:
        return {"behavior": "allow", "updatedInput": tool_input}
    return {"behavior": "deny", "message": "Der User hat diese Aktion nicht bestätigt. Nicht ausführen, keinen Umweg versuchen."}


def _open(url):
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url if url and "://" not in url else ""
    if not url:
        return "Abgelehnt: nur http(s)-Adressen."
    if BROWSER and os.path.exists(BROWSER):
        subprocess.Popen([BROWSER, url], close_fds=True,
                         **hostos.DETACHED)
    else:
        hostos.open_target(url)      # system default browser
    return f"Geöffnet: {url}"


def _reply(mid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": mid}
    if error:
        msg["error"] = {"code": -32601, "message": error}
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def call_tool(name, args):
    """Runs one tool and returns its text, or None for an unknown tool. No policy here — see handle_call."""
    text = None
    if name == "approve":
        text = json.dumps(_approve(args.get("tool_name", "?"), args.get("input") or {}))
    elif name == "open_website":
        text = _open(args.get("url"))
    elif name in ("calendar_events", "calendar_add"):
        text = _calendar(name, args)
    elif name == "music":
        try:
            text = _music(args)
        except Exception as e:  # noqa: BLE001
            text = f"Musik-Fehler: {e}"
    elif name == "mail_inbox":
        text = _mail(args)
    elif name == "mail_search":
        text = _mail_search(args)
    elif name == "mail_mark_read":
        text = _mail_mark(args)
    elif name in ("announce", "tv_control"):
        try:
            import speakers
            text = (speakers.announce(args.get("raum"), args.get("text")) if name == "announce"
                    else speakers.tv(args.get("befehl"), args.get("app") or ""))
        except Exception as e:  # noqa: BLE001
            text = f"Fehler: {e}"
    elif name == "weather_alerts":
        try:
            import weather_alerts
            text = weather_alerts.current()
        except Exception as e:  # noqa: BLE001
            text = f"Wetterwarnungen-Fehler: {e}"
    elif name == "weather":
        try:
            text = _weather(args)
        except Exception as e:  # noqa: BLE001
            text = f"Wetterdienst nicht erreichbar: {e}"
    elif name in ("nas_status", "garmin_health"):
        try:
            text = _nas_status() if name == "nas_status" else _garmin(args)
        except Exception as e:  # noqa: BLE001
            text = f"Fehler: {e}"
    elif name in ("home_states", "home_control"):
        text = _home(name, args)
    elif name in ("network_devices", "pi_status", "pi_run"):
        try:
            text = {"network_devices": _network_devices, "pi_status": _pi_status,
                    "pi_run": lambda: _pi_run(args.get("command"))}[name]()
        except Exception as e:  # noqa: BLE001
            text = f"Fehler: {e}"
    elif name == "web_fetch":
        text = _web_fetch(args.get("url"))
    elif name in ("reminder_add", "reminder_list", "reminder_cancel"):
        text = _internal(name, args)
    elif name in ("list_read", "list_add", "list_remove"):
        try:
            import lists
            text = lists.tool(name, args)
        except Exception as e:  # noqa: BLE001
            text = f"Listen-Fehler: {e}"
    elif name in ("routine_list", "routine_save", "routine_delete"):
        try:
            import routines
            text = routines.tool(name, args)
        except Exception as e:  # noqa: BLE001
            text = f"Routinen-Fehler: {e}"
    elif name == "memory_review":
        import memory_intel
        text = memory_intel.review()
    elif name in ("memory_overview", "memory_why", "memory_forget", "memory_export"):
        try:
            import memory_tools as mt
            if name == "memory_overview":
                text = mt.overview(args.get("note") or "")
            elif name == "memory_why":
                text = mt.why(args.get("text"))
            elif name == "memory_forget":
                text = mt.forget(args.get("text"), args.get("note") or "")
            else:
                text = f"Exportiert: {mt.export()}"
        except Exception as e:  # noqa: BLE001
            text = f"Gedächtnis-Fehler: {e}"
    elif name in ("window_arrange", "file_search", "file_read", "clipboard_read", "screen_read",
                  "script_list", "script_run"):
        try:
            import desktop
            text = {"window_arrange": lambda: desktop.arrange(args.get("app") or "", args.get("position") or "",
                                                              args.get("monitor") or 1),
                    "file_search": lambda: desktop.search(args.get("query")),
                    "file_read": lambda: desktop.read(args.get("path")),
                    "clipboard_read": desktop.clipboard,
                    "screen_read": lambda: desktop.screen_text(bool(args.get("ganz"))),
                    "script_list": desktop.script_list,
                    "script_run": lambda: desktop.script_run(args.get("name"))}[name]()
        except Exception as e:  # noqa: BLE001
            text = f"Windows-Fehler: {e}"
    elif name in ("mail_draft", "day_prep", "research_save", "watch_add", "watch_list", "watch_remove",
                  "plan_create", "plan_list", "plan_check"):
        try:
            import assistant as a
            text = {"mail_draft": lambda: a.mail_draft(args.get("to"), args.get("subject"), args.get("body"),
                                                       args.get("account") or ""),
                    "day_prep": lambda: a.day_prep(args.get("day") or None),
                    "research_save": lambda: a.research_save(args.get("title"), args.get("summary"), args.get("sources")),
                    "watch_add": lambda: a.watch_add(args.get("topic"), args.get("every_days") or 7),
                    "watch_list": a.watch_list,
                    "watch_remove": lambda: a.watch_remove(args.get("topic") or ""),
                    "plan_create": lambda: a.plan_create(args.get("title"), args.get("steps") or [], internal=_internal),
                    "plan_list": a.plan_list,
                    "plan_check": lambda: a.plan_check(args.get("title"), args.get("step"))}[name]()
        except Exception as e:  # noqa: BLE001
            text = f"Fehler: {e}"
    elif name in ("energy_coach", "energy_device_usage"):
        try:
            import energy_coach
            text = (energy_coach.report() if name == "energy_coach"
                    else energy_coach.set_usage(args.get("geraet") or "", bool(args.get("oft"))))
        except Exception as e:  # noqa: BLE001
            text = f"Energie-Coach-Fehler: {e}"
    elif name in ("pc_status", "net_diagnose", "backup_status", "home_check", "phone_notify"):
        try:
            import infra
            text = (infra.phone_notify(args.get("text")) if name == "phone_notify"
                    else getattr(infra, name)())
        except Exception as e:  # noqa: BLE001
            text = f"Fehler: {e}"
    elif name == "shipments_status":
        try:
            import shipments
            text = shipments.status_text()
        except Exception as e:  # noqa: BLE001
            text = f"Fehler: {e}"
    elif name in ("mail_events_scan", "mail_events_add", "mail_events_undo"):
        try:
            import mail_events
            if name == "mail_events_scan":
                text = mail_events.scan_text()
            elif name == "mail_events_add":
                text = mail_events.add(args.get("ids"))
            else:
                text = mail_events.undo(args.get("hours") or 24)
        except Exception as e:  # noqa: BLE001
            text = f"Fehler: {e}"
    elif name == "outage_impact":
        try:
            import outage_map
            text = outage_map.describe(args.get("question") or "")
        except Exception as e:  # noqa: BLE001
            text = f"Blast-Radius-Fehler: {e}"
    elif name == "pi_reports":
        try:
            import pi_reports
            text = pi_reports.describe(args.get("topic") or "alles")
        except Exception as e:  # noqa: BLE001
            text = f"Pi-Werkzeuge-Fehler: {e}"
    elif name in ("homelab_status", "metrics_query", "pihole_status"):
        try:
            import homelab
            text = {"homelab_status": lambda: homelab.describe(args.get("topic") or "alles"),
                    "metrics_query": lambda: homelab.query(args.get("expr"), args.get("hours") or 0),
                    "pihole_status": homelab.pihole}[name]()
        except Exception as e:  # noqa: BLE001
            text = f"Homelab-Fehler: {e}"
    elif name in ("security_findings", "security_accept", "security_scan"):
        try:
            import situation
            text = {"security_findings": situation.open_findings, "security_scan": situation.run_scan,
                    "security_accept": lambda: situation.accept(args.get("id"), args.get("grund"), args.get("tage"))}[name]()
        except Exception as e:  # noqa: BLE001
            text = f"Sicherheitszentrale-Fehler: {e}"
    elif name == "power_costs":
        try:
            import power_costs
            text = power_costs.report(args.get("geraet") or "", bool(args.get("standby")))
        except Exception as e:  # noqa: BLE001
            text = f"Stromkosten-Fehler: {e}"
    elif name == "homelab_report":
        try:
            import situation
            text = situation.report(args.get("topic") or "alles", bool(args.get("detail")))
        except Exception as e:  # noqa: BLE001
            text = f"Lagebericht-Fehler: {e}"
    elif name in ("updates_check", "update_install", "update_status"):
        try:
            import updates
            text = {"updates_check": updates.check, "update_status": updates.install_status,
                    "update_install": lambda: updates.install(args.get("target"))}[name]()
        except Exception as e:  # noqa: BLE001
            text = f"Update-Fehler: {e}"
    elif name == "screen_describe":
        try:
            import vision
            text = vision.describe(args.get("frage") or "", bool(args.get("ganz")))
        except Exception as e:  # noqa: BLE001
            text = f"Fehler: {e}"
    elif name in ("doc_search", "doc_status"):
        try:
            import docsearch
            text = docsearch.search(args.get("query")) if name == "doc_search" else docsearch.status()
        except Exception as e:  # noqa: BLE001
            text = f"Fehler: {e}"
    elif name == "forecast_status":
        import monitoring
        text = monitoring.describe()
    elif name in ("task_run", "task_status", "task_cancel"):
        try:
            import tasks
            if name == "task_run":
                text = tasks.start(args.get("recipe"))[1]
            elif name == "task_status":
                text = tasks.describe(args.get("id") or None)
            else:
                text = tasks.cancel(args.get("id") or None)
        except Exception as e:  # noqa: BLE001
            text = f"Fehler: {e}"
    elif name == "context_snapshot":
        try:
            import context
            text = context.describe(args.get("sources") or None)
        except Exception as e:  # noqa: BLE001
            text = f"Fehler: {e}"
    elif name in ("skill_list", "skill_undo"):
        try:
            import skill_factory
            text = skill_factory.listing() if name == "skill_list" else skill_factory.undo(args.get("text"))
        except Exception as e:  # noqa: BLE001
            text = f"Fehler: {e}"
    elif name == "resource_status":
        try:
            import resources
            text = resources.describe()
        except Exception as e:  # noqa: BLE001
            text = f"Fehler: {e}"
    elif name == "audit_today":
        import audit
        text = audit.today_summary(args.get("day") or None)
    elif name == "health_log":
        try:
            import weekly_review
            text = weekly_review.log_entry(args.get("what"), args.get("at") or "")
        except Exception as e:  # noqa: BLE001
            text = f"Notiz-Fehler: {e}"
    elif name in ("proposal_list", "proposal_accept", "proposal_decline", "rule_remove"):
        try:
            import proposals
            if name == "proposal_list":
                text = "\n".join(f"{p['id']}: {p['title']} — {p['why']}\n  {proposals.describe(p)}"
                                 for p in proposals.pending()) or "Keine offenen Vorschläge."
            elif name == "proposal_accept":
                text = proposals.accept(args.get("id"), internal=_internal)
            elif name == "proposal_decline":
                text = proposals.decline(args.get("id"))
            else:
                import reflection
                text = reflection.remove_rule(args.get("text"))
        except Exception as e:  # noqa: BLE001
            text = f"Vorschlags-Fehler: {e}"
    elif name in ("memory_search", "memory_read", "memory_save"):
        try:
            text = _memory(name, args)
        except Exception as e:  # noqa: BLE001
            text = f"Gedächtnis-Fehler: {e}"
    elif name == "memory_timeline":
        try:
            import episodes
            text = episodes.describe(args.get("start"), args.get("end"), args.get("query") or "", args.get("days"))
        except Exception as e:  # noqa: BLE001
            text = f"Fehler: {e}"
    elif name == "maintenance_status":
        try:
            import maintenance
            text = maintenance.describe()
        except Exception as e:  # noqa: BLE001
            text = f"Wartungs-Fehler: {e}"
    elif name in ("paperless_search", "paperless_read"):
        try:
            import paperless
            text = paperless.search(args.get("query")) if name == "paperless_search" else paperless.read(args.get("id"))
        except Exception as e:  # noqa: BLE001
            text = f"Paperless-Fehler: {e}"
    elif name == "unifi_status":
        try:
            import unifi
            text = unifi.describe(args.get("topic") or "clients")
        except Exception as e:  # noqa: BLE001
            text = f"UniFi-Fehler: {e}"
    elif name in ("media_recent", "media_search", "media_play"):
        try:
            import jellyfin
            text = {"media_recent": jellyfin.describe_recent,
                    "media_search": lambda: jellyfin.describe_search(args.get("query") or ""),
                    "media_play": lambda: jellyfin.play(args.get("item") or "", args.get("device") or "")}[name]()
        except Exception as e:  # noqa: BLE001
            text = f"Jellyfin-Fehler: {e}"
    else:
        return None
    return text


# Set once untrusted content (mail, web, files, screen) was read in this process —
# one process per request in lean mode, so it covers exactly one request.
TAINTED = {"on": False, "by": ""}


def handle_call(name, args):
    """Policy, audit and size cap around call_tool (policy.py has the rules)."""
    import audit
    import policy
    if name == "approve":
        return call_tool(name, args)
    cls = policy.action_class(name)
    refused = policy.blocked_reason(name, args)
    if refused:
        audit.tool(name, args, refused, cls)
        return refused
    if TAINTED["on"] and cls == policy.LIMITED_WRITE:
        hint = (f"Vorher wurden fremde Inhalte gelesen ({TAINTED['by']}). Deshalb frage ich, bevor ich etwas ändere.")
        verdict = _ask(f"mcp__jarvis__{name}", dict(args, _hinweis=hint))
        if verdict.get("behavior") != "allow":
            audit.write("card", tool=name, allow=False, reason="taint")
            return "Nicht ausgeführt: nach dem Lesen fremder Inhalte nicht bestätigt."
    text = call_tool(name, args)
    if text is None:
        return None
    # Keys and tokens leave the result here: before the audit line and before the cut
    # (half a key would no longer look like one).
    import content_guard
    text = content_guard.clean(name, text, untrusted=name in policy.UNTRUSTED)
    audit.tool(name, args, text, cls)
    if name in policy.UNTRUSTED:
        TAINTED.update(on=True, by=name)
    if len(text) > MAX_RESULT:
        text = text[:MAX_RESULT] + f" … [gekürzt, {len(text)} Zeichen]"
    if name in policy.UNTRUSTED:
        text = policy.wrap_untrusted(name, text)
    return text


def main():
    jarvis_log.setup("jarvis-mcp", console=False)     # its own file: one MCP process per request
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:          # notifications need no answer
            continue
        if method == "initialize":
            _reply(mid, {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2024-11-05"),
                         "capabilities": {"tools": {}},
                         "serverInfo": {"name": "jarvis", "version": "1.0"}})
        elif method == "tools/list":
            _reply(mid, {"tools": listed_tools()})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name, args = params.get("name"), params.get("arguments") or {}
            try:
                text = handle_call(name, args)
            except Exception as e:  # noqa: BLE001 — before, one broken tool took the whole MCP process down
                log.exception("tool %s failed: %s", name, str(e)[:200])
                text = f"Fehler im Werkzeug {name}: {str(e)[:200]}"
            if text is None:
                _reply(mid, error=f"unknown tool {name}")
                continue
            _reply(mid, {"content": [{"type": "text", "text": text}]})
        elif method == "ping":
            _reply(mid, {})
        else:
            _reply(mid, error=f"unsupported method {method}")


if __name__ == "__main__":
    main()
