"""
Shopping list and to-do list — by voice, without the LLM.

    "Setz Milch und Eier auf die Einkaufsliste", "Butter auf die Einkaufsliste"
    "Was steht auf der Einkaufsliste?", "Streich die Milch von der Einkaufsliste"
    "Schreib Steuererklärung auf meine To-do-Liste", "Lösch die Einkaufsliste"

Where a list lives:
  - a Home Assistant to-do entity (JARVIS_SHOPPING_LIST / JARVIS_TODO_LIST, e.g.
    todo.shopping_list) — then it shows up in the HA app on the phone as well;
  - otherwise a checkbox note in the vault (Erinnerungen/Listen/<Name>.md),
    which Obsidian shows as a real checklist.
If the HA entity cannot be reached the note is used, so nothing gets lost.
Removing marks an item done in HA (undo in the app), or deletes the line locally.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

import memory

HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123").strip().rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "").strip()
LISTS = {
    "einkauf": {"title": "Einkaufsliste", "entity": os.environ.get("JARVIS_SHOPPING_LIST", "").strip()},
    "todo": {"title": "To-do-Liste", "entity": os.environ.get("JARVIS_TODO_LIST", "").strip()},
    "druckideen": {"title": "Druckideen", "entity": ""},      # print_ideas.py, always the vault note
}
LIST_DIR = memory.MEMORY_DIR / "Listen"


def which(text):
    t = (text or "").lower()
    if re.search(r"einkauf|shopping", t):
        return "einkauf"
    if re.search(r"to[\s-]*do|aufgaben\s*liste|aufgabenliste|erledigungs?liste|todoliste", t):
        return "todo"
    return None


# ── backends ─────────────────────────────────────────────────
def _ha(path, body):
    req = urllib.request.Request(HA_URL + path, method="POST", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {HA_TOKEN}", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def _use_ha(key):
    return bool(HA_TOKEN and LISTS[key]["entity"])


def _note(key):
    return LIST_DIR / f"{LISTS[key]['title']}.md"


def _read_note(key):
    p = _note(key)
    if not p.exists():
        return []
    return [m.group(1).strip() for m in re.finditer(r"(?m)^- \[ \] (.+)$", p.read_text(encoding="utf-8"))]


def _write_note(key, entries):
    p = _note(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = (f"---\ntype: liste\nwichtigkeit: mittel\nupdated: {time.strftime('%Y-%m-%d')}\n---\n\n"
            f"# {LISTS[key]['title']}\n\nGehört zu [[Profil]]. Jarvis pflegt diese Liste per Sprache.\n\n"
            + "".join(f"- [ ] {e}\n" for e in entries))
    tmp = p.with_suffix(".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, p)


def entries(key):
    if _use_ha(key):
        try:
            res = _ha("/api/services/todo/get_items?return_response",
                      {"entity_id": LISTS[key]["entity"], "status": "needs_action"})
            resp = (res or {}).get("service_response") or {}
            return [i["summary"] for i in (resp.get(LISTS[key]["entity"]) or {}).get("items", [])]
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            pass
    return _read_note(key)


def add(key, items):
    items = [i for i in items if i]
    if not items:
        return []
    have = {e.lower() for e in entries(key)}
    new = [i for i in items if i.lower() not in have]
    if _use_ha(key):
        try:
            for i in new:
                _ha("/api/services/todo/add_item", {"entity_id": LISTS[key]["entity"], "item": i})
            return new
        except (urllib.error.URLError, OSError, ValueError):
            pass
    _write_note(key, _read_note(key) + new)
    return new


def _find(item, current):
    want = item.lower().strip()
    exact = [e for e in current if e.lower() == want]
    if exact:
        return exact[0]
    loose = [e for e in current if want in e.lower() or e.lower() in want]
    return loose[0] if len(loose) == 1 else None


def remove(key, item):
    """Returns the entry that was removed, or None."""
    current = entries(key)
    hit = _find(item, current)
    if not hit:
        return None
    if _use_ha(key):
        try:
            _ha("/api/services/todo/update_item", {"entity_id": LISTS[key]["entity"], "item": hit,
                                                   "status": "completed"})
            return hit
        except (urllib.error.URLError, OSError, ValueError):
            pass
    _write_note(key, [e for e in _read_note(key) if e != hit])
    return hit


def clear(key):
    current = entries(key)
    if _use_ha(key):
        try:
            for e in current:
                _ha("/api/services/todo/update_item", {"entity_id": LISTS[key]["entity"], "item": e,
                                                       "status": "completed"})
            return len(current)
        except (urllib.error.URLError, OSError, ValueError):
            pass
    _write_note(key, [])
    return len(current)


# ── the voice side ───────────────────────────────────────────
_LIST_WORD = (r"(?:einkaufsliste|einkaufszettel|shopping\s*liste|to[\s-]*do[\s-]*liste|todo[\s-]*liste|to[\s-]*dos?|"
              r"aufgabenliste|aufgaben\s*liste|erledigungsliste)")
_ADD = re.compile(r"^(?:\W*(?:hey\s+)?jarvis\W+)?(?:bitte\s+)?(?:(?:setz|schreib|pack|tu|füg|notier|nimm|gib|trag|"
                  r"kommt|muss|müssen|kommen)\w*\s+)?(?:mir\s+|uns\s+)?(?:bitte\s+)?(?:noch\s+)?(?:mal\s+)?"
                  r"(?P<items>.+?)\s*,?\s+(?:mit\s+)?(?:auf|zu[rm]?|in)\s+(?:die|den|das|der|meine[nr]?|unsere[nr]?)?\s*"
                  + _LIST_WORD + r"(?:\s+(?:hinzu|dazu|drauf|rauf|setzen|schreiben|packen))?\W*(?:bitte|sir)?\W*$", re.I)
_REMOVE = re.compile(r"^(?:\W*jarvis\W+)?(?:bitte\s+)?(?:streich\w*|nimm|lösch\w*|entfern\w*|hak\w*|mach)\s+(?:bitte\s+)?"
                     r"(?P<item>.+?)\s+(?:von|aus)\s+(?:der|dem|den|meiner|unserer)?\s*" + _LIST_WORD +
                     r"(?:\s+(?:runter|weg|ab|raus|heraus|gestrichen))?\W*(?:bitte|sir)?\W*$", re.I)
_DONE = re.compile(r"^(?:\W*jarvis\W+)?(?:hak\w*|streich\w*)\s+(?P<item>.+?)\s+ab\W*$", re.I)
_READ = re.compile(r"\b(was\s+(?:steht|ist|hab\w*|hat|muss|müssen|brauch\w*)|zeig|lies|vorlesen|nenn|welche|"
                   r"wie\s+(?:lang|viel)|steht\s+(?:schon|noch)?)\b", re.I)
_CLEAR = re.compile(r"^(?:\W*jarvis\W+)?(?:bitte\s+)?(?:lösch\w*|leer\w*|mach)\s+(?:die|den|meine[nr]?|unsere)?\s*"
                    + _LIST_WORD + r"(?:\s+(?:leer|komplett|ganz))?\W*(?:bitte)?\W*$", re.I)


def split_items(text):
    text = re.sub(r"\s+(?:und|sowie|plus)\s+", ",", text.strip(), flags=re.I)
    out = []
    for part in text.split(","):
        part = re.sub(r"^(?:noch\s+|auch\s+|bitte\s+)+", "", part.strip(" .!?"), flags=re.I)
        part = re.sub(r"^(?:die|der|das|den|ein|eine|einen)\s+", "", part, flags=re.I).strip()
        if part:
            out.append(part[0].upper() + part[1:])
    return out


def say_list(names):
    if not names:
        return ""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " und " + names[-1]


def handle(n, raw=""):
    """Router handler: spoken reply, or None if this is not about a list."""
    raw = (raw or n).strip()
    if not re.search(_LIST_WORD, raw, re.I):
        return None
    key = which(raw)
    if not key:
        return None
    title = LISTS[key]["title"]
    if _CLEAR.match(raw):
        count = clear(key)
        return f"{title} geleert, {count} Einträge abgehakt, Sir." if count else f"Die {title} war schon leer, Sir."
    m = _REMOVE.match(raw) or _DONE.match(raw)
    if m:
        item = split_items(m.group("item"))
        if not item:
            return None
        gone = [g for g in (remove(key, i) for i in item) if g]
        if not gone:
            return f"{say_list(item)} steht nicht auf der {title}, Sir."
        return f"{say_list(gone)} gestrichen, Sir."
    m = _ADD.match(raw)
    if m and not _READ.match(raw):
        what = re.sub(r"\s+(?:muss|müssen|kommt|kommen|soll|sollen)$", "", m.group("items"), flags=re.I)
        item = split_items(what)
        if not item or any(len(i) > 60 for i in item):
            return None
        new = add(key, item)
        if not new:
            return "Steht schon drauf, Sir."
        return f"{say_list(new)} {'steht' if len(new) == 1 else 'stehen'} auf der {title}, Sir."
    if _READ.search(raw) or raw.rstrip().endswith("?"):
        current = entries(key)
        if not current:
            return f"Die {title} ist leer, Sir."
        if len(current) > 12:
            return f"Auf der {title} stehen {len(current)} Dinge, zuerst: {say_list(current[:8])}."
        return f"Auf der {title}: {say_list(current)}."
    return None


# ── for the LLM tools ────────────────────────────────────────
def tool(name, args):
    key = which(args.get("list") or "") or ("todo" if "todo" in (args.get("list") or "").lower() else "einkauf")
    if name == "list_read":
        return "\n".join(entries(key)) or "Liste ist leer."
    if name == "list_add":
        items = args.get("items") or []
        if isinstance(items, str):
            items = split_items(items)
        new = add(key, [str(i).strip()[:80] for i in items])
        return f"Hinzugefügt: {', '.join(new)}" if new else "Stand schon drauf."
    if name == "list_remove":
        hit = remove(key, str(args.get("item") or ""))
        return f"Abgehakt: {hit}" if hit else "Nicht gefunden — erst list_read."
    return "unbekannt"
