"""
Open proposals from the learning modules — things Jarvis wants to change but
may only change after the user confirms a card.

A background job cannot show a card (cards exist only inside a turn in the
HUD). So it stores a proposal here and mentions it; in the next conversation
the proposal sits in the context block, Jarvis explains it in German and calls
proposal_accept — which always goes through the confirmation card.

Kinds and what accepting does:
  rules     learned rules → section "Gelernte Regeln" in the note Vorlieben (reflection.py)
  routine   a routine → config/routines.json (routines.py)
  reminder  a reminder → the scheduler in the HUD server
  idea      needs new code: only noted as wanted, Jarvis never changes his own code
  calendar  bookings found in mail (mail_events.py) -> into the calendar
  switch_off  what still runs after the user left home (away_check.py) or before he leaves for an
              appointment (departure.py) -> turned off through home_control; "climate" in the payload
              sets thermostats to the away temperature
  restart     a Docker container on the Pi whose Uptime Kuma monitor is down (kuma_alerts.py) ->
              `docker restart <container>`, only containers listed in config/homelab.json → kuma_restart
  habit       a question the user asks at the same time of day (user_habits.py) -> a job that says
              the answer by itself or fetches it into the cache beforehand

Stored in its own file (JARVIS_PROPOSALS), not in state.json, because both the
server and jarvis_mcp.py (a separate process) write it. Proposals expire after
JARVIS_PROPOSAL_DAYS (7) days.
"""
import json
import os
import pathlib
import secrets
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
PATH = pathlib.Path(os.environ.get("JARVIS_PROPOSALS", ROOT / "logs" / "proposals.json"))
DAYS = int(os.environ.get("JARVIS_PROPOSAL_DAYS", "7"))
KINDS = ("rules", "routine", "reminder", "idea", "skill", "calendar", "switch_off", "habit", "restart")
_LOCK = threading.Lock()


def _load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(items):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, PATH)


def _update(fn):
    with _LOCK:
        items = _load()
        out = fn(items)
        _save(items)
        return out


def add(kind, title, why, payload=None, source=""):
    """Stores a proposal; an open one of the same kind and source is replaced. Returns its id."""
    if kind not in KINDS:
        raise ValueError(kind)
    pid = secrets.token_hex(3)

    def fn(items):
        now = time.time()
        for p in items:
            if p["status"] == "open" and p["kind"] == kind and p.get("source") == source:
                p["status"] = "replaced"
        items.append(dict(id=pid, kind=kind, title=title.strip()[:120], why=why.strip()[:400],
                          payload=payload or {}, source=source, status="open", created=now,
                          expires=now + DAYS * 86400))
        del items[:max(0, len(items) - 60)]
    _update(fn)
    return pid


def pending():
    now = time.time()
    return [p for p in _load() if p["status"] == "open" and p.get("expires", 0) > now]


def get(pid):
    return next((p for p in _load() if p["id"] == (pid or "").strip()), None)


def resolve(pid, status):
    def fn(items):
        for p in items:
            if p["id"] == pid:
                p["status"], p["resolved"] = status, time.time()
                return p
        return None
    return _update(fn)


