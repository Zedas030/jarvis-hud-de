"""
Full-text index over the whole memory (SQLite FTS5) — "Was hab ich dir mal zu … gesagt?" also for old things.

The vault stays the source (Obsidian, Papierkorb, provenance tags); this is only an index next to it.
Before 10.11.2026 memory_search read every note with a regex, and conversations were gone for good
60 days after they happened (diary → Papierkorb after 30, deleted after another 30). Now:

  - every note under vault/ (not the Papierkorb) is one row; changed files are re-read by mtime
  - every exchange of the diary is its own row with its day — and it stays when the diary file is
    deleted, so the index is the long-term memory of what was said (mirrored to the NAS, memory_nas.py)
  - search() ranks with bm25, facts before conversations, recent conversations before old ones

  JARVIS_MEMORY_INDEX   path of the database (logs/memory_index.sqlite)

Work topics never get in: the diary already holds "(Arbeitsthema, nicht protokolliert)" for them, and
privacy.is_work drops anything that slipped through.
"""
import os
import pathlib
import re
import sqlite3
import threading
import time

import memory

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = pathlib.Path(os.environ.get("JARVIS_MEMORY_INDEX", ROOT / "logs" / "memory_index.sqlite"))
REFRESH_S = 60                   # a search re-scans the vault at most this often
_LOCK = threading.Lock()
_LAST = {"scan": 0.0}

_TURN = re.compile(r"\*\*(\d{1,2}:\d{2}) · [^*]+:\*\* (.*?)\n\n\*\*Jarvis:\*\* (.*?)(?=\n\*\*\d{1,2}:\d{2} · |\Z)", re.S)
_DAY = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _db():
    PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(PATH, timeout=10)
    con.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL)")
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS mem USING fts5(title, body, kind UNINDEXED, day UNINDEXED, "
                "ref UNINDEXED, tokenize='unicode61 remove_diacritics 2')")
    return con


def _rel(path):
    try:
        return str(path.relative_to(memory.VAULT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _rows_for(path, text):
    """The index rows of one file: a note is one row, a diary file one row per exchange."""
    import privacy
    rel = _rel(path)
    _, body = memory._parse_frontmatter(text)
    if "Gespraeche" in path.parts:
        m = _DAY.search(path.stem)
        day = m.group(1) if m else ""
        rows = []
        for t, user, reply in _TURN.findall(body):
            user, reply = " ".join(user.split()), " ".join(reply.split())
            if user.startswith("(Arbeitsthema") or privacy.is_work(user, reply):
                continue
            rows.append((f"Gespräch {day} {t}", f"User: {user}\nJarvis: {reply}", "turn", day, rel))
        return rows
    return [(path.stem, body.strip(), "note", "", rel)]


def refresh(force=False):
    """Brings the index up to date with the vault. Returns the number of files re-read."""
    with _LOCK:
        if not force and time.time() - _LAST["scan"] < REFRESH_S:
            return 0
        _LAST["scan"] = time.time()
        con = _db()
        try:
            # the server and every tool process (jarvis_mcp, one per request) refresh the same file:
            # one writer at a time, or two of them insert the same note twice
            con.execute("BEGIN IMMEDIATE")
            known = dict(con.execute("SELECT path, mtime FROM files"))
            present, n = set(), 0
            for path in memory._notes():
                if "Papierkorb" in path.parts:
                    continue
                rel = _rel(path)
                present.add(rel)
                try:
                    mtime = path.stat().st_mtime
                    if known.get(rel) == mtime:
                        continue
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                con.execute("DELETE FROM mem WHERE ref = ?", (rel,))
                con.executemany("INSERT INTO mem (title, body, kind, day, ref) VALUES (?, ?, ?, ?, ?)",
                                _rows_for(path, text))
                con.execute("INSERT OR REPLACE INTO files (path, mtime) VALUES (?, ?)", (rel, mtime))
                n += 1
            # a note that is gone (deleted, moved to the Papierkorb) leaves the index;
            # exchanges stay — they are the only copy once the diary is cleaned up
            for rel in set(known) - present:
                con.execute("DELETE FROM files WHERE path = ?", (rel,))
                con.execute("DELETE FROM mem WHERE ref = ? AND kind = 'note'", (rel,))
            con.commit()
            return n
        finally:
            con.close()


def _match(query):
    """FTS5 query from free words: each word as a prefix, any of them may match (bm25 ranks)."""
    words = [w for w in re.split(r"[\W_]+", (query or "").lower()) if len(w) > 2]
    return " OR ".join(f'"{w}"*' for w in list(dict.fromkeys(words))[:12])


def search(query, limit=8, kind=None):
    """[{title, kind, day, snippet, score}] best first. Empty list when nothing matches or no index."""
    q = _match(query)
    if not q:
        return []
    refresh()
    sql = ("SELECT title, kind, day, snippet(mem, 1, '«', '»', ' … ', 18), bm25(mem, 4.0, 1.0) "
           "FROM mem WHERE mem MATCH ?" + (" AND kind = ?" if kind else "") + " ORDER BY bm25(mem, 4.0, 1.0) LIMIT ?")
    with _LOCK:
        con = _db()
        try:
            rows = con.execute(sql, (q, kind, limit * 4) if kind else (q, limit * 4)).fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            con.close()
    today = time.strftime("%Y-%m-%d")
    out = []
    for title, k, day, snip, rank in rows:
        score = -rank                                   # bm25: smaller is better
        if k == "turn":
            score *= 0.6                                # facts before chat
            age = _days_between(day, today)
            score *= 1.0 / (1.0 + age / 180)            # a recent conversation before an old one
        out.append({"title": title, "kind": k, "day": day, "snippet": " ".join(snip.split()), "score": score})
    out.sort(key=lambda r: -r["score"])
    return out[:limit]


def _days_between(a, b):
    try:
        import datetime as dt
        return max(0, (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days)
    except ValueError:
        return 0


def describe(query, limit=6):
    """Text for the tool memory_search."""
    hits = search(query, limit)
    return "\n".join(f"· {h['title']}: {h['snippet'][:220]}" for h in hits)


def stats():
    with _LOCK:
        con = _db()
        try:
            return dict(con.execute("SELECT kind, count(*) FROM mem GROUP BY kind").fetchall())
        finally:
            con.close()


def start():
    """Keeps the index fresh in the background, so the first search after a quiet hour is fast."""
    import background

    def step():
        try:
            refresh(force=True)
        except sqlite3.Error:
            pass
    background.loop("memory_index", 600, step, pausable=False)
