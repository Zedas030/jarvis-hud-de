"""
"Wo ist der Ersatzschlüssel?" — where the user put things. Local, 0 tokens, in the vault note Orte.

    "Ich hab den Ersatzschlüssel in die blaue Kiste im Flur gelegt."   -> Orte: „Ersatzschlüssel: in die blaue Kiste im Flur“
    "Merk dir, der Reisepass liegt im Schreibtisch oben links."
    "Die Winterreifen sind jetzt im Keller."
    "Wo ist der Ersatzschlüssel?" / "Wo hab ich den Reisepass hingelegt?"

A new place for the same thing replaces the old one (the old bullet goes, with its date, into the history of
memory_save). A plain statement only counts with „merk dir“, „jetzt“ or „ich hab … gelegt“: otherwise
"das Licht ist im Wohnzimmer an" or the TV would fill the note. Questions find the bullet by its words; when
the note has nothing, the full-text index (memory_index.py) may know an older conversation about it.
"""
import re

NOTE = "Orte"
_ART = r"(?:der|die|das|den|dem|mein|meine|meinen|meinem|unser|unsere|unseren)"
_PREP = r"(?:in|im|ins|auf|aufs|unter|unterm|hinter|neben|bei|beim|an|am|ans|oben|unten|zwischen|vor)"
_PUT = re.compile(rf"^\s*(?:jarvis\s+)?(?:merk dir\s+)?ich\s+(?:habe|hab)\s+{_ART}\s+(?P<item>.{{2,40}}?)\s+"
                  rf"(?P<place>{_PREP}\s+.{{2,60}}?)\s+(?:gelegt|getan|gepackt|gestellt|gehängt|verstaut|versteckt)\s*$")
_IS = re.compile(rf"^\s*(?:jarvis\s+)?(?P<merk>merk dir\s+(?:dass\s+)?)?{_ART}\s+(?P<item>.{{2,40}}?)\s+"
                 rf"(?:liegt|liegen|ist|sind|steht|stehen|hängt|hängen|befindet sich|befinden sich)\s+(?P<now>jetzt\s+)?"
                 rf"(?P<place>{_PREP}\s+.{{2,60}}?)\s*$")
_ASK = re.compile(rf"^\s*(?:jarvis\s+)?(?:weißt du\s+)?wo\s+(?:ist|sind|liegt|liegen|steht|stehen|hängt|hab ich|habe ich|"
                  rf"hatte ich|war|waren)\s+(?:{_ART}\s+)?(?P<item>.{{2,40}}?)(?:\s+(?:hingelegt|hingetan|gelassen|hin|"
                  rf"verstaut|abgelegt|hingestellt))?\s*$")
_NOT_THINGS = re.compile(r"\b(licht|lampe|heizung|musik|tv|fernseher|ich|du|er|sie|es)\b")


def _not_a_thing(item):
    """People and devices are other questions ("wo ist <user name>" belongs to the room tracking)."""
    import jarvis_profile
    name = (jarvis_profile.USER_NAME or "").split(" ")[0].lower()
    return bool(_NOT_THINGS.search(item) or (name and re.search(rf"\b{re.escape(name)}\b", item)))


def cased(fragment):
    """The user's own spelling of a fragment the router lower-cased ("blaue kiste im flur" -> "blaue Kiste im Flur")."""
    import router
    raw = " ".join(re.sub(r"[^\wäöüßÄÖÜ%]+", " ", router._raw() or "").split())
    frag = " ".join((fragment or "").split())
    i = raw.lower().find(frag.lower())
    return raw[i:i + len(frag)] if frag and i >= 0 else frag


def _norm_item(item):
    item = " ".join(item.split()).strip(" ,.")
    return item[:1].upper() + item[1:]


def _bullets():
    import memory
    path = memory.note_path(NOTE)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [ln[2:].strip() for ln in lines if ln.startswith("- ")]


def _strip_links(text):
    return re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", text)


def remember(item, place):
    import jarvis_mcp
    import memory_tools
    item = _norm_item(item)
    text = f"{item}: {place.strip()}"
    old = next((b for b in _bullets() if _strip_links(memory_tools.split_tag(b)[0]).lower().startswith(item.lower() + ":")), None)
    args = {"note": NOTE, "text": text, "quelle": "gesagt"}
    if old:
        args["replace"] = memory_tools.split_tag(old)[0]
    res = jarvis_mcp._memory("memory_save", args)
    if res.startswith(("Gespeichert", "Steht")):
        return f"Notiert, Sir: {item} {place.strip()}."
    return res


def find(item):
    import memory_tools
    words = [w for w in re.findall(r"[a-zäöüß]{3,}", item.lower())]
    hits = []
    for b in _bullets():
        plain = _strip_links(memory_tools.split_tag(b)[0])
        head, _, place = plain.partition(":")
        if words and all(w in head.lower() for w in words):
            day = memory_tools.split_tag(b)[2]
            hits.append((head.strip(), place.strip(), day))
    return hits


def handle(n):
    n = re.sub(r"^\s*ist\s+(?=wo\b)", " ", n)      # router._local puts "ist" before a question it does not know
    m = _ASK.match(n)
    if m:
        item = m.group("item")
        if _not_a_thing(item):
            return None                     # "wo ist <name>", "wo ist das Licht an" are other questions
        hits = find(item)
        if hits:
            head, place, day = hits[-1]
            return f"{head}: {place}" + (f" — so haben Sie es mir am {day[:6]} gesagt." if day else ".")   # dd.mm.
        try:
            import memory_index
            turns = memory_index.search(item, limit=1, kind="turn")
        except Exception:  # noqa: BLE001
            turns = []
        if turns:
            return (f"Notiert habe ich dazu nichts. Im Gespräch vom {turns[0]['day'][8:10]}.{turns[0]['day'][5:7]}. "
                    f"ging es darum: {turns[0]['snippet'][:160]}")
        return f"Für „{_norm_item(item)}“ haben Sie mir keinen Ort gesagt, Sir."
    m = _PUT.match(n)
    if not m:
        m = _IS.match(n)
        if not m or not (m.group("merk") or m.group("now")):
            return None
    if _not_a_thing(m.group("item")):
        return None
    return remember(cased(m.group("item")), cased(m.group("place")))
