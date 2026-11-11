"""
"Was hast du heute gemacht?" — what Jarvis did by himself today, from his own logs. Local, 0 tokens.

    Heute 14 Anfragen, 9 davon selbst erledigt. Von selbst gemeldet: 3-mal, zuletzt um 16:40
    „Die Waschmaschine ist fertig“. Gemerkt: Anna mag Tulpen. Ein Vorschlag wartet: Container blast-radius
    neu starten. Mit Ihrer Bestätigung ausgeführt: 1 Aktion (Licht aus).

Sources: logs/notices.jsonl (everything he said by himself, events.py), the Lernprotokoll (what he saved),
proposals.py (what he proposed), logs/audit.jsonl (tools that changed something, with or without card) and
logs/usage.jsonl (requests, local share). Nothing is stored here; every question reads the files again.

  report(day)   the sentence
  handle(n)     router: "Was hast du heute gemacht?", "Was hast du gestern so gemacht?"
"""
import datetime as dt
import json
import pathlib
import re
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
_ASK = re.compile(r"\bwas hast du (heute|gestern)( so| alles| eigentlich)* (gemacht|getan|erledigt|getrieben)\b")
WRITES = {"LIMITED_WRITE", "CONFIRM"}


def _jsonl(path):
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def notices(day):
    import events
    out = []
    for r in _jsonl(events.NOTICES):
        at = r.get("at")
        if isinstance(at, (int, float)) and dt.date.fromtimestamp(at) == day and r.get("text"):
            out.append((at, r.get("kind") or "", r["text"]))
    return out


def saved(day):
    import memory_weekly
    return [it["text"] for it in memory_weekly.saved(1, day)]


def proposed(day):
    import proposals
    return [p for p in proposals._load() if dt.date.fromtimestamp(p.get("created") or 0) == day]


def actions(day):
    import audit
    stamp = day.isoformat()
    out = []
    for r in _jsonl(audit.PATH):
        if str(r.get("at", "")).startswith(stamp) and r.get("kind") == "tool" and r.get("cls") in WRITES and r.get("ok"):
            out.append(r)
    return out


def requests(day):
    import learning
    stamp = day.isoformat()
    rows = [r for r in learning.rows(2) if str(r.get("at", "")).startswith(stamp)
            and not str(r.get("route", "")).startswith("background")]
    local = [r for r in rows if str(r.get("route", "")).startswith(("local", "cache", "briefing-local"))]
    return len(rows), len(local)


def _short(text, n=70):
    text = re.sub(r"^Sir,\s*", "", " ".join(str(text).split()))
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …"


def report(day=None):
    today = dt.date.today()
    day = day or today
    word = "Heute" if day == today else "Gestern" if day == today - dt.timedelta(days=1) else day.strftime("Am %d.%m.")
    parts = []
    total, local = requests(day)
    if total:
        parts.append(f"{word} {total} Anfragen, {local} davon selbst erledigt.")
    said = notices(day)
    if said:
        at, _, text = said[-1]
        parts.append(f"Von selbst gemeldet: {len(said)}-mal, zuletzt um {time.strftime('%H:%M', time.localtime(at))} "
                     f"„{_short(text)}“.")
    got = saved(day)
    if got:
        parts.append("Gemerkt: " + "; ".join(_short(t, 60) for t in got[:3]) + ".")
    props = proposed(day)
    if props:
        waiting = [p for p in props if p.get("status") == "open"]
        parts.append(f"{len(props)} Vorschlag" + ("" if len(props) == 1 else "e") + " gemacht"
                     + (f", offen: {_short(waiting[0]['title'], 60)}" if waiting else "") + ".")
    acts = actions(day)
    if acts:
        tools = sorted({a.get("tool", "?") for a in acts})
        parts.append(f"Geändert habe ich {len(acts)}-mal etwas ({', '.join(tools[:4])}).")
    if not parts:
        return f"{word} war es ruhig, Sir — keine Anfragen, keine Meldungen."
    return " ".join(parts)


def handle(n):
    m = _ASK.search(n)
    if not m:
        return None
    day = dt.date.today() - dt.timedelta(days=1 if m.group(1) == "gestern" else 0)
    return report(day)
