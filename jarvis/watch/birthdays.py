"""
Birthdays from the memory, the day before — with what Jarvis knows the person likes. Local, 0 tokens.

    "Morgen hat Anna Geburtstag, sie wird 34. Notiert habe ich: mag Tulpen, liest gern Krimis."

Found in the vault notes under Erinnerungen (Personen/<Name> first, then any bullet like
"Anna hat am 3. Mai Geburtstag" or "Geburtstag: 03.05.1992") — also the ones the learning loop saved by
itself (reflection.remember). The gift idea is no model call: the bullets of the person's note that say what
they like (mag, liebt, wünscht sich, sammelt, Hobby …).

  JARVIS_BIRTHDAYS        1 / 0 = off
  JARVIS_BIRTHDAY_TIME    when the day-before notice comes (18:00)

  upcoming(days, today)   [(date, name, age|None)] sorted
  step(now)               background: the day-before notice, once per person and year
  handle(n)               router: "Wer hat bald Geburtstag?", "Wann hat Anna Geburtstag?"
"""
import datetime as dt
import os
import re

import events
import memory

ENABLED = os.environ.get("JARVIS_BIRTHDAYS", "1").strip().lower() not in ("0", "false", "no", "off")
AT = os.environ.get("JARVIS_BIRTHDAY_TIME", "18:00").strip()
MONTHS = {m: i for i, m in enumerate(("januar", "februar", "märz", "april", "mai", "juni", "juli", "august",
                                      "september", "oktober", "november", "dezember"), 1)}
_DATE_WORD = re.compile(r"(\d{1,2})\.?\s*(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?", re.I)
_DATE_NUM = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})?\b")
_NAME_IN = re.compile(r"(?:\[\[)?([A-ZÄÖÜ][\wäöüß-]+)(?:\]\])?\s+(?:hat|feiert)\b")
_LIKES = re.compile(r"\b(mag|liebt|wünscht|wünscht sich|sammelt|steht auf|hobby|interessiert sich|liest gern|"
                    r"trinkt gern|isst gern|spielt gern|fan von)\b", re.I)
_TAG = re.compile(r"\s*_\([^)]*\)_\s*$")


def _plain(text):
    return _TAG.sub("", re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", text)).strip()


def _date_of(text):
    m = _DATE_WORD.search(text)
    if m:
        return int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3)) if m.group(3) else None
    m = _DATE_NUM.search(text)
    if m:
        y = m.group(3)
        year = (int(y) + (1900 if int(y) > 30 else 2000) if len(y) == 2 else int(y)) if y else None
        return int(m.group(1)), int(m.group(2)), year
    return None


def people():
    """{name: {"day", "month", "year", "likes": [...]}} from the vault."""
    found = {}
    likes = {}
    root = memory.MEMORY_DIR
    if not root.exists():
        return {}
    for path in root.rglob("*.md"):
        person = path.stem if "Personen" in path.parts else None
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            if not ln.startswith("- "):
                continue
            text = _plain(ln[2:])
            if person and _LIKES.search(text):
                likes.setdefault(person, []).append(text)
            if not re.search(r"geburtstag|geboren", text, re.I):
                continue
            d = _date_of(text)
            if not d:
                continue
            name = person
            if not name:
                m = _NAME_IN.search(text)
                name = m.group(1) if m else None
            if not name or name in found and person is None:
                continue
            try:
                dt.date(2000, d[1], d[0])          # leap-year safe check of day/month
            except ValueError:
                continue
            found[name] = {"day": d[0], "month": d[1], "year": d[2]}
    for name, info in found.items():
        info["likes"] = likes.get(name, [])[:3]
    return found


def _next(day, month, today):
    for year in (today.year, today.year + 1):
        try:
            d = dt.date(year, month, day)
        except ValueError:                          # 29.02. outside a leap year
            d = dt.date(year, 3, 1)
        if d >= today:
            return d
    return None


def upcoming(days=30, today=None):
    today = today or dt.date.today()
    out = []
    for name, p in people().items():
        d = _next(p["day"], p["month"], today)
        if d and (d - today).days <= days:
            out.append((d, name, d.year - p["year"] if p.get("year") else None))
    return sorted(out)


def _likes_text(name, info):
    likes = [re.sub(rf"^{re.escape(name)}\s+", "", x) for x in info.get("likes") or []]
    return (" Notiert habe ich: " + ", ".join(likes) + ".") if likes else ""


def notice(name, info, age):
    who = f"Morgen hat {name} Geburtstag"
    return who + (f", {name} wird {age}." if age else ".") + _likes_text(name, info)


def step(now=None):
    import background
    now = now or dt.datetime.now()
    if not ENABLED or now.strftime("%H:%M") < AT:
        return []
    tomorrow = now.date() + dt.timedelta(days=1)
    said = []
    ppl = people()
    for d, name, age in upcoming(1, now.date()):
        if d != tomorrow or not background.claim(f"birthday:{name}", str(d.year)):
            continue
        text = notice(name, ppl[name], age)
        events.publish("say", text, kind="birthday")
        said.append(text)
    return said


def handle(n):
    if re.search(r"\bwer hat (bald|demnächst|als nächstes|diesen monat) geburtstag\b|\bnächste geburtstage?\b", n):
        soon = upcoming(30)
        if not soon:
            return "In den nächsten 30 Tagen hat niemand Geburtstag, den ich kenne, Sir."
        return "Bald Geburtstag: " + "; ".join(
            f"{name} am {d.day}.{d.month}." + (f" ({age})" if age else "") for d, name, age in soon[:5]) + "."
    m = re.search(r"\bwann hat ([a-zäöüß-]+) geburtstag\b", n)
    if m:
        want = m.group(1).lower()
        for name, p in people().items():
            if name.lower() == want:
                return f"{name} hat am {p['day']}.{p['month']}. Geburtstag." + _likes_text(name, p)
        return None                                 # unknown here: let the model look further
    return None


def start():
    import background
    if ENABLED:
        background.loop("birthdays", 900, step)
