# Security

The model is never the security boundary. Permissions are enforced by code in `jarvis_mcp.handle_call`,
`policy.py` and the HUD confirmation gate; prompts only explain them.

## Action classes (`policy.py`)

| Class | Meaning | Gate |
|---|---|---|
| READ | reads, changes nothing | none |
| LIMITED_WRITE | small, reversible change (lamp, list entry, note bullet, timer, window) | none — but a card once untrusted content was read in the same request |
| CONFIRM | external effect, deletion, calendar, mail draft, scripts, rules | always a confirmation card |
| BLOCKED | never | refused in code |

Unknown tools default to CONFIRM. Blocked Home Assistant domains: `lock`, `alarm_control_panel`,
`camera`, `siren`, `valve`, `update`, `button`. `light`, `switch`, `fan`, `media_player` and `scene`
can be switched. Climate control is limited to the `climate.*` entities listed in `config/home.json`
under `climate_groups`; only `set_temperature` from 5–30 °C and a supported `set_hvac_mode` of `heat`, `off`
or `auto` are allowed.

Bookings from mail (`mail_events.py`): from senders whose address is at a domain in
`JARVIS_MAIL_EVENTS_TRUSTED` (default PC CADDIE, golftimer, Golf House) they are entered without a
card — the one exception to "background jobs only propose", chosen by the user. Fixed code reads
the calendar attachment, the model plays no part; links are stripped; every entry is announced and
`mail_events_undo` removes it (only entries Jarvis created, recognised by their own UID). Everything
else, and every other sender, goes through one confirmation card. A forged sender is the remaining
risk; the mail provider's spam filter guards against it.

Deliveries (`shipments.py`) are part of the same exception: an all-day entry "📦 Shop · Carrier"
on the expected day, from shipping mails of any sender (shops change too often for a list), with
the tracking number but never a link; announced, moved when the day changes, and removed by the
same `mail_events_undo`. Worst case of a forged shipping mail is one such note in the calendar.
Same-day services and work mails stay out. With `TRACK17_API_KEY`, 17TRACK (a third party) is told
each tracking number to follow it across carriers — nothing else (no address, no shop, no mail
content); with only `DHL_API_KEY`, DHL itself is asked about DHL numbers.

Facts from conversations (`reflection.remember`, since 10.11.2026, the user's choice): after a
conversation the learning loop may save up to three lasting facts the user said himself, without a
card, through the same `memory_save` path (duplicate and contradiction checks, `privacy.is_work`).
Only Profil, Vorlieben, Termine, Wissen, Golf, Gesundheit, Personen/… and Projekte/… — never Befehle,
Websites or Smart Home, because those steer behaviour. English lines (TV heard as the user) are
dropped before the model sees them; the prompt asks only for what the user said, not for mail, web or
calendar content. Each save is listed in the Lernprotokoll and removable with memory_forget; worst
case of an injected line is a wrong bullet tagged „abgeleitet“. `JARVIS_REMEMBER=0` turns it off.

Container restarts after an Uptime Kuma alarm (`kuma_alerts.py`, since 10.11.2026): the alarm only creates a
proposal of kind `restart`; nothing runs before proposal_accept and its card. The container name comes from
config/homelab.json → `kuma_restart` (never from the alarm text), is checked against `^[a-z0-9][a-z0-9_.-]+$`
and against that list again when the card is accepted, and the only command is `docker restart <name>` over the
pi_run SSH path. The weekly memory review strikes only bullets Jarvis saved by himself that week, without a card,
as the undo of his own background save.

Several mailboxes (`mailboxes.py`, `MAIL2_…`): all read-only except drafts (APPEND) and
"mark as read"; one failing account never blocks the others. Every module that reads mail over a
period (inbox, bookings, deliveries, parcels) goes through `mailboxes.recent` and never looks back
more than `JARVIS_MAIL_LOOKBACK_HOURS` (default 24) — less foreign content read, fewer old mails
to act on.

Before a calendar entry is offered or entered — the model's `calendar_add`, bookings from mail,
deliveries — code reads that day first (`icloud_calendar.check`): an entry with a similar title on
the same day means nothing is offered (for `calendar_add`, the request is refused before any card);
an overlap with another entry is named on the card and in the announcement.

Updates go only through `update_install` (CONFIRM, `updates.py`): one target per card, fixed commands
(the model picks the target, never what runs), nothing in the background. DSM itself and Docker images
are reported, not installed; so are FRITZ! repeaters (version read without login, compared with
download.avm.de) and smart-home firmware HA lists without an update entity (compared with the newest
device of the same model in the house). Checking talks to version.home-assistant.io, download.avm.de and
the image registries anonymously — it sends image names, nothing about the house.

## Sonos clip server (`sonos.py`)

With `JARVIS_SONOS` set, a second listener runs on the PC's LAN address (`JARVIS_SONOS_PORT`, default 8723),
because a Sonos only plays what it fetches itself. It serves nothing but voice clips and the live voice stream of the
current answer (which ends a few seconds after its last sentence): GET/HEAD only,
a random 128-bit name per clip, gone after 10 minutes, and only to private addresses. It reads nothing and takes
no commands. Which speaker a device uses is a per-device choice in `state.json` (`speech_outputs`) and only names
from `JARVIS_SONOS` are accepted.

Talking over him: once a voice has lasted ~0.4 s during his answer he pauses, and that clip is transcribed on the PC
even when the device's own wake word did not hear "Jarvis" — the user is in a conversation with him then. Without his
name it becomes a request only with real words, no TV/sport playing and not mostly his own words (his voice from the
Sonos); otherwise he goes on and nothing is stored.

## Voice engine (`voice_ws.py`, `voice_engine.py`)

The engine listens on `127.0.0.1:8724` (`config/voice.yaml` → `engine.bind/port`). The first message of a
connection must carry the HUD's per-launch token, otherwise the socket is closed; a paired device's cookie
names the device. Microphone frames never leave the machine: Silero VAD and Whisper run locally, only the
transcript goes where `/api/run` always sent it. The engine calls `/api/run`, `/api/cancel` and
`/api/permission/answer` on the loopback with the same token, host and origin checks as the HUD. An
interruption clip is transcribed without "Jarvis" only while he was speaking (the user is in a conversation);
without real words, with a stop word or as his own echo nothing is stored. `engine.debug_audio` writes
segments to `logs/voice_debug/` — off by default, local only.

## Logs and metrics

`logs/jarvis.log` (`jarvis_log.py`, JSON lines, 5 MB × 3) holds warnings and errors of every module with
the exception text, never a request body, a mail or a password: modules log what failed, not what was
said. The MCP server writes `logs/jarvis-mcp.log`. `GET /metrics` (`metrics.py`, no token, loopback
only) exposes counts and durations: routes, tokens per model, cards by outcome, background jobs,
stand-in cool-downs, HTTP status per API path, warnings per module, and today's tool calls by action
class from the audit log — numbers and fixed label values, no text from the user.

## Confirmation cards

- Shown in the HUD via Claude Code's `--permission-prompt-tool` (or `fallback_llm.run_tool` for the
  stand-ins). No answer within `JARVIS_PERM_TIMEOUT` (75 s) means no.
