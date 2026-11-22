"""
mail_inbox looks further back when asked, and "mark all as read" names the important ones first — no network.

    .venv\\Scripts\\python.exe -m unittest tests.test_mail_review -v
"""
import isolate  # noqa: F401  (must come first)
import email.utils
import imaplib
import json
import os
import re
import time
import unittest
from unittest import mock

import jarvis_mcp
import mailboxes

ACC = {"key": "1", "name": "web.de", "host": "x", "port": 993, "user": "a@example.com", "password": "x"}


class Box:
    """An IMAP inbox: n mails, one per 6 hours back from now, all unread; uid = seq + 100."""
    def __init__(self, n):
        now = time.time()
        self.mails = {i: {"uid": i + 100, "ts": now - (i - 1) * 6 * 3600, "seen": False,
                          "from": "Bank <info@bank.example>" if i == 7 else f"Shop {i} <news@shop.example>",
                          "subject": "Kontoauszug" if i == 7 else f"Angebot {i}"} for i in range(1, n + 1)}
        self.stored = []

    def _head(self, m):
        date = email.utils.formatdate(m["ts"], localtime=True)
        return f"From: {m['from']}\r\nSubject: {m['subject']}\r\nDate: {date}\r\n\r\n".encode()

    def search(self, charset, *crit):
        since = time.mktime(time.strptime(crit[crit.index("SINCE") + 1], "%d-%b-%Y"))
        unseen = "UNSEEN" in crit
        return "OK", [b" ".join(str(i).encode() for i, m in sorted(self.mails.items())
                               if m["ts"] >= since and not (unseen and m["seen"]))]

    def fetch(self, ids, what):
        seqs = [int(i) for i in (ids.decode() if isinstance(ids, bytes) else ids).split(",")]
        if what == "(INTERNALDATE)":
            return "OK", [f"{i} (INTERNALDATE {imaplib.Time2Internaldate(self.mails[i]['ts'])})".encode() for i in seqs]
        if what == "(BODY.PEEK[])":
            return "OK", [(f"{i} (BODY[] {{1}}".encode(), self._head(self.mails[i]) + b"Hallo\r\n") for i in seqs]
        assert "HEADER.FIELDS" in what, what
        return "OK", [(f"{i} (BODY[HEADER] {{1}}".encode(), self._head(self.mails[i])) for i in seqs]

    def uid(self, cmd, *args):
        by_uid = {m["uid"]: m for m in self.mails.values()}
        if cmd == "search":
            want_seen = args[1] == "SEEN"
            return "OK", [b" ".join(str(m["uid"]).encode() for m in by_uid.values() if m["seen"] == want_seen)]
        if cmd == "fetch":
            uids = [int(u) for u in args[0].split(",") if int(u) in by_uid]
            return "OK", [(f"{u} (UID {u} BODY[HEADER] {{1}}".encode(), self._head(by_uid[u])) for u in uids]
        if cmd == "STORE":
            self.stored.append(args)
            for u in args[0].split(","):
                by_uid[int(u)]["seen"] = args[1] == "+FLAGS"
            return "OK", []
        raise AssertionError(cmd)

    def logout(self):
        pass


def run(box, fn, args):
    with mock.patch.object(mailboxes, "accounts", return_value=[ACC]), \
            mock.patch.object(mailboxes, "connect", return_value=box):
        return fn(args)


class InboxPeriod(unittest.TestCase):
    def test_default_stays_24_hours_and_days_goes_back(self):
        box = Box(20)   # 20 mails over ~5 days
        self.assertTrue(run(box, jarvis_mcp._mail, {}).startswith("4 ungelesene Mails der letzten 24 Stunden"))
        out = run(box, jarvis_mcp._mail, {"days": 3, "limit": 50})
        self.assertTrue(out.startswith("12 ungelesene Mails der letzten 3 Tage"), out[:80])
        self.assertIn("Kontoauszug", out, "the compact list shows every mail of the period")

    def test_days_is_capped(self):
        with mock.patch.object(mailboxes, "READ_DAYS", 2):
            out = run(Box(20), jarvis_mcp._mail, {"days": 30, "limit": 50})
        self.assertTrue(out.startswith("8 ungelesene Mails der letzten 2 Tage"), out[:80])

    def test_background_readers_keep_the_24_hours(self):
        self.assertEqual(len(mailboxes.recent(Box(20), hours=24 * 30)), 4)


class MarkAll(unittest.TestCase):
    def test_a_pile_is_listed_first_and_nothing_marked(self):
        box = Box(20)
        out = run(box, jarvis_mcp._mail_mark, {"all": True})
        self.assertTrue(out.startswith("NICHTS markiert: 20 ungelesene"), out[:80])
        self.assertIn("Kontoauszug", out)
        self.assertEqual(box.stored, [])

    def test_after_review_marks_and_remembers_and_undoes(self):
        box = Box(20)
        out = run(box, jarvis_mcp._mail_mark, {"all": True, "reviewed": True})
        self.assertTrue(out.startswith("20 Mails als gelesen markiert"), out)
        self.assertTrue(all(m["seen"] for m in box.mails.values()))
        # the module's path, not the environment: isolate.py can be loaded a second time in a full run
        with open(jarvis_mcp.MAIL_MARKED, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["mails"]), 20)
        listed = run(box, jarvis_mcp._mail, {"last_marked": True})
        self.assertIn("20 Mails habe ich", listed)
        self.assertIn("Kontoauszug", listed)
        out = run(box, jarvis_mcp._mail_mark, {"unread": True, "last_batch": True})
        self.assertTrue(out.startswith("20 Mails als ungelesen markiert"), out)
        self.assertFalse(any(m["seen"] for m in box.mails.values()))

    def test_few_or_by_sender_need_no_review(self):
        box = Box(5)
        self.assertTrue(run(box, jarvis_mcp._mail_mark, {"all": True}).startswith("5 Mails als gelesen"))
        box = Box(20)
        self.assertTrue(run(box, jarvis_mcp._mail_mark, {"search": "bank"}).startswith("1 Mails als gelesen"))
        self.assertEqual(re.sub(r"\D", "", box.stored[0][0]), "107")


if __name__ == "__main__":
    unittest.main()
