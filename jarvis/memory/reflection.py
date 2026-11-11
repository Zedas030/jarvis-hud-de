"""
Learning loop: Jarvis reviews his own conversations and learns lasting rules.

After a conversation (JARVIS_REFLECT_IDLE_MIN, default 30 minutes of silence) the
new exchanges from the conversation log (vault/Gespraeche) are evaluated with one
small background call: what went well, what the user corrected, what to do
differently. The result is appended to the note Erinnerungen/Lernprotokoll.

Once a week (Sunday from JARVIS_REFLECT_WEEK_HOUR, default 19 h) the week's
entries are condensed into a few fixed rules. They are NOT written directly:
they become a proposal (proposals.py) that the user confirms on a card. Accepted
rules live in the note Vorlieben, in the section "Gelernte Regeln" between two
markers, and go into the system prompt of every turn (rules_block). "Streich die
Regel …" removes one again (tool rule_remove, also with a card).

Work topics never get in: exchanges that look like work are skipped before the
model sees them, and anything work-like in its answer is dropped (privacy.py).
"""
import datetime as dt
import os
import re
import time

import background
import commands
import events
import memory
import privacy
import proposals

IDLE_MIN = int(os.environ.get("JARVIS_REFLECT_IDLE_MIN", "30"))
MIN_EXCHANGES = int(os.environ.get("JARVIS_REFLECT_MIN_EXCHANGES", "2"))
WEEK_HOUR = int(os.environ.get("JARVIS_REFLECT_WEEK_HOUR", "19"))
MAX_RULES = int(os.environ.get("JARVIS_MAX_RULES", "20"))
ENABLED = os.environ.get("JARVIS_REFLECT", "1").strip() not in ("0", "false", "no", "off")
# Lasting facts the user said are saved by the same call, without a card (the user's choice, 10.11.2026):
# the same checks as memory_save (duplicates, contradictions, work), tagged „abgeleitet“ with the day,
# logged in the Lernprotokoll and removable with „Vergiss …“ (memory_forget).
REMEMBER = os.environ.get("JARVIS_REMEMBER", "1").strip() not in ("0", "false", "no", "off")
REMEMBER_MAX = int(os.environ.get("JARVIS_REMEMBER_MAX", "3"))
REMEMBER_NOTES = re.compile(r"^(Profil|Vorlieben|Termine|Wissen|Golf|Gesundheit|(Personen|Projekte)/[\w äöüÄÖÜß.\-]{2,40})$")

LOG_NOTE = "Lernprotokoll"
RULES_NOTE = "Vorlieben"
START = "<!-- jarvis:gelernte-regeln -->"
END = "<!-- /jarvis:gelernte-regeln -->"

_EXCHANGE = re.compile(r"\*\*(\d{1,2}:\d{2}) · [^*]+:\*\* (.*?)\n\n\*\*Jarvis:\*\* (.*?)(?=\n\*\*\d{1,2}:\d{2} · |\Z)", re.S)
_CORRECTION = re.compile(
    r"\b(nein|falsch|stimmt nicht|ich meinte|meinte ich|nicht das|nicht so|lass das|hör auf|stopp|zu lang|"
    r"zu laut|zu leise|schon wieder|nochmal|noch mal|hab ich doch|eigentlich wollte|du sollst|bitte nicht|"
    r"das war nicht)\b", re.I)

SYSTEM = ("Du bist JARVIS und wertest deine eigenen Gespräche mit dem User aus, um besser zu werden. "
          "Ehrlich, konkret, knapp. Arbeitsthemen (Job, Kollegen, Kunden, Projekte der Arbeit) lässt du komplett weg. "
          "Antworte ausschließlich mit einem JSON-Objekt.")


# ── conversation log → exchanges ─────────────────────────────

def exchanges(text):
    """[(HH:MM, user, jarvis)] from a conversation-log note."""
    return [(t, " ".join(u.split()), " ".join(a.split())) for t, u, a in _EXCHANGE.findall(text)]