- The card shows Jarvis' German explanation (`begruendung`), the action, the target, the details
  and the consequence (`policy.describe`). Only "bestätigt"/"nein" by voice — a bare "ja" does not count.
- Each card covers exactly one call; an earlier yes never authorises a later action.
- Without an open HUD nobody can confirm, so the answer is no.
- `pi_run` (one shell command on the Pi over ssh) is CONFIRM: the card shows the exact command. The code
  refuses a short list of destructive commands (`rm -rf /`, `mkfs`, `dd` onto a device, fork bomb, `shred`,
  `wipefs`) before the card; everything else is the user's decision on the card. 120 s limit, output cut.

## Leaving home (`away_check.py`)

When the person entity in Home Assistant has been away for `JARVIS_AWAY_MINUTES`, Jarvis reads what still runs (lights on, media playing, listed plugs above their wattage), pushes one message to the phone and stores one `switch_off` proposal. Nothing is switched until that proposal is accepted on a card; accepting goes through `home_control`, so blocked domains stay blocked. Only the time of the last handled leaving is kept (`state.json` → `away_checked`), never a log of comings and goings.

Coming back after `JARVIS_WELCOME_MINUTES`: one spoken welcome from the notice log (`events.py` → `logs/notices.jsonl`, the last 300 of Jarvis' own announcements); the open `switch_off` proposal is dropped.

## The phone as a card (`phone_actions.py`)

A proposal that matters on the way (leaving home, leaving for an appointment) goes to the phone with
two buttons. The answer never reaches Jarvis directly: the companion app fires an event in Home
Assistant, a user-made automation writes it into a text helper, and Jarvis reads that helper with the
other states. `JARVIS_YES_<id>` runs `proposals.accept` for exactly that id — only an open proposal
Jarvis created himself, through the same code as the card (blocked domains stay blocked), audited as
`card` with reason `phone`; `JARVIS_NO_<id>` declines. Every helper value is handled once (its value
and `last_changed` are remembered) and the helper is emptied afterwards. Anything else in the helper is
ignored. Whoever controls the phone or Home Assistant can confirm a proposal this way — the same trust
as the HUD on a paired device.

## Leaving for an appointment (`departure.py`)

Like leaving home: nothing is switched. The proposal may also carry thermostats (`config/home.json` →
`departure`) that `proposals.accept` sets to the away temperature through `home_control`, so the
`climate_groups` restriction and the 5–30 °C range apply unchanged. A `climate_away` of 0 means
"switch off": the proposal then calls `turn_off`, which `home_control` maps to `set_hvac_mode off`.

## Ask-back choices (`learning.py` → `choices`)

When a switching request fits a lamp and a socket alike, the router asks back instead of calling the
model. The answer is kept for that wording in `config/learned.json` → `choices` without a card: the
user said it himself, in the same breath. Choices only ever select among the devices the router had
already matched and may switch; every protection (router, PC, fridge, blocked domains) still applies.
`skill_list` shows them; deleting the entry removes the choice.

## Focus sessions (`focus.py`)

A session holds `say` notices (shown, not spoken) — alarms and `critical` notices still ring. The
distraction check only reads the title of the active window on this PC, never its content, and
nothing is stored beyond the minutes counted. The playlist is started through the Spotify account.

## Other rooms and the TV (`speakers.py`)

`announce` (LIMITED_WRITE) speaks text in a room from `config/home.json` → `speakers`: an Echo through `notify.alexa_media_*`, a Sonos through `tts.speak`. `tv_control` (LIMITED_WRITE) sends a command to the Fire TV's Alexa as if spoken there — so only fixed commands (pause, lauter, …) and app names reach it: letters, digits, spaces, at most 30 characters, never words like kaufen, bestellen, anrufen, Routine or Skill. Nothing can be bought or called through Jarvis. Both go through Home Assistant; the untrusted-content card applies as for any LIMITED_WRITE.

A device's answers can be read aloud by an Echo instead (`alexa_voice.py`, chosen on the HUD's output button like a Sonos): the answer text goes to Amazon through `notify.alexa_media_*` (type tts) — the same text Jarvis would speak, nothing else. The morning briefing (`morning_echo.py`) only speaks, once a day, from fixed text; the follow-ups in the router ("heller", "wieder aus", "im Schlafzimmer auch") only act on what Jarvis switched in the last two minutes, and never switch protected devices off.

