"""
The mail accounts Jarvis may read — one place for every module that opens a mailbox
(mail_inbox, dashboard, briefing, parcels, shipments, mail_events, drafts).

  .env, first account (as before):  MAIL_IMAP_HOST, MAIL_USER, MAIL_PASSWORD, optional MAIL_NAME
  more accounts:                     MAIL2_IMAP_HOST, MAIL2_USER, MAIL2_PASSWORD, MAIL2_NAME,
                                     MAIL3_... (up to MAIL9_); optional MAILn_IMAP_PORT (993)

  accounts()       -> [{key, name, host, port, user, password}]   only complete ones
  connect(acc)     -> logged-in IMAP4_SSL with INBOX selected read-only (nothing gets marked)
  each(fn)         -> fn(acc, box) for every account; one failing account does not stop
                      the others — its error comes back with the results
  recent(box)      -> ids of the mails from the last LOOKBACK_H hours (JARVIS_MAIL_LOOKBACK_HOURS,
                      default 24) — every module that looks back over a period uses this; it is
                      the upper limit, a caller may only ask for less. Only mail_inbox, when the
                      user names a period himself, passes asked=True and may go back READ_DAYS
                      (JARVIS_MAIL_READ_DAYS, default 90)
  search(box, q)   -> one targeted lookup ("did Stadtwerke ever write?") over the last SEARCH_DAYS days
                      (JARVIS_MAIL_SEARCH_DAYS, default 365) in the inbox and the user's own folders
                      (not Sent, Drafts, Trash, Spam). Only the headers (date, sender, subject) are
                      read, never the body — the long window is for asking, not for background scans

Read-only by default. Only mail_draft opens a box writable (APPEND to Drafts) and
mail_mark_read sets flags; both pass readonly=False themselves.
"""
import imaplib
import os
import time

# The user's choice: background readers never look further back in the mail than this.
LOOKBACK_H = max(1, int(os.environ.get("JARVIS_MAIL_LOOKBACK_HOURS", "24")))
# When the user asks for it (mail_inbox `days`, e.g. "lies die Mails der letzten drei Tage"), Jarvis
# may read this far back — chosen by the user on 19.11.2026 after a day away from home.
READ_DAYS = max(1, int(os.environ.get("JARVIS_MAIL_READ_DAYS", "90")))
# A search the user asks for may look further back, but only reads headers.
SEARCH_DAYS = max(1, int(os.environ.get("JARVIS_MAIL_SEARCH_DAYS", "365")))
# Folders a search skips: what the user sent or threw away, and spam.
_SKIP_FLAGS = ("\\sent", "\\drafts", "\\trash", "\\junk", "\\noselect")
_SKIP_NAMES = {"postausgang", "outbox", "conversation history", "entwurf", "entwürfe", "gesendet", "papierkorb", "spam"}


def _env(prefix, name, default=""):
    return os.environ.get(f"{prefix}{name}", default).strip()


def accounts():
    out = []
    for n in range(1, 10):
        prefix = "MAIL_" if n == 1 else f"MAIL{n}_"
        host, user, pw = _env(prefix, "IMAP_HOST"), _env(prefix, "USER"), _env(prefix, "PASSWORD")
        if not (host and user and pw):
            continue
        port = int(_env(prefix, "IMAP_PORT", "993") or 993)
        out.append({"key": str(n), "name": _env(prefix, "NAME") or user, "host": host, "port": port,
                    "user": user, "password": pw})
    return out


def configured():
    return bool(accounts())


def connect(acc, readonly=True, timeout=30):
    box = imaplib.IMAP4_SSL(acc["host"], acc["port"], timeout=timeout)
    try:
        box.login(acc["user"], acc["password"])
        box.select("INBOX", readonly=readonly)
    except Exception:
        try:
            box.logout()
        except Exception:  # noqa: BLE001
            pass
        raise
    return box


def each(fn, readonly=True, timeout=30):
    """-> (results, errors): results = [fn(acc, box) per account], errors = ["name: reason"]."""
    results, errors = [], []
    for acc in accounts():
        try:
            box = connect(acc, readonly=readonly, timeout=timeout)
        except Exception as e:  # noqa: BLE001 — wrong password in one account, the others still count
            errors.append(f"{acc['name']}: {str(e)[:80]}")
            continue
        try:
            results.append(fn(acc, box))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{acc['name']}: {str(e)[:80]}")
        finally:
            try:
                box.logout()
            except Exception:  # noqa: BLE001
                pass
    return results, errors


