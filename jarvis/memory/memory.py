"""
Obsidian-style memory vault -> knowledge graph.

Every .md file under vault/ is one node. Frontmatter gives it a type; wiki
links ([[Another Note]]) become edges. Nothing here is a database: the memory
IS a folder of markdown you can open in Obsidian, edit by hand, or let Claude
write to. That is the whole point — the graph on screen is the real filesystem,
not a decorative animation.
"""
import os
import pathlib
import re
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
VAULT = pathlib.Path(os.path.expanduser(os.environ.get("JARVIS_VAULT", ROOT / "vault")))

WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)

# Palette mirrors the filter legend: one colour per node type.
TYPE_COLOURS = {
    "call":     "#4c9bff",
    "note":     "#e9eff7",
    "concept":  "#ffb340",
    "project":  "#5f7cff",
    "person":   "#b98cff",
    "client":   "#2ed99b",
    "invoice":  "#ff6ba6",
    "proposal": "#ffa033",
    "sop":      "#ff8c42",
    "brief":    "#cbd6e4",
    "campaign": "#dfe8f3",
    "erinnerung": "#5fe4ff",
    "gespraech": "#7d8da3",
    "profil":   "#ffd84d",
    "website":  "#4c9bff",
    "befehl":   "#ff8c42",
    "vorliebe": "#ff6ba6",
    "termin":   "#2ed99b",
    "projekt":  "#5f7cff",
    "wissen":   "#cbd6e4",
    "golfrunde": "#7bd88f",
}

# Long-term memory JARVIS writes itself (one note per fact/topic), and the
# conversation diary the server appends to after every turn.
MEMORY_DIR = VAULT / "Erinnerungen"
DIARY_DIR = VAULT / "Gespraeche"
TRASH_DIR = VAULT / "Papierkorb"
DIARY_KEEP_DAYS = int(os.environ.get("JARVIS_DIARY_DAYS", "30"))   # diary -> trash after
TRASH_KEEP_DAYS = int(os.environ.get("JARVIS_TRASH_DAYS", "30"))   # trash -> deleted after


def _notes():
    """All live notes — the trash is not part of memory."""
    if not VAULT.exists():
        return []
    return [p for p in VAULT.rglob("*.md") if TRASH_DIR not in p.parents]


def note_path(name):
    """The note `name` ("Vorlieben", "Projekte/Umzug") under MEMORY_DIR. An existing one is found
    whatever its case: Windows does not tell "vorlieben" from "Vorlieben", Linux would start a
    second note."""
    path = MEMORY_DIR / f"{name}.md"
    if path.exists() or not MEMORY_DIR.exists():
        return path
    want = path.relative_to(MEMORY_DIR).as_posix().lower()
    return next((p for p in MEMORY_DIR.rglob("*.md") if p.relative_to(MEMORY_DIR).as_posix().lower() == want), path)
DEFAULT_COLOUR = "#8fa3bf"

_CACHE = {"at": 0.0, "data": None, "sig": None}
_LOCK = threading.Lock()
CACHE_TTL = 2.0


def _parse_frontmatter(text):
    meta, body = {}, text
    m = FRONTMATTER.match(text)
    if m:
        body = text[m.end():]
        for line in m.group(1).splitlines():
            if ":" in line and not line.strip().startswith("#"):
                k, _, v = line.partition(":")
                meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return meta, body


def _signature():
    """Cheap change detector so the graph reloads when you edit the vault."""
    if not VAULT.exists():
        return ()
    return tuple(sorted(
        (str(p), int(p.stat().st_mtime))
        for p in _notes()
    ))


