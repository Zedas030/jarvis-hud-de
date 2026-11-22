"""
The voice follows the hour: at night Jarvis whispers, late in the evening he talks softer and
a little slower, and the model is told to keep it short. Plain clock rules, 0 tokens.

  modes       tag    the normal voice
              spät   from LATE_FROM o'clock: a bit softer (LATE_VOLUME) and slower (SLOW)
              nacht  in the whisper hours (WHISPER_HOURS, default = the quiet hours): the voices
                     that take delivery tags get "[whispers]", the local one goes slower and softer
  override    "Flüstermodus an" / "Flüstern aus" / "sprich normal" switch the mode by hand until
              the next morning (state.json → voice_mode)

  mode(now)          "tag" | "spät" | "nacht"
  prepare(text, supports_tags)   the text a provider gets (tag in front when whispering)
  speed_factor()     >1 = slower, for the local voice (Piper length_scale)
  volume_factor()    the HUD multiplies its slider with it (the quiet-hour share is separate)
  hint()             one line for the model's context in the evening and at night
"""
import datetime as dt
import os
import re
import time

import commands
import events

WHISPER_HOURS = os.environ.get("JARVIS_WHISPER_HOURS", "").strip() or events.QUIET_HOURS
# Without own whisper hours the night voice follows "Gute Nacht" (events.sleeping), not the clock (17.11.2026).
_WHISPER_SET = bool(os.environ.get("JARVIS_WHISPER_HOURS", "").strip())
LATE_FROM = int(os.environ.get("JARVIS_LATE_FROM", "21"))
LATE_VOLUME = float(os.environ.get("JARVIS_LATE_VOLUME", "0.8"))
SLOW = float(os.environ.get("JARVIS_LATE_SPEED", "1.08"))
WHISPER_TAG = os.environ.get("JARVIS_WHISPER_TAG", "[whispers]").strip()
ENABLED = os.environ.get("JARVIS_DAYTIME_VOICE", "1").strip() not in ("0", "false", "no", "off")


def _in_hours(spec, now):
    spec = (spec or "").lower()
    if not spec or spec in ("0", "off", "aus", "none"):
        return False
    try:
        start, end = (int(x) for x in spec.split("-", 1))
    except ValueError:
        return False
    hour = now.hour
    if start == end:
        return False
    return start <= hour < end if start < end else hour >= start or hour < end


def override():
    o = commands.load().get("voice_mode") or {}
    return o.get("mode") if o.get("until", 0) > time.time() else None


def mode(now=None):
    if not ENABLED:
        return "tag"
    given = now
    now = now or dt.datetime.now()
    forced = override()
    if forced:
        return forced
    if _in_hours(WHISPER_HOURS, now) if _WHISPER_SET else events.sleeping(given):
        return "nacht"
    if now.hour >= LATE_FROM:
        return "spät"
    return "tag"


def prepare(text, supports_tags, now=None):
    if mode(now) == "nacht" and supports_tags and WHISPER_TAG and not re.match(r"^\s*\[", text or ""):
        return f"{WHISPER_TAG} {text}"
    return text


def speed_factor(now=None):
    return SLOW if mode(now) in ("spät", "nacht") else 1.0


def volume_factor(now=None):
    m = mode(now)
    if m == "spät":
        return LATE_VOLUME
    if m == "nacht" and override() == "nacht" and not events.sleeping(now):
        return LATE_VOLUME                     # whispering by hand outside the quiet hours: softer too
    return 1.0


def hint(now=None):
    m = mode(now)
    if m == "nacht":
        return "Es ist Nacht: antworte in höchstens zwei kurzen Sätzen, ruhig, ohne Pointe."
    if m == "spät":
        return "Später Abend: knapp und ruhig antworten."
    return ""


# ── by hand ──────────────────────────────────────────────────

def _until_morning(now=None):
    now = now or dt.datetime.now()
    morning = (now + dt.timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
    if now.hour < 7:
        morning = now.replace(hour=7, minute=0, second=0, microsecond=0)
    return morning.timestamp()


def set_mode(m, now=None):
    commands.update_state(lambda d: d.__setitem__("voice_mode", {"mode": m, "until": _until_morning(now)} if m else {}))


_ON = re.compile(r"\b(flüster(modus|n)? (an|ein|bitte)|flüster\w*|leise sprechen|sprich leise|nachtmodus (an|ein))\b")
_OFF = re.compile(r"\b(flüster(modus|n)? aus|nicht mehr flüstern|sprich (wieder )?normal|normal sprechen|"
                  r"normale lautstärke|nachtmodus aus)\b")


def handle(n):
    if _OFF.search(n):
        set_mode("tag")
        return "Normale Stimme, Sir."
    if _ON.search(n) and not re.search(r"\baus\b", n):
        set_mode("nacht")
        return "Natürlich, Sir. Ich flüstere bis zum Morgen."
    return None
