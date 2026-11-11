# CLAUDE.md — working on JARVIS

JARVIS is a German-speaking voice assistant for one person on Windows 11. The brain is the
Claude Code CLI on a subscription; a local intent router answers simple commands with 0 tokens.
Read `ARCHITECTURE.md` for the module map and `SECURITY.md` before touching tools or permissions.

## Run and test

```powershell
start-jarvis.bat                                            # supervisor + server on http://localhost:8720
.venv\Scripts\python.exe -m unittest discover -s tests      # all tests, no network, no Claude, no house
.venv\Scripts\python.exe -m unittest tests.test_policy -v   # one file
```

A running Jarvis restarts itself when its code changes. For a throwaway instance use another
port and state file (see `docs/installation.md`, "Entwicklung und Diagnose"); `JARVIS_RUNTIME=mock` avoids Claude calls.

## Conventions

- Python 3.12, standard library first. New dependencies only with the user's OK.
- Code, comments and docs in English; everything Jarvis **says**, router patterns and prompts in German.
- One module per feature in `jarvis/<group>/` (core, safety, tools, memory, voice, llm, watch), plain functions,
  module-level config from environment variables with defaults. Imports stay flat (`import memory`) — `paths.py`
  adds the group folders to `sys.path`; a new entry point must `import paths` first. Paths to config, logs, vault
  and ui come from the repository root (`Path(__file__).resolve().parents[2]`), never from the module's folder.
  A new module goes into the fitting group; module names must stay unique across groups.
- Every test file imports `tests/isolate.py` first — it moves state, vault, logs and config into a
  temp folder. A new data file needs an env var and an entry there.
- Comments explain why, not what. Match the surrounding density.
- Commit messages in German, author from the local git config (`Zedas030`, GitHub noreply address); no Claude co-author line.

## Rules that must not break

- Every Jarvis tool has an action class in `policy.py` (READ, LIMITED_WRITE, CONFIRM, BLOCKED);
  `tests/test_policy.py` fails otherwise. Cards are enforced in code, never only in the prompt.
- Content written by others (mail, web, calendar, files, clipboard, screen) is wrapped as untrusted
  data and taints the request (`jarvis_mcp.handle_call`).
- No work topics are stored anywhere (`privacy.py`).
- Background jobs never change anything themselves: they create a proposal (`proposals.py`) that
  the user accepts on a card. One exception, chosen by the user: `mail_events.py` enters bookings
  from senders in `JARVIS_MAIL_EVENTS_TRUSTED`, and `shipments.py` enters expected deliveries as
  all-day entries, straight into the calendar (fixed code, no model, no links, announced, undoable
  via `mail_events_undo`). A second one, chosen by the user on 10.11.2026: the learning loop
  (`reflection.remember`) saves up to `JARVIS_REMEMBER_MAX` lasting facts the user said himself through
  `memory_save` (duplicate/contradiction/work checks, only the notes in `REMEMBER_NOTES`, never Befehle,
  logged in the Lernprotokoll, removable with memory_forget). Do not widen either without asking.
- Jarvis never edits his own code at runtime.
- Never set `ANTHROPIC_API_KEY`; personal data lives only in `.env`, `config/*.json` (not the
  examples), `vault/`, `logs/`, `backups/`, `state.json` — all ignored by git.
- Publishing to GitHub: see the sanitized snapshot flow (branch `clean`). Never push `main`.