def recent(box, hours=None, criteria=(), asked=False):
    """Ids of the mails received in the last `hours` (default and maximum LOOKBACK_H; with
    `asked` — the user asked for a longer period himself — at most READ_DAYS), oldest first;
    `criteria` narrow the search further ("UNSEEN"). IMAP SINCE only knows days, so the
    server narrows by date and the arrival time (INTERNALDATE) does the hours."""
    hours = min(float(hours or LOOKBACK_H), READ_DAYS * 24 if asked else LOOKBACK_H)
    cutoff = time.time() - hours * 3600
    _, data = box.search(None, *criteria, "SINCE", time.strftime("%d-%b-%Y", time.localtime(cutoff)))
    ids = data[0].split() if data and data[0] else []
    if not ids:
        return []
    _, rows = box.fetch(b",".join(ids), "(INTERNALDATE)")
    keep = []
    for row in rows or []:
        raw = row[0] if isinstance(row, tuple) else row
        if not isinstance(raw, bytes):
            continue
        when = imaplib.Internaldate2tuple(raw)
        if when and time.mktime(when) >= cutoff:
            keep.append(raw.split()[0])
    return keep


def folders(box):
    """Names of the folders a search looks in: INBOX first, then the user's own ones."""
    import re
    out = []
    _, rows = box.list()
    for row in rows or []:
        if not isinstance(row, bytes):
            continue
        m = re.match(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$', row)
        if not m:
            continue
        flags = m["flags"].decode(errors="replace").lower()
        name = m["name"].decode(errors="replace").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"')
        if any(f in flags for f in _SKIP_FLAGS) or name.lower() in _SKIP_NAMES:
            continue
        out.append(name)
    out.sort(key=lambda n: n.upper() != "INBOX")
    return out or ["INBOX"]


def search(box, query, days=None, in_text=False):
    """[{folder, id, date, from, subject}] of the mails whose sender or subject (`in_text`: also the
    body, searched by the server) contain `query`, received in the last `days` (at most SEARCH_DAYS),
    over all folders(box). Read-only, headers only; the INBOX selection is restored at the end."""
    import email
    import email.header
    import email.utils
    days = min(float(days or SEARCH_DAYS), SEARCH_DAYS)
    since = time.strftime("%d-%b-%Y", time.localtime(time.time() - days * 86400))
    q = " ".join((query or "").split())
    if not q:
        return []

    def dec(v):
        try:
            return str(email.header.make_header(email.header.decode_header(v or ""))).strip()
        except Exception:  # noqa: BLE001 — a broken header must not stop the search
            return (v or "").strip()

    out = []
    try:
        for folder in folders(box):
            typ, _ = box.select('"' + folder.replace('"', '\\"') + '"', readonly=True)
            if typ != "OK":
                continue
            if q.isascii():
                quoted = '"' + q.replace("\\", "").replace('"', "") + '"'
                crit = ("SINCE", since, "TEXT", quoted) if in_text else \
                       ("SINCE", since, "OR", "FROM", quoted, "SUBJECT", quoted)
                _, data = box.search(None, *crit)
            else:   # umlauts: a UTF-8 literal, which IMAP only allows as the last argument
                found = set()
                for key in (("TEXT",) if in_text else ("FROM", "SUBJECT")):
                    box.literal = q.encode("utf-8")
                    _, d = box.search("UTF-8", "SINCE", since, key)
                    found.update(d[0].split() if d and d[0] else [])
                data = [b" ".join(sorted(found, key=int))]
            ids = data[0].split() if data and data[0] else []
            if not ids:
                continue
            _, rows = box.fetch(b",".join(ids[-200:]), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            for row in rows or []:
                if not isinstance(row, tuple):
                    continue
                msg = email.message_from_bytes(row[1])
                try:
                    sent = email.utils.parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
                except Exception:  # noqa: BLE001
                    sent = None
                out.append({"folder": folder, "id": row[0].split()[0], "date": sent,
                            "from": dec(msg.get("From")), "subject": dec(msg.get("Subject"))})
    finally:
        try:
            box.select("INBOX", readonly=True)
        except Exception:  # noqa: BLE001
            pass
    return out


def find(name_or_user):
    """The account a user names ("privat", "gmail", the address), else the first one."""
    want = (name_or_user or "").strip().lower()
    accs = accounts()
    for a in accs:
        if want and (want == a["key"] or want in a["name"].lower() or want in a["user"].lower()):
            return a
    return accs[0] if accs else None
