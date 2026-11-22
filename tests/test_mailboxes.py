"""
Mail accounts from .env — no network.

    .venv\\Scripts\\python.exe -m unittest tests.test_mailboxes -v
"""
import isolate  # noqa: F401  (must come first)
import os
import unittest
from unittest import mock

import mailboxes

TWO = {"MAIL_IMAP_HOST": "imap.gmx.net", "MAIL_USER": "a@gmx.de", "MAIL_PASSWORD": "x",
       "MAIL2_IMAP_HOST": "imap.gmail.com", "MAIL2_USER": "b@gmail.com", "MAIL2_PASSWORD": "y",
       "MAIL2_NAME": "Gmail", "MAIL3_IMAP_HOST": "imap.web.de", "MAIL3_USER": "c@web.de"}   # MAIL3 has no password


class Mailboxes(unittest.TestCase):
    def test_first_account_keeps_the_old_names_and_more_follow(self):
        with mock.patch.dict(os.environ, TWO, clear=False):
            accs = mailboxes.accounts()
        self.assertEqual([(a["key"], a["name"], a["host"]) for a in accs],
                         [("1", "a@gmx.de", "imap.gmx.net"), ("2", "Gmail", "imap.gmail.com")],
                         "an incomplete account (no password) is left out")
        self.assertEqual(accs[0]["port"], 993)

    def test_find_by_name_or_address(self):
        with mock.patch.dict(os.environ, TWO, clear=False):
            self.assertEqual(mailboxes.find("gmail")["user"], "b@gmail.com")
            self.assertEqual(mailboxes.find("a@gmx")["user"], "a@gmx.de")
            self.assertEqual(mailboxes.find("")["user"], "a@gmx.de", "no name: the first one")

    def test_one_broken_account_does_not_stop_the_others(self):
        boxes = {"imap.gmx.net": mock.MagicMock()}

        def fake(host, port, timeout=30):
            if host not in boxes:
                raise OSError("connection refused")
            return boxes[host]
        with mock.patch.dict(os.environ, TWO, clear=False), mock.patch("imaplib.IMAP4_SSL", side_effect=fake):
            results, errors = mailboxes.each(lambda acc, box: acc["name"])
        self.assertEqual(results, ["a@gmx.de"])
        self.assertTrue(errors and errors[0].startswith("Gmail:"))
        boxes["imap.gmx.net"].select.assert_called_with("INBOX", readonly=True)


class SearchBox:
    """A web.de-like box: INBOX and Archiv hold mails, Sent/Trash/Spam must never be searched."""
    LIST = [b'(\\Archive \\HasNoChildren) "/" Archiv', b'(\\Sent \\NoInferiors) "/" Gesendet',
            b'(\\HasNoChildren) "/" INBOX', b'(\\Trash \\HasNoChildren) "/" Papierkorb',
            b'(\\NoInferiors) "/" Postausgang', b'(\\Junk \\NoInferiors) "/" Spam',
            b'(\\HasNoChildren) "/" "Meine Sachen"']
    MAILS = {"INBOX": {b"1": ("Stadtwerke <info@stadtwerke.example>", "Ihre Rechnung", "Mon, 03 Nov 2026 10:00:00 +0100")},
             "Archiv": {b"7": ("Stadtwerke GmbH <noreply@stadtwerke.example>", "Abschlag", "Tue, 10 Mar 2026 08:30:00 +0100")}}

    def __init__(self):
        self.selected, self.searched, self.literal = None, [], None

    def list(self):
        return "OK", self.LIST

    def select(self, name, readonly=False):
        assert readonly, "search must stay read-only"
        self.selected = name.strip('"')
        return "OK", [b"1"]

    def search(self, charset, *crit):
        self.searched.append((self.selected, crit))
        return "OK", [b" ".join(self.MAILS.get(self.selected, {}))]

    def fetch(self, ids, what):
        assert "HEADER.FIELDS" in what and "PEEK" in what, "only headers, nothing marked"
        out = []
        for i in ids.split(b","):
            frm, subj, date = self.MAILS[self.selected][i]
            out.append((i + b" (BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {99}",
                        f"From: {frm}\r\nSubject: {subj}\r\nDate: {date}\r\n\r\n".encode()))
            out.append(b")")
        return "OK", out


class MailSearch(unittest.TestCase):
    def test_own_folders_only_inbox_first(self):
        self.assertEqual(mailboxes.folders(SearchBox()), ["INBOX", "Archiv", "Meine Sachen"])

    def test_finds_old_mails_in_inbox_and_archive_headers_only(self):
        box = SearchBox()
        hits = mailboxes.search(box, "stadtwerke")
        self.assertEqual(sorted((h["folder"], h["subject"]) for h in hits),
                         [("Archiv", "Abschlag"), ("INBOX", "Ihre Rechnung")])
        crit = box.searched[0][1]
        self.assertEqual(crit[0], "SINCE")
        self.assertEqual(crit[2:], ("OR", "FROM", '"stadtwerke"', "SUBJECT", '"stadtwerke"'))
        self.assertEqual(box.selected, "INBOX", "INBOX is selected again afterwards")

    def test_window_is_capped(self):
        import time
        box = SearchBox()
        mailboxes.search(box, "stadtwerke", days=5000)
        since = time.mktime(time.strptime(box.searched[0][1][1], "%d-%b-%Y"))
        self.assertGreater(since, time.time() - (mailboxes.SEARCH_DAYS + 2) * 86400)

    def test_umlauts_go_as_literal(self):
        box = SearchBox()
        mailboxes.search(box, "Müller")
        inbox = [c for f, c in box.searched if f == "INBOX"]
        self.assertEqual([c[2] for c in inbox], ["FROM", "SUBJECT"], "sender and subject, each as UTF-8 literal")
        self.assertEqual(box.literal, "Müller".encode())

    def test_tool_answers_with_count_and_range(self):
        import jarvis_mcp
        one = {"MAIL_IMAP_HOST": "imap.example", "MAIL_USER": "a@example.org", "MAIL_PASSWORD": "x"}
        with mock.patch.dict(os.environ, one, clear=False), \
                mock.patch.object(mailboxes, "accounts", return_value=[mailboxes.accounts()[0]]), \
                mock.patch.object(mailboxes, "connect", return_value=SearchBox()):
            out = jarvis_mcp._mail_search({"query": "stadtwerke"})
        self.assertIn("2 Mails", out)
        self.assertIn("10.03.2026", out)
        self.assertIn("[Archiv]", out)
        self.assertIn("Ihre Rechnung", out)

    def test_router_sends_the_question_to_mail(self):
        import router
        for q in ("Kam irgendwann was von Stadtwerke?", "ob irgendwann was von stadtwerke kam",
                  "Hab ich schon mal Post von der Telekom bekommen?"):
            self.assertIn("mail", router.llm_route(q)["groups"], q)


if __name__ == "__main__":
    unittest.main()