def build_graph(force=False):
    with _LOCK:
        now = time.time()
        if not force and _CACHE["data"] and now - _CACHE["at"] < CACHE_TTL:
            return _CACHE["data"]
        sig = _signature()
        if not force and _CACHE["data"] and sig == _CACHE["sig"]:
            _CACHE["at"] = now
            return _CACHE["data"]

        nodes, by_title, edges = {}, {}, []
        if VAULT.exists():
            for path in sorted(_notes()):
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                meta, body = _parse_frontmatter(text)
                title = meta.get("title") or path.stem
                ntype = (meta.get("type") or "note").strip().lower()
                key = title.lower()
                snippet = " ".join(
                    ln.strip() for ln in body.splitlines()
                    if ln.strip() and not ln.strip().startswith("#")
                )[:340]
                nodes[key] = dict(
                    id=key, title=title, type=ntype,
                    colour=TYPE_COLOURS.get(ntype, DEFAULT_COLOUR),
                    file=str(path.relative_to(VAULT)),
                    tags=[t.strip() for t in (meta.get("tags") or "").split(",") if t.strip()],
                    updated=meta.get("updated", ""),
                    snippet=snippet, degree=0,
                    # Home-Assistant-Entitäten für lebendige Sterne (siehe briefing.live)
                    online=meta.get("online", ""), pegel=meta.get("pegel", ""),
                    groesse=meta.get("groesse", ""),   # extra radius for a hand-picked star
                )
                by_title[key] = key
                for target in WIKILINK.findall(body):
                    edges.append((key, target.strip().lower()))

        # Keep only edges whose target actually exists; count degree both ways.
        clean, seen = [], set()
        for src, dst in edges:
            if dst not in by_title or src == dst:
                continue
            pair = tuple(sorted((src, dst)))
            if pair in seen:
                continue
            seen.add(pair)
            clean.append(dict(source=src, target=dst))
            nodes[src]["degree"] += 1
            nodes[dst]["degree"] += 1

        counts = {}
        for n in nodes.values():
            counts[n["type"]] = counts.get(n["type"], 0) + 1

        hubs = sorted(nodes.values(), key=lambda n: (-n["degree"], n["title"]))[:8]
        data = dict(
            nodes=list(nodes.values()),
            links=clean,
            counts=sorted(counts.items(), key=lambda kv: -kv[1]),
            hubs=[dict(id=h["id"], title=h["title"], degree=h["degree"],
                       colour=h["colour"], type=h["type"]) for h in hubs],
            vault=str(VAULT),
            total=len(nodes),
        )
        _CACHE.update(at=now, data=data, sig=sig)
        return data


def search(query, limit=6):
    """Plain substring recall over the vault, for the /recall command."""
    q = (query or "").strip().lower()
    if not q:
        return []
    hits = []
    for node in build_graph()["nodes"]:
        haystack = (node["title"] + " " + node["snippet"]).lower()
        if q in haystack:
            hits.append(node)
    hits.sort(key=lambda n: (-n["degree"], n["title"]))
    return hits[:limit]


def _scored(message):
    words = [w for w in re.split(r"[\W_]+", (message or "").lower()) if len(w) > 3]
    if not words:
        return []
    out = []
    for node in build_graph()["nodes"]:
        haystack = (node["title"] + " " + node["snippet"]).lower()
        score = sum(2 if w in node["title"].lower() else 1 for w in words if w in haystack)
        if score:
            out.append((score + node["degree"] * 0.1, node))
    out.sort(key=lambda pair: -pair[0])
    return out


def top_match(message):
    """The vault note a question most likely refers to, for graph focus."""
    hits = _scored(message)
    return hits[0][1]["id"] if hits else None


def context_for(message, limit=4):
    """Pull the most relevant vault notes so Claude answers from real memory."""
    scored = _scored(message)
    if not scored:
        return ""
    out = ["## Relevant memory from the JARVIS vault",
           "These notes come from the user's own markdown vault. Treat them as fact."]
    for _, node in scored[:limit]:
        out.append(f"### {node['title']} ({node['type']})\n{node['snippet']}")
    return "\n\n".join(out)


# ── long-term memory + conversation diary ──────────────────────
def _strip_tags(text):
    return " ".join(re.sub(r"\[[^\][]{1,90}\]", " ", text or "").split())


# Device notes and single golf rounds are many and small; they are looked up
# on demand instead of riding along in every system prompt.
ON_DEMAND_DIRS = {"netzwerk-geräte", "golfrunden"}