## Weather warnings and habits

`weather_alerts.py` asks api.brightsky.dev with the weather coordinates only. `house_habits.py` reads ten days of Home Assistant history for lights and the plugs in `away_check` (not `habits_ignore`); it only proposes routines (card) and mentions lights left on — it never switches.

## Security findings (`situation.py`)

`security_findings` reads the security centre's report (untrusted: device names come from DHCP). `security_accept` is CONFIRM: one finding, a reason of at least one sentence, 30/90/365 days — never open-ended (that stays on the website). `security_scan` only asks for a quick scan, which reads.

## Untrusted content

Mail, web pages, calendar entries, files, clipboard, screen text and day preparation are wrapped in
«FREMDE DATEN» markers (fake end markers inside are defused). After such a tool, every
LIMITED_WRITE call in the same request needs a card ("taint"). One MCP process per request in lean mode.

## Privacy

- Work topics are not logged, evaluated, saved, planned or watched (`privacy.py`, `JARVIS_WORK_TERMS`).
- Screen reading only on request, local Windows OCR; refused while a password manager, banking or
  messenger window is visible. The image never leaves the PC.
- Screen description (`vision.py`) only on request with a card for every picture; same window block; the
  picture goes to Claude in a call without any tools and is deleted afterwards. No mouse or keyboard control.
- Files only below `JARVIS_FILE_ROOTS`; path traversal is resolved and refused.
- The Gemini free tier may train on prompts, so it never sees mail, calendar, health or briefing turns.
- Background jobs that read conversations or health data use Claude or Groq/Ollama only.

## Learning without self-modification

- Router rules learned from Claude are drafts. They become active only after checks (devices exist and are
  switchable, no clash with routine triggers) and a confirmation card; every version is kept in
  `config/learned.history.jsonl` and can be undone (card). Checks only read — they never switch anything.
- A memory entry that contradicts an existing one is never overwritten; Jarvis asks the user.
- Check tasks (`tasks.py`) only read; findings come with a recovery plan in words, nothing is repaired automatically.
- The resource manager only unloads or pauses Jarvis' own components when they are idle; it never touches other programs.

## Emergency stop

"Jarvis, stopp alles" / "Not-Aus" (local, no LLM): open cards are denied, a running Claude process
is stopped, proactive messages, background jobs and planned commands pause for `JARVIS_PAUSE_HOURS`.
Alarms still ring. "weitermachen" resumes.

## Audit

`logs/audit.jsonl`: every tool call (class, shortened arguments with secrets masked, success),
every card decision, local actions, pauses. 90 days. "Was hast du heute gemacht?" reads it.

## Network

The server binds to `127.0.0.1` with a per-launch token, host and origin checks. Remote devices only
via `tailscale serve` on a second local port, paired with one-time codes, keys stored as hashes.

## Reporting

Private project. Problems go to the owner directly; never paste `.env` content into issues or logs.
