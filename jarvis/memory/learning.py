"""
The router learns from the LLM — fewer tokens every week.

1. Aliases: when a switching request had to go to Claude ("Mach die Leselampe
   im Büro aus") and Claude switched exactly the device(s) with home_control,
   the wording is stored as a draft (config/learned.json → "drafts"). Only after
   it recurred and the user accepted it on a card (skill_factory.py) does the
   router do it alone, in milliseconds, for 0 tokens.

2. Report: logs/usage.jsonl tells which requests went to the LLM how often and
   at what cost. "Verbrauchsbericht" / "Was hast du gelernt?" answers locally;
   scripts/router_report.py prints the full list for tuning the rules.
"""
import collections
import json
import os
import pathlib
import re
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
PATH = pathlib.Path(os.environ.get("JARVIS_LEARNED", ROOT / "config" / "learned.json"))
USAGE = ROOT / "logs" / "usage.jsonl"
_CACHE = {"mtime": None, "data": {}}
_SWITCH_WORDS = {"an", "aus", "ein", "einschalten", "ausschalten", "anmachen", "ausmachen", "anschalten",
                 "abschalten", "schalte", "schalt", "mach", "mache", "bitte", "mal", "jarvis", "sir", "der", "die",
                 "das", "den", "dem", "im", "in", "am", "kannst", "du", "und", "noch", "jetzt", "doch", "auch"}


def key(n):
    """The device part of a command: 'mach bitte die Leselampe im Büro aus' -> 'büro leselampe'."""
    words = [w for w in re.sub(r"[^\wäöüß]+", " ", (n or "").lower()).split() if w not in _SWITCH_WORDS]
    return " ".join(sorted(set(words)))


def load():
    try:
        mtime = PATH.stat().st_mtime
    except OSError:
        return {}
    if _CACHE["mtime"] != mtime:
        try:
            _CACHE.update(mtime=mtime, data=json.loads(PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            _CACHE.update(mtime=mtime, data={})
    return _CACHE["data"]


def _save(data):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, PATH)
    _CACHE["mtime"] = None


def alias(n):
    """Entity ids learned for this wording, or []."""
    k = key(n)
    return list((load().get("aliases") or {}).get(k, {}).get("entities", [])) if k else []


def observe(message, tool_calls):
    """After an LLM turn: learn the wording if it was a plain switch of known devices.
    tool_calls: [(tool_name, args_dict)] in order."""
    calls = [(n.rsplit("__", 1)[-1], a or {}) for n, a in tool_calls]
    if not calls or any(n not in ("home_control", "home_states", "memory_search", "memory_read") for n, _ in calls):
        return None
    switches = [a for n, a in calls if n == "home_control"]
    services = {a.get("service") for a in switches}
    if not switches or len(services) != 1 or services - {"turn_on", "turn_off", "toggle"}:
        return None
    if any(a.get("data") for a in switches):
        return None                       # brightness etc.: the wording does not carry the value reliably
    n = (message or "").lower()
    if re.search(r"\?|\b(wenn|falls|später|um \d|in \d|warum|ob)\b", n) or len(n) > 90:
        return None
    k = key(n)
    if not k or len(k.split()) > 5:
        return None
    entities = sorted({a.get("entity_id") for a in switches if a.get("entity_id")})
    if not entities or len(entities) > 4:
        return None
    data = dict(load())
    active = (data.get("aliases") or {}).get(k)
    if active and sorted(active.get("entities", [])) == entities:
        return None                                     # already a live rule
    drafts = dict(data.get("drafts") or {})
    old = drafts.get(k) or {}
    same = sorted(old.get("entities", [])) == entities
    drafts[k] = {"entities": entities, "first": old.get("first") if same else time.strftime("%Y-%m-%d"),
                 "example": message[:80], "seen": (old.get("seen", 0) + 1) if same else 1,
                 "proposed": old.get("proposed") if same else None}
    data["drafts"] = drafts
    _save(data)
    try:
        import skill_factory
        skill_factory.draft_seen(k)
    except Exception:  # noqa: BLE001 — a proposal is a bonus, the draft is kept anyway
        pass
    return k


def choice(n):
    """Entities the user picked once when this wording was ambiguous ("die Lampe"), or []."""
    k = key(n)
    return list((load().get("choices") or {}).get(k, {}).get("entities", [])) if k else []


def remember_choice(n, entities):
    """The user's answer to "Meinen Sie A oder B?" — kept for the wording, no card needed: he said it."""
    k = key(n)
    if not k or not entities:
        return False
    data = dict(load())
    choices = dict(data.get("choices") or {})
    choices[k] = {"entities": sorted(entities), "example": (n or "").strip()[:80], "at": time.strftime("%Y-%m-%d")}
    data["choices"] = choices
    _save(data)
    return True


def forget(k):
    data = dict(load())
    al = dict(data.get("aliases") or {})
    if al.pop(k, None) is None:
        return False
    data["aliases"] = al
    _save(data)
    return True


# ── usage report ─────────────────────────────────────────────
def rows(days=7):
    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - days * 86400))
    out = []
    try:
        with USAGE.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("at", "") >= cutoff:
                    out.append(r)
    except OSError:
        pass
    return out


def report(days=7):
    rs = rows(days)
    if not rs:
        return {"total": 0}
    local = [r for r in rs if str(r.get("route", "")).startswith(("local", "cache", "briefing-local"))]
    llm = [r for r in rs if r not in local]
    by_msg = collections.defaultdict(lambda: [0, 0])
    for r in llm:
        m = " ".join(re.sub(r"[^\wäöüß ]+", " ", (r.get("message") or "").lower()).split())[:60]
        by_msg[m][0] += 1
        by_msg[m][1] += r.get("total_in", 0) + r.get("output", 0)
    repeat = sorted(((m, c, t) for m, (c, t) in by_msg.items() if c >= 2), key=lambda x: -x[2])[:10]
    return {"total": len(rs), "local": len(local), "llm": len(llm),
            "tokens": sum(r.get("total_in", 0) + r.get("output", 0) for r in llm),
            "repeat": repeat, "aliases": len((load().get("aliases") or {})), "drafts": len(load().get("drafts") or {})}


def spoken_report(days=7):
    r = report(days)
    if not r.get("total"):
        return "Dazu habe ich noch keine Zahlen, Sir."
    share = round(100 * r["local"] / r["total"])
    text = (f"In den letzten {days} Tagen {r['total']} Anfragen, {share} Prozent davon habe ich selbst erledigt. "
            f"Ans Sprachmodell gingen {r['llm']}, zusammen etwa {round(r['tokens'] / 1000)} tausend Tokens. "
            f"Ich kenne {r['aliases']} gelernte Formulierungen"
            + (f", {r['drafts']} weitere warten als Entwurf auf Ihre Freigabe." if r.get("drafts") else "."))
    if r["repeat"]:
        m, c, _ = r["repeat"][0]
        text += f" Am teuersten wiederholt: „{m}“, {c} mal."
    try:
        import token_budget
        budget = token_budget.spoken()
        if budget:
            text += " " + budget
    except Exception:  # noqa: BLE001 — the report must not fail on the budget
        pass
    try:
        import selfcheck
        n = len(selfcheck.misses(days))
        if n:
            text += f" {n} Mal mussten Sie gleich nochmal fragen — Details mit „Wie gut warst du?“."
    except Exception:  # noqa: BLE001
        pass
    return text