def _new_exchanges():
    """Exchanges not evaluated yet, from today's and yesterday's log, plus the new read positions."""
    seen = commands.load().get("reflection_pos") or {}
    out, pos = [], {}
    for back in (1, 0):
        day = (dt.date.today() - dt.timedelta(days=back)).isoformat()
        path = memory.DIARY_DIR / f"Gespräch {day}.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        start = seen.get(path.name, 0)
        if start > len(text):
            start = 0
        out += [(day,) + e for e in exchanges(text[start:])]
        pos[path.name] = len(text)
    return out, pos


def _prompt(items):
    lines, hints = [], []
    for day, t, user, reply in items:
        lines.append(f"[{day} {t}] User: {user[:300]}\nJarvis: {reply[:400]}")
        if _CORRECTION.search(user):
            hints.append(f"[{day} {t}] {user[:120]}")
    try:
        import selfcheck
        misses = selfcheck.hints()
    except Exception:  # noqa: BLE001
        misses = []
    return ("Gespräche seit der letzten Auswertung:\n\n" + "\n\n".join(lines) +
            ("\n\nMögliche Korrekturen des Users:\n" + "\n".join(hints) if hints else "") +
            ("\n\nMögliche Fehlschläge (der User fragte sofort noch einmal, die erste Antwort passte wohl nicht):\n"
             + "\n".join(misses) if misses else "") +
            "\n\nAntworte mit JSON: {\"gut\": [...], \"korrigiert\": [...], \"lehren\": [...]"
            + (", \"merken\": [...]" if REMEMBER else "") + "}\n"
            "- gut: was gut lief (höchstens 3)\n"
            "- korrigiert: was der User korrigiert hat und was richtig gewesen wäre (höchstens 3)\n"
            "- lehren: allgemeine Lehre für künftige Gespräche, als Anweisung an dich (höchstens 3)\n"
            + (MERKEN if REMEMBER else "") +
            "Jeder Eintrag ein kurzer deutscher Satz (unter 140 Zeichen). Nichts erfinden; nichts Auffälliges = leere Listen.")


MERKEN = ("- merken: dauerhafte Fakten, die der USER selbst gesagt hat und die später nützen — über sich, Menschen "
          "in seinem Leben, Vorlieben, Termine, Hobbys. Als Liste von {\"note\": ..., \"text\": ...}; note ist "
          "Profil, Vorlieben, Termine, Wissen, Golf, Gesundheit, Personen/<Name> oder Projekte/<Name>; text ein kurzer "
          "Stichpunkt in der dritten Person. NICHT: was nur Jarvis gesagt hat, Inhalte aus Mails, Webseiten oder "
          "Kalender, Befehle, Gerätezustände, Einmaliges (\"heute müde\"), Fernsehton. Höchstens 3, sonst leer.\n")


def remember(items):
    """Saves the „merken“ facts through memory_save. Returns [(note, text, result)] for the log."""
    if not REMEMBER:
        return []
    import jarvis_mcp
    out = []
    for item in (items or [])[:REMEMBER_MAX]:
        if not isinstance(item, dict):
            continue
        note = " ".join(str(item.get("note") or "").split()).strip("/")
        text = " ".join(str(item.get("text") or "").split())[:160]
        if not text or not REMEMBER_NOTES.match(note) or privacy.is_work(note, text):
            continue
        res = jarvis_mcp._memory("memory_save", {"note": note, "text": text, "quelle": "abgeleitet"})
        out.append((note, text, res))
    return out


def _clean(items, limit=3):
    out = []
    for i in items or []:
        s = " ".join(str(i).split())[:160]
        if s and not privacy.is_work(s):
            out.append(s)
    return out[:limit]


def _note(name):
    return memory.MEMORY_DIR / f"{name}.md"


