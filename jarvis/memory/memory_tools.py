"""
Memory you can control: where a fact came from, why it is there, correct it,
forget it, see everything, export it.

Provenance: memory_save adds a short tag to each new bullet,
  "- Mag kurze Antworten _(gesagt, 22.10.2026)_"      the user said it
  "- Spielt wohl sonntags Golf _(abgeleitet, 22.10.2026)_"  Jarvis concluded it
Derived facts are treated as assumptions: the prompt shows them marked.

  overview(note)   "Zeig mir, was du über mich gespeichert hast"
  why(text)        "Warum hast du das gespeichert?" — tag plus the conversation of that day
  forget(...)      "Vergiss das" — the bullet goes to Papierkorb/Vergessen (card first)
  export()/backup  the whole vault as a zip in backups/ (daily backup keeps JARVIS_BACKUP_KEEP)

Correcting is memory_save with `replace` (existing tool).
"""
import datetime as dt
import os
import pathlib
import re
import time
import zipfile

import memory

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
BACKUP_DIR = pathlib.Path(os.environ.get("JARVIS_BACKUP_DIR", ROOT / "backups"))
BACKUP_KEEP = int(os.environ.get("JARVIS_BACKUP_KEEP", "14"))
TAG = re.compile(r"\s*_\((gesagt|abgeleitet|vermutet), (\d{1,2}\.\d{1,2}\.\d{4})(?:, bis (\d{1,2}\.\d{1,2}\.\d{4}))?\)_\s*$")
FORGOTTEN = "Vergessen"


def tag(source="abgeleitet", day=None, until=None):
    s = str(source or "").lower()
    source = "gesagt" if s.startswith("ges") else "vermutet" if s.startswith("verm") else "abgeleitet"
    end = ""
    if until:
        try:
            end = ", bis " + dt.date.fromisoformat(str(until)[:10]).strftime("%d.%m.%Y")
        except ValueError:
            end = ""
    return f" _({source}, {(day or dt.date.today()).strftime('%d.%m.%Y')}{end})_"


def expiry(bullet):
    """date or None from a bullet's tag."""
    m = TAG.search(bullet)
    if not (m and m.group(3)):
        return None
    return dt.datetime.strptime(m.group(3), "%d.%m.%Y").date()


def expire_bullets(today=None):
    """Moves bullets whose 'bis' date has passed to the trash note. Returns how many."""
    today = today or dt.date.today()
    moved = 0
    for path in _facts():
        gone = [b for b in _bullets(path) if (expiry(b) or today) < today]
        for b in gone:
            forget(split_tag(b)[0], _title(path))
            moved += 1
    return moved


def split_tag(bullet):
    """('text', 'gesagt'|'abgeleitet'|None, 'dd.mm.yyyy'|None)"""
    m = TAG.search(bullet)
    if not m:
        return bullet.strip(), None, None
    return bullet[:m.start()].strip(), m.group(1), m.group(2)


def for_prompt(bullet):
    """What the system prompt sees: no dates, derived facts marked as such."""
    text, source, _ = split_tag(bullet)
    return text + {"abgeleitet": " (abgeleitet, unsicher)", "vermutet": " (Vermutung)"}.get(source, "")


def _bullets(path):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [ln[2:].strip() for ln in lines if ln.startswith("- ")]


def _facts():
    """Notes with lasting facts (no diaries, no trash)."""
    return [p for p in memory.MEMORY_DIR.rglob("*.md")] if memory.MEMORY_DIR.exists() else []


def _title(path):
    return path.relative_to(memory.MEMORY_DIR).with_suffix("").as_posix()


def overview(note=""):
    note = (note or "").strip()
    if note:
        path = memory.note_path(note)
        if not path.exists():
            return f"Keine Notiz '{note}'."
        rows = []
        for b in _bullets(path):
            text, source, day = split_tag(b)
            rows.append(f"- {text}" + (f" [{source}, {day}]" if source else ""))
        return f"{note}:\n" + ("\n".join(rows) or "(keine Einträge)")
    rows = []
    for path in sorted(_facts()):
        bullets = _bullets(path)
        if bullets:
            derived = sum(1 for b in bullets if split_tag(b)[1] == "abgeleitet")
            rows.append(f"- {_title(path)}: {len(bullets)} Einträge" + (f", davon {derived} abgeleitet" if derived else ""))
    return "Gespeichert über Sie:\n" + "\n".join(rows) if rows else "Noch nichts gespeichert."


