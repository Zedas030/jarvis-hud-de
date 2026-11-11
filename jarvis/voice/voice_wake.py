"""
Was the utterance meant for Jarvis? The text-mode wake word check of the HUD (ui/app.js → Wake),
now on the server so the engine can decide before the model is called.

  check(text, awake, strict) -> (ok, rest)
      ok: the words count; rest: the request without the name.
      awake: a conversation window is open (after an answer, after a bare "Jarvis", push-to-talk);
      strict: TV or music in the room — only a name at the start or the end counts.
  is_bare(text)  the utterance is nothing but the name ("Jarvis?") — he should open the window and listen
  names(text)    the name appears anywhere (barge-in by name)
  foreign(text)  mostly English or another script: the TV, a video, not the user (who speaks German)
"""
import re

# spellings Whisper produces for "Jarvis"; "arvis" lost its first sound
NAME = (r"(?:jarvis|jarviz|jarves|javis|jarwis|jervis|yarvis|garvis|tscharvis|tschawis|dschawis|dschavis|"
        r"charvis|chavis|arvis|jarvi)")
START = re.compile(r"^\W*(?:(?:hey|hi|hallo|ok|okay|he|ey|du)\W+)?" + NAME + r"\b[\s,.!?:;-]*", re.I)
END = re.compile(r"[\s,;-]+(?:bitte\W+)?" + NAME + r"\W*$", re.I)
MID = re.compile(r"(^|[\s,.!?;:-]+)" + NAME + r"(?![^\W\d_])[\s,.!?:;-]*", re.I)
ANY = re.compile(r"(^|[^^\w])" + NAME + r"(?![^\W\d_])", re.I)
WORD = re.compile(r"[^\W\d_]|\d", re.U)
# Function words only: they say which language a sentence is in, names and loanwords do not.
# Whisper is told "de" but writes English commentary as English ("Jarvis Kelsey" = Travis Kelce).
EN = frozenset("""the and is are was were that this these those with what you your he she they them his her
their it its of to for on in at be been have has had will would can could should going gonna get got there
here from about just like not don't it's that's he's they're we're i'm you're let's i we our me my do does
did so but or if when who which where how why than then very really right now well yeah""".split())
DE = frozenset("""der die das den dem des und ist sind war ich du er sie es wir ihr mir mich dir dich mein
dein nicht kein keine bitte mach mache mal noch schon auch auf aus an im in am zum zur mit von vom für bei
nach wie was wer wo warum wann ein eine einen einem einer ja nein doch gut licht heizung wohnzimmer
schlafzimmer küche bad grad uhr heute morgen jetzt gerade dann aber oder wenn ob soll kannst kann wird""".split())
TOKENS = re.compile(r"[a-zäöüß']+", re.I)
# Cyrillic, Greek, Hebrew/Arabic, Japanese, Chinese, Korean: Whisper's guesses at foreign speech
SCRIPT = re.compile("[\u0400-\u04ff\u0370-\u03ff\u0590-\u06ff\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]")


def foreign(text):
    """True when the words are not German: at least three English function words and clearly more
    of them than German ones, or letters of another script. "Jarvis, spiel Let it be" stays German;
    when the name is in it, the words after it count (the TV before "Jarvis, gute Nacht" does not)."""
    text = text or ""
    if len(SCRIPT.findall(text)) >= 3:
        return True
    # TV first, then "Jarvis, gute Nacht ...": what follows the name decides
    m = ANY.search(text)
    if m and len(TOKENS.findall(text[m.end():])) >= 2:
        text = text[m.end():]
    words = [w.lower() for w in TOKENS.findall(text)]
    en = sum(1 for w in words if w in EN)
    de = sum(1 for w in words if w in DE)
    return en >= 3 and en > 2 * de


def names(text):
    return bool(ANY.search(text or ""))


def is_bare(text):
    m = START.match(text or "")
    if not m:
        return False
    return len(WORD.findall((text or "")[m.end():])) < 2


def check(text, awake=False, strict=False):
    text = (text or "").strip()
    m = START.match(text)
    if m:
        return True, text[m.end():].strip()
    e = END.search(text)
    if e:
        punct = re.search(r"[.!?]\s*$", text)
        return True, (text[:e.start()].strip() + (punct.group(0).strip() if punct else ""))
    if not strict:
        mid = MID.search(text)
        if mid:
            return True, re.sub(r"\s+", " ", text[:mid.start()] + " " + text[mid.end():]).strip()
    if awake:
        return True, text
    return False, text