def describe(p):
    """What the card shows: in plain German, what happens on yes."""
    pl = p.get("payload") or {}
    if p["kind"] == "rules":
        body = "\n".join(f"• {r.get('regel', r) if isinstance(r, dict) else r}" for r in pl.get("rules") or [])
        return f"Diese Regeln in die Notiz Vorlieben übernehmen:\n{body}"
    if p["kind"] == "routine":
        steps = "; ".join(next(iter(s.values()), "") if isinstance(s, dict) else str(s) for s in pl.get("steps") or [])
        return f"Routine „{pl.get('name', p['title'])}“ anlegen. Auslöser: {', '.join(pl.get('triggers') or [])}. Schritte: {steps}"
    if p["kind"] == "reminder":
        return f"Erinnerung anlegen: {pl.get('text', p['title'])} um {pl.get('at', '?')}"
    if p["kind"] == "calendar":
        import mail_events
        listed = mail_events.card_lines(pl.get("ids"))
        return "Diese Termine aus Ihren Mails eintragen:\n" + ("\n".join(f"• {t}" for t in listed)
                                                               or "(nicht mehr gefunden)")
    if p["kind"] == "switch_off":
        return "Ausschalten:\n" + "\n".join(f"• {label}" for label in pl.get("labels") or pl.get("entities") or [])
    if p["kind"] == "habit":
        import user_habits
        when = user_habits._when(pl.get("weekdays") or range(7), pl.get("at", "08:00"))
        return (f"{when.capitalize()}: „{pl.get('text', p['title'])}“ "
                + ("von selbst sagen." if pl.get("mode") == "say" else "vorab holen, damit die Antwort sofort kommt."))
    if p["kind"] == "restart":
        return (f"Auf dem Pi den Container {pl.get('container', '?')} neu starten (docker restart). "
                f"Grund: Uptime Kuma meldet {pl.get('monitor', '?')} als ausgefallen.")
    if p["kind"] == "skill":
        import learning
        import skill_factory
        d = (learning.load().get("drafts") or {}).get(pl.get("key")) or {}
        problems, notes = skill_factory.check(pl.get("key"))
        return (f"Router-Regel aktivieren: „{d.get('example', pl.get('key'))}“ schaltet "
                f"{', '.join(d.get('entities', [])) or '?'} künftig lokal."
                + ("\nPrüfung: " + " ".join(problems) if problems else "\nPrüfung bestanden.")
                + (" " + " ".join(notes) if notes else ""))
    return f"Als gewünschte Fähigkeit vormerken (bauen müssen Sie sie in Claude Code): {p['title']}"


def block():
    """Context for the next turn: what is waiting for the user's yes or no."""
    items = pending()
    if not items:
        return ""
    rows = "\n".join(f"- {p['id']}: {p['title']} — {p['why']}" for p in items[:4])
    return ("Offene Vorschläge von dir (warten auf den User):\n" + rows +
            "\nSagt der User ja oder fragt danach: erst auf Deutsch erklären, was passiert und warum, dann "
            "proposal_accept mit id und begruendung. Bei nein: proposal_decline. Nicht von selbst aufdrängen, "
            "höchstens einmal kurz erwähnen, wenn es passt.")


def accept(pid, internal=None):
    """Carries out an accepted proposal. internal(op, args) reaches the HUD server (reminders)."""
    p = get(pid)
    if not p:
        return "Diesen Vorschlag gibt es nicht."
    if p["status"] != "open" or p.get("expires", 0) < time.time():
        return "Dieser Vorschlag ist nicht mehr offen."
    pl = p.get("payload") or {}
    if p["kind"] == "rules":
        import reflection
        result = reflection.apply_rules(pl.get("rules") or [])
    elif p["kind"] == "routine":
        import routines
        result = routines.tool("routine_save", pl)
    elif p["kind"] == "reminder":
        result = internal("reminder_add", pl) if internal else "Erinnerungen gehen nur über den Server."
    elif p["kind"] == "skill":
        import skill_factory
        result = skill_factory.activate(pl.get("key"))
    elif p["kind"] == "calendar":
        import mail_events
        result = mail_events.add(pl.get("ids"))
    elif p["kind"] == "switch_off":
        import jarvis_mcp
        # the same gate as every other switch: blocked domains stay blocked
        parts = [jarvis_mcp._home("home_control", {"entity_id": e, "service": "turn_off"}) or ""
                 for e in pl.get("entities") or []]
        for e, temp in (pl.get("climate") or {}).items():     # only climate_groups entities pass _home
            if float(temp) <= 0:                            # departure "climate_away": 0 = switch the thermostat off
                parts.append(jarvis_mcp._home("home_control", {"entity_id": e, "service": "turn_off"}) or "")
            else:
                parts.append(jarvis_mcp._home("home_control", {"entity_id": e, "service": "set_temperature",
                                                               "data": {"temperature": temp}}) or "")
        result = " ".join(parts)
    elif p["kind"] == "habit":
        import user_habits
        result = user_habits.add_job(pl)
    elif p["kind"] == "restart":
        import kuma_alerts
        result = kuma_alerts.restart(pl.get("container"))
    else:
        result = "Vorgemerkt."
    resolve(pid, "accepted")
    try:
        import suggestions
        suggestions.mark(p, "angenommen")
    except Exception:  # noqa: BLE001 — the note is a log, not a requirement
        pass
    return result


def decline(pid):
    p = resolve((pid or "").strip(), "declined")
    if not p:
        return "Diesen Vorschlag gibt es nicht."
    try:
        import suggestions
        suggestions.mark(p, "abgelehnt")
    except Exception:  # noqa: BLE001
        pass
    return "Verworfen."