def append_log(result, when=None, saved=()):
    """Writes one evaluation to the Lernprotokoll note. Returns the number of bullets."""
    rows = ([f"- Gut: {s}" for s in _clean(result.get("gut"))] +
            [f"- Korrigiert: {s}" for s in _clean(result.get("korrigiert"))] +
            [f"- Lehre: {s}" for s in _clean(result.get("lehren"))] +
            [f"- Gemerkt ([[{note.split('/')[-1]}]]): {text}" if res.startswith("Gespeichert")
             else f"- Nicht gemerkt ([[{note.split('/')[-1]}]]): {text} — {res[:90]}" for note, text, res in saved])
    if not rows:
        return 0
    path = _note(LOG_NOTE)
    path.parent.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    if not path.exists():
        path.write_text(f"---\ntype: lernen\nwichtigkeit: niedrig\nupdated: {today}\n---\n\n# {LOG_NOTE}\n\n"
                        "Was Jarvis nach Gesprächen über sich selbst gelernt hat. Daraus entstehen jeden Sonntag "
                        "Vorschläge für [[Vorlieben]].\n", encoding="utf-8")
    body = path.read_text(encoding="utf-8")
    body = re.sub(r"(?m)^updated: .*$", f"updated: {today}", body, count=1)
    stamp = when or time.strftime("%Y-%m-%d %H:%M")
    path.write_text(body.rstrip() + f"\n\n## {stamp}\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return len(rows)


def reflect(force=False):
    """Evaluates the new exchanges if the conversation is over. Returns the number of bullets written."""
    last_turn = commands.load().get("last_turn_at") or 0
    if not force and time.time() - last_turn < IDLE_MIN * 60:
        return 0
    items, pos = _new_exchanges()
    import voice_wake
    # TV and YouTube heard as "the user" (English commentary) are no conversation to learn from
    kept = [e for e in items if not privacy.is_work(e[2], e[3]) and not voice_wake.foreign(e[2])]
    if len(kept) < (1 if force else MIN_EXCHANGES):
        if pos and not kept:          # only work talk (or nothing): mark as read anyway
            commands.update_state(lambda d: d.__setitem__("reflection_pos", pos))
        return 0
    result = background.parse_json(background.ask("reflection", SYSTEM, _prompt(kept[-30:])))
    if result is None:
        return 0                      # no model right now: try again later, positions unchanged
    commands.update_state(lambda d: d.__setitem__("reflection_pos", pos))
    return append_log(result, saved=remember(result.get("merken")))


# ── weekly: entries → proposed rules ─────────────────────────

def week_entries(days=7, today=None):
    path = _note(LOG_NOTE)
    if not path.exists():
        return []
    since = (today or dt.date.today()) - dt.timedelta(days=days)
    out = []
    for block in re.split(r"(?m)^## ", path.read_text(encoding="utf-8"))[1:]:
        head, _, rest = block.partition("\n")
        try:
            day = dt.date.fromisoformat(head.strip()[:10])
        except ValueError:
            continue
        if day > since:
            out += [ln[2:].strip() for ln in rest.splitlines() if ln.startswith("- ")]
    return out


def rules_list():
    path = _note(RULES_NOTE)
    if not path.exists():
        return []
    body = path.read_text(encoding="utf-8")
    if START not in body or END not in body:
        return []
    section = body.split(START, 1)[1].split(END, 1)[0]
    return [re.sub(r"\s*\(seit [\d-]+\)$", "", ln[2:].strip()) for ln in section.splitlines() if ln.startswith("- ")]


def rules_block():
    rules = rules_list()
    return ("Vom User bestätigte Regeln, die du aus früheren Gesprächen gelernt hast:\n"
            + "\n".join(f"- {r}" for r in rules)) if rules else ""


def _norm(s):
    return re.sub(r"\W+", " ", s.lower()).strip()


def _write_rules(rules_with_dates):
    path = _note(RULES_NOTE)
    path.parent.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    if path.exists():
        body = path.read_text(encoding="utf-8")
    else:
        body = (f"---\ntype: vorliebe\nwichtigkeit: mittel\nupdated: {today}\n---\n\n# {RULES_NOTE}\n\n"
                "Wie der User Dinge mag. Gehört zu [[Profil]].\n")
    section = (f"{START}\n## Gelernte Regeln\n\nVon Jarvis vorgeschlagen, vom User per Karte bestätigt.\n\n"
               + "\n".join(f"- {r} (seit {d})" for r, d in rules_with_dates) + f"\n{END}")
    if START in body and END in body:
        before, rest = body.split(START, 1)
        body = before + section + rest.split(END, 1)[1]
    else:
        body = body.rstrip() + "\n\n" + section + "\n"
    body = re.sub(r"(?m)^updated: .*$", f"updated: {today}", body, count=1)
    path.write_text(body, encoding="utf-8")


def _dated_rules():
    path = _note(RULES_NOTE)
    if not path.exists() or START not in path.read_text(encoding="utf-8"):
        return []
    section = path.read_text(encoding="utf-8").split(START, 1)[1].split(END, 1)[0]
    out = []
    for ln in section.splitlines():
        if ln.startswith("- "):
            m = re.match(r"- (.*?)\s*\(seit ([\d-]+)\)$", ln.strip())
            out.append((m.group(1), m.group(2)) if m else (ln[2:].strip(), time.strftime("%Y-%m-%d")))
    return out


def apply_rules(rules):
    """Adds confirmed rules to the note Vorlieben (oldest drop out beyond MAX_RULES)."""
    current = _dated_rules()
    known = {_norm(r) for r, _ in current}
    today = time.strftime("%Y-%m-%d")
    added = 0
    for r in rules:
        text = " ".join(str(r.get("regel") if isinstance(r, dict) else r).split())[:200]
        if text and _norm(text) not in known and not privacy.is_work(text):
            current.append((text, today))
            known.add(_norm(text))
            added += 1
    _write_rules(current[-MAX_RULES:])
    return f"{added} Regel(n) in Vorlieben übernommen." if added else "Nichts Neues — die Regeln standen schon drin."


def remove_rule(text):
    want = _norm(text or "")
    if not want:
        return "Welche Regel?"
    current = _dated_rules()
    keep = [(r, d) for r, d in current if want not in _norm(r)]
    if len(keep) == len(current):
        return "Keine gelernte Regel passt dazu."
    _write_rules(keep)
    return f"{len(current) - len(keep)} Regel(n) gestrichen."


def consolidate(today=None):
    """Turns the week's log entries into a rules proposal. Returns the proposal id or None."""
    entries = privacy.without_work(week_entries(today=today))
    if not entries:
        return None
    existing = rules_list()
    prompt = ("Einträge aus deinem Lernprotokoll dieser Woche:\n" + "\n".join(f"- {e}" for e in entries[:60]) +
              ("\n\nSchon bestehende Regeln (nicht wiederholen):\n" + "\n".join(f"- {r}" for r in existing)
               if existing else "") +
              "\n\nLeite daraus höchstens 5 feste Regeln für dich ab. Nur, was mehrfach vorkam oder ausdrücklich "
              "vom User korrigiert wurde. Jede Regel eine kurze Anweisung an dich (unter 140 Zeichen), dazu ein "
              "Grund. JSON: {\"regeln\": [{\"regel\": \"...\", \"grund\": \"...\"}]}. Nichts Belastbares = leere Liste.")
    result = background.parse_json(background.ask("reflection-week", SYSTEM, prompt)) or {}
    rules = [{"regel": " ".join(str(r.get("regel", "")).split())[:200],
              "grund": " ".join(str(r.get("grund", "")).split())[:200]}
             for r in (result.get("regeln") or []) if isinstance(r, dict)]
    known = {_norm(r) for r in existing}
    rules = [r for r in rules if r["regel"] and _norm(r["regel"]) not in known
             and not privacy.is_work(r["regel"], r["grund"])][:5]
    if not rules:
        return None
    week = background.week_key(today)
    pid = proposals.add("rules", f"{len(rules)} gelernte Regeln aus {week}",
                        "Aus dem Lernprotokoll der Woche: " + rules[0]["grund"], {"rules": rules}, source="reflection")
    events.publish("say", f"Sir, aus dieser Woche habe ich {len(rules)} Regeln für mich abgeleitet, zum Beispiel: "
                          f"{rules[0]['regel']} Wenn Sie mögen, sagen Sie „Regeln übernehmen“, dann zeige ich sie "
                          "Ihnen zur Bestätigung.", kind="learning")
    return pid


def step(now=None):
    now = now or dt.datetime.now()
    reflect()
    if now.weekday() == 6 and now.hour >= WEEK_HOUR and background.claim("reflection-week", background.week_key(now.date())):
        consolidate(now.date())


def start():
    if ENABLED:
        background.loop("reflection", 300, step)