def core_memory(limit_chars=6000):
    """Every note in Erinnerungen/ (the profile first), in full. These are small,
    curated facts, so JARVIS gets all of them on every turn instead of a
    keyword-matched excerpt. Whatever does not fit is listed by name only."""
    if not MEMORY_DIR.exists():
        return ""
    rank = {"hoch": 0, "mittel": 1, "niedrig": 2}
    entries, later = [], []
    for path in MEMORY_DIR.rglob("*.md"):
        if path.parent.name.lower() in ON_DEMAND_DIRS:
            later.append(path)
            continue
        try:
            meta, body = _parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        entries.append((path.stem.lower() != "profil",
                        rank.get(meta.get("wichtigkeit", "mittel").lower(), 1),
                        -path.stat().st_mtime, path, body))
    out, used = [], 0
    for *_, path, body in sorted(entries, key=lambda e: e[:3]):
        body = body.strip()
        chunk = f"### {path.stem}\n{body}"
        if used + len(chunk) > limit_chars:
            later.append(path)
            continue
        out.append(chunk)
        used += len(chunk)
    if later:
        names = ", ".join(sorted(str(p.relative_to(MEMORY_DIR).with_suffix("")) for p in later))
        out.append(f"### Weitere Notizen (bei Bedarf mit Read aus {MEMORY_DIR} lesen)\n{names}")
    return "\n\n".join(out)


def log_turn(user_text, reply_text):
    """Append one exchange to today's diary note. Written by the server, not by
    Claude, so nothing said is ever lost even if Claude forgets to save it."""
    reply = _strip_tags(reply_text)
    if not (user_text or "").strip() or not reply:
        return
    if reply.strip(" .!").upper() == "IGNORIEREN":   # TV/background a satellite heard (remote_ask.IGNORE)
        return
    import privacy
    if privacy.is_work(user_text, reply):          # work stays out of the vault
        user_text, reply = "(Arbeitsthema, nicht protokolliert)", "(nicht protokolliert)"
    DIARY_DIR.mkdir(parents=True, exist_ok=True)
    day = time.strftime("%Y-%m-%d")
    path = DIARY_DIR / f"Gespräch {day}.md"
    new = not path.exists()
    with path.open("a", encoding="utf-8") as fh:
        if new:
            fh.write(f"---\ntype: gespraech\nupdated: {day}\n---\n\n# Gespräch {day}\n\n"
                     "Automatisches Protokoll. Verknüpft mit [[Profil]].\n")
        fh.write(f"\n**{time.strftime('%H:%M')} · {_user_label()}:** {user_text.strip()}\n\n"
                 f"**Jarvis:** {reply}\n")


def recent_turns(max_chars=1200):
    """Tail of the newest diary notes, for continuity when a fresh Claude session starts."""
    if not DIARY_DIR.exists():
        return ""
    text = ""
    for path in sorted(DIARY_DIR.glob("*.md"), reverse=True)[:2]:
        try:
            _, body = _parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        text = f"## {path.stem}\n{body.strip()}\n\n" + text
        if len(text) > max_chars:
            break
    return text[-max_chars:]


def housekeeping():
    """Forget on purpose. Expired memories (frontmatter `ablauf:` in the past) and
    diary days older than DIARY_KEEP_DAYS move to the trash; trash older than
    TRASH_KEEP_DAYS is deleted. Durable facts live in Erinnerungen/ and stay."""
    import shutil
    today = time.strftime("%Y-%m-%d")
    moved, deleted = [], 0

    def to_trash(path):
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        target = TRASH_DIR / path.name
        if target.exists():
            target = TRASH_DIR / f"{path.stem} ({int(time.time())}){path.suffix}"
        shutil.move(str(path), str(target))
        os.utime(target)          # trash age counts from the move
        moved.append(path.stem)

    if MEMORY_DIR.exists():
        for path in list(MEMORY_DIR.rglob("*.md")):
            try:
                meta, _ = _parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            ablauf = (meta.get("ablauf") or "").strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ablauf) and ablauf < today:
                to_trash(path)
    if DIARY_DIR.exists():
        cutoff = time.time() - DIARY_KEEP_DAYS * 86400
        for path in list(DIARY_DIR.glob("*.md")):
            if path.stat().st_mtime < cutoff:
                to_trash(path)
    if TRASH_DIR.exists():
        cutoff = time.time() - TRASH_KEEP_DAYS * 86400
        for path in list(TRASH_DIR.glob("*")):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                deleted += 1
    return moved, deleted


def _user_label():
    import jarvis_profile as profile
    return profile.USER_LABEL