def find(text):
    """[(path, bullet)] of fact bullets containing `text` (case-insensitive)."""
    want = " ".join((text or "").lower().split())
    if len(want) < 3:
        return []
    out = []
    for path in _facts():
        for b in _bullets(path):
            # saves get [[links]] added (memory_intel.link_entities); "Anna hat …" must still find "[[Anna]] hat …"
            plain = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", b)
            if want in " ".join(b.lower().split()) or want in " ".join(plain.lower().split()):
                out.append((path, b))
    return out


def why(text):
    hits = find(text)
    if not hits:
        return "Dazu habe ich nichts gespeichert."
    if len(hits) > 3:
        return f"{len(hits)} Einträge passen — bitte genauer."
    out = []
    for path, b in hits:
        fact, source, day = split_tag(b)
        line = f"„{fact}“ steht in {_title(path)}"
        if not source:
            out.append(line + " (älterer Eintrag ohne Herkunftsangabe).")
            continue
        line += f" — {'Sie haben es gesagt' if source == 'gesagt' else 'von mir abgeleitet'}, am {day}."
        d = dt.datetime.strptime(day, "%d.%m.%Y").date().isoformat()
        diary = memory.DIARY_DIR / f"Gespräch {d}.md"
        words = [w for w in re.split(r"\W+", fact.lower()) if len(w) > 3][:4]
        if diary.exists() and words:
            hits_d = [ln.strip() for ln in diary.read_text(encoding="utf-8", errors="replace").splitlines()
                      if ln.startswith("**") and sum(w in ln.lower() for w in words) >= min(2, len(words))]
            if hits_d:
                line += " Aus dem Gespräch: " + " / ".join(h[:160] for h in hits_d[:2])
        out.append(line)
    return "\n".join(out)


def forget(text, note=""):
    """Moves matching bullets to the trash note. Refuses if the match is ambiguous."""
    hits = find(text)
    if note:
        hits = [(p, b) for p, b in hits if _title(p).lower() == note.strip().lower()]
    if not hits:
        return "Dazu habe ich nichts gespeichert."
    if len({b for _, b in hits}) > 1:
        return ("Mehrere Einträge passen, ich vergesse nichts auf Verdacht:\n" +
                "\n".join(f"- {_title(p)}: {split_tag(b)[0]}" for p, b in hits[:6]))
    memory.TRASH_DIR.mkdir(parents=True, exist_ok=True)
    trash = memory.TRASH_DIR / f"{FORGOTTEN}.md"
    if not trash.exists():
        trash.write_text("---\ntype: papierkorb\n---\n\n# Vergessen\n\nAuf Wunsch vergessene Einträge "
                         "(werden nach 30 Tagen mit dem Papierkorb gelöscht).\n", encoding="utf-8")
    today = time.strftime("%Y-%m-%d")
    with trash.open("a", encoding="utf-8") as fh:
        for path, b in hits:
            fh.write(f"- {today} aus {_title(path)}: {b}\n")
    for path, b in hits:
        body = path.read_text(encoding="utf-8")
        body = body.replace(f"- {b}\n", "", 1) if f"- {b}\n" in body else body.replace(f"- {b}", "", 1)
        body = re.sub(r"(?m)^updated: .*$", f"updated: {today}", body, count=1)
        path.write_text(body, encoding="utf-8")
    return f"Vergessen: „{split_tag(hits[0][1])[0]}“ (aus {_title(hits[0][0])})."


def export(prefix="vault-export"):
    """Zips the vault into backups/. Returns the zip path."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"{prefix}-{time.strftime('%Y-%m-%d_%H%M%S')}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for path in memory.VAULT.rglob("*"):
            if path.is_file() and ".obsidian" not in path.parts:
                z.write(path, path.relative_to(memory.VAULT).as_posix())
    return target


def backup():
    """Daily backup; keeps the newest BACKUP_KEEP. Returns the new zip or None."""
    target = export("vault-backup")
    old = sorted(BACKUP_DIR.glob("vault-backup-*.zip"))[:-BACKUP_KEEP]
    for p in old:
        p.unlink()
    return target


def start():
    import background
    import audit

    def step():
        today = time.strftime("%Y-%m-%d")
        if background.claim("vault-backup", today):
            backup()
            audit.trim()
            expire_bullets()
    background.loop("vault_backup", 3600, step, pausable=False)
