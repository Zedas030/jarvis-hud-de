"""
Words Whisper keeps getting wrong for this speaker, put right before anyone reads the transcript.

Nutzer, 20.11.2026: reading his mail took half a dozen tries — "Mails" came out as "Milz" and "Sunnails",
"auf gelesen" as "aufgelesen". The vocabulary prompt (voice_stt.Recognizer.vocabulary) nudges Whisper towards
the right word; what still slips through the same way every time is listed in config/heard.json and replaced
in the finished transcript, for the PC's microphone and the satellites alike (Recognizer.final):

    {"fixes": {"milz": "Mails", "aufgelesen": "auf gelesen"}}

Whole words or phrases, upper/lower case ignored. The file is his to edit and is read again when it changes.
Only put in what he never means literally.

  JARVIS_HEARD_FIX   path of the table (default config/heard.json); a missing file means no fixes
"""
import json
import os
import pathlib
import re

import jarvis_log

log = jarvis_log.get(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = pathlib.Path(os.environ.get("JARVIS_HEARD_FIX", ROOT / "config" / "heard.json"))
_TABLE = {"mtime": None, "rules": []}


def _rules():
    try:
        mtime = PATH.stat().st_mtime
    except OSError:
        _TABLE.update(mtime=None, rules=[])
        return []
    if mtime != _TABLE["mtime"]:
        rules = []
        try:
            fixes = (json.loads(PATH.read_text(encoding="utf-8")) or {}).get("fixes") or {}
            # longest first, so a phrase wins over a word inside it
            for heard, meant in sorted(fixes.items(), key=lambda kv: -len(kv[0])):
                heard, meant = str(heard).strip(), str(meant).strip()
                if heard and meant:
                    rules.append((re.compile(r"(?<!\w)" + re.escape(heard).replace(r"\ ", r"\s+") + r"(?!\w)", re.I),
                                  meant, heard))
        except (OSError, ValueError, AttributeError) as e:
            log.warning("heard.json: %s", str(e)[:120])
        _TABLE.update(mtime=mtime, rules=rules)
    return _TABLE["rules"]


def fix(text):
    """The transcript with the known mishearings replaced. Never raises."""
    if not text:
        return text
    try:
        hit = []
        for pat, meant, heard in _rules():
            text, n = pat.subn(lambda _m, meant=meant: meant, text)
            if n:
                hit.append(heard)
        if hit:
            log.info("Verhörer ersetzt: %s", ", ".join(hit))
    except Exception as e:  # noqa: BLE001 — better the raw transcript than none
        log.warning("heard_fix: %s", str(e)[:120])
    return text
