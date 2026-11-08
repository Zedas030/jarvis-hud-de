"""
When the proactive mode speaks, shows or keeps a notice back. Pure functions, no I/O: the caller
(proactive.py) collects the situation once and passes it in, so every rule is testable on its own.

Levels, most urgent first: kritisch, wichtig, nett — and "stumm" below them, which only feedback reaches
("unwichtig" three times). Kritisch is never lowered.

Holds (any of them = only kritisch is spoken):
  aus        the proactive switch in Home Assistant is off
  notaus     the emergency stop ("Not-Aus") is active
  weg        the user is not at home (nobody to hear it; the arrival briefing brings it, kritisch also goes to the phone)
  ruhe       "Ruhe für eine Stunde" / "heute keine Ansagen" / the quiet switch in Home Assistant
  gaeste     guest mode in Home Assistant
  telefonat  the PC microphone is in use by a call app, or a do-not-disturb entity is on
  ruhezeit   inside the quiet hours (the morning briefing ends the night, so it is not held by them)

Actions: "speak"; "defer" = shown now and spoken later while it is still valid (only wichtig, only after
a hold the user ends during the day: ruhe, gaeste, telefonat); "show" = only on the dashboard.
A request by the user ("gute Nacht", "mehr dazu") is an answer, not an interruption: it is always spoken.
"""
import datetime as dt
import re

LEVELS = ("kritisch", "wichtig", "nett", "stumm")
RETRY_AFTER = ("weg", "ruhe", "gaeste", "telefonat")       # holds that end during the day: wichtig is said afterwards
REASONS = {"aus": "Proaktiv-Modus aus", "notaus": "Not-Aus", "weg": "nicht zu Hause", "ruhe": "Ruhe", "gaeste": "Gäste-Modus",
           "telefonat": "Telefonat", "ruhezeit": "Ruhezeit", "stumm": "per Feedback herabgestuft",
           "nett": "nur nett zu wissen"}


class Decision(tuple):
    """(action, reason, retry) with names."""
    __slots__ = ()

    def __new__(cls, action, reason="", retry=False):
        return super().__new__(cls, (action, reason, retry))

    action = property(lambda s: s[0])
    reason = property(lambda s: s[1])
    retry = property(lambda s: s[2])


def in_hours(spec, now):
    """True inside '22-7' (wraps over midnight). '' / 'off' / garbage = never."""
    spec = str(spec or "").strip().lower()
    if not spec or spec in ("0", "off", "aus", "none"):
        return False
    try:
        start, end = (int(x) for x in spec.split("-", 1))
    except ValueError:
        return False
    if start == end:
        return False
    h = now.hour
    return start <= h < end if start < end else h >= start or h < end


def demote(level, steps):
    """`level` lowered by `steps` (feedback); kritisch stays kritisch, nothing goes below stumm."""
    level = level if level in LEVELS else "nett"
    if level == "kritisch" or steps <= 0:
        return level
    return LEVELS[min(LEVELS.index(level) + steps, len(LEVELS) - 1)]


def holds(ctx):
    """The active holds as codes, most binding first. ctx keys (all optional):
    now, quiet_hours, proactive_switch (False = off; None = unknown counts as on), emergency, quiet_until (epoch),
    away, quiet_switch, guests, call, night_ended (the morning briefing)."""
    now = ctx.get("now") or dt.datetime.now()
    out = []
    if ctx.get("proactive_switch") is False:
        out.append("aus")
    if ctx.get("emergency"):
        out.append("notaus")
    if ctx.get("away"):
        out.append("weg")
    if ctx.get("quiet_switch") or (ctx.get("quiet_until") or 0) > now.timestamp():
        out.append("ruhe")
    if ctx.get("guests"):
        out.append("gaeste")
    if ctx.get("call"):
        out.append("telefonat")
    if not ctx.get("night_ended") and in_hours(ctx.get("quiet_hours", ""), now):
        out.append("ruhezeit")
    return out


def decide(level, ctx, mode="instant", critical_always=True):
    """What to do with one notice. mode: "instant" (it just happened), "briefing" (arrival, morning),
    "requested" (the user asked)."""
    if level == "stumm":
        return Decision("show", "stumm")
    if mode == "requested":
        return Decision("speak", "angefragt")
    active = holds(ctx)
    if level == "kritisch" and (critical_always or not active):
        return Decision("speak", "kritisch")
    if active:
        first = active[0]
        if level == "wichtig":
            return Decision("defer", first, retry=all(h in RETRY_AFTER for h in active))
        return Decision("show", first)
    if mode == "instant" and level == "nett":
        return Decision("show", "nett")            # nice to know never interrupts; it waits for a briefing
    return Decision("speak", "")


def briefing_allowed(ctx):
    """A whole briefing (arrival, morning) is spoken only without holds; kritisch inside it still speaks."""
    return not holds(ctx)


# ── voice commands for quiet ─────────────────────────────────
_NUM = {"ein": 1, "eine": 1, "einen": 1, "einer": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "fuenf": 5,
        "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "zwoelf": 12,
        "zwanzig": 20, "dreißig": 30, "dreissig": 30, "vierzig": 40, "fünfzig": 50, "neunzig": 90}
_FOR = re.compile(r"\b(ruhe|still|keine ansagen|keine meldungen|nicht stören|nicht stoeren)\b.*?\b(für|fuer)\s+"
                  r"(\d+|[a-zäöüß]+)\s*(stunden?|std|minuten?|min)\b")
_TODAY = re.compile(r"\b(heute|für heute|fuer heute)\b.*\bkeine (ansagen|meldungen|durchsagen)\b|"
                    r"\bkeine (ansagen|meldungen|durchsagen)\b.*\b(heute|mehr für heute|bis morgen)\b|"
                    r"\bruhe bis morgen\b")
_END = re.compile(r"\b(ruhe (aufheben|beenden|vorbei|aus)|ansagen wieder (an|einschalten)|"
                  r"du darfst wieder (sprechen|reden)|keine ruhe mehr)\b")


_SPOKEN_TIMES = ((re.compile(r"\b(eine )?halbe stunde\b"), "30 minuten"),
                 (re.compile(r"\b(anderthalb|eineinhalb) stunden?\b"), "90 minuten"),
                 (re.compile(r"\b(eine )?viertelstunde\b"), "15 minuten"))


def _amount(word):
    return (int(word) if word.isdigit() else _NUM.get(word)), None


def parse_quiet(n, now, today_until_hour=5):
    """A quiet command in the normalised text `n`: ("set", until_datetime) / ("end", None) / None."""
    n = " ".join((n or "").lower().split())
    for pattern, plain in _SPOKEN_TIMES:
        n = pattern.sub(plain, n)
    if _END.search(n):
        return ("end", None)
    m = _FOR.search(n)
    if m:
        value, unit = _amount(m.group(3))
        if value:
            unit = unit or ("min" if m.group(4).startswith("min") else "h")
            minutes = value if unit == "min" else value * 60
            if 0 < minutes <= 24 * 60:
                return ("set", now + dt.timedelta(minutes=minutes))
    if _TODAY.search(n):
        return ("set", until_morning(now, today_until_hour))
    return None


def until_morning(now, hour=5):
    """The next morning at `hour` (still today when it is past midnight and before that hour)."""
    day = now.date() if now.hour < hour else now.date() + dt.timedelta(days=1)
    return dt.datetime.combine(day, dt.time(hour))
