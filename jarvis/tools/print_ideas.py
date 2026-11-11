"""
Ideas for the 3D printer: "Merk dir als Druckidee: Kabelhalter für den Schreibtisch". Local, 0 tokens.

A third list next to shopping and to-do (lists.py, key "druckideen", always the vault note
Erinnerungen/Listen/Druckideen — Home Assistant has no use for it):

  "Merk dir als Druckidee …", "Neue Druckidee: …"        add
  "Was wollte ich noch drucken?", "Welche Druckideen …"  read
  "Der Kabelhalter ist gedruckt", "Streich die Druckidee …"  remove
  "Such den Kabelhalter auf MakerWorld"                  opens MakerWorld's search in the browser (open_website)
"""
import re
import urllib.parse

KEY = "druckideen"
_IDEA = r"druck ?ide(?:e|en)"
_ADD = re.compile(rf"^\s*(?:jarvis\s+)?(?:bitte\s+)?(?:merk dir\s+|notier\s+|schreib\s+)?(?:als|eine|neue|noch eine)?\s*"
                  rf"{_IDEA}\s+(?:ist\s+|wäre\s+)?(?P<item>.{{3,80}}?)\s*$")
_ADD2 = re.compile(rf"^\s*(?:jarvis\s+)?merk dir\s+(?P<item>.{{3,80}}?)\s+als\s+{_IDEA}\s*$")
_READ = re.compile(rf"\b(was wollte ich (noch |mal )?drucken|welche {_IDEA}|zeig (mir )?(die |meine )?{_IDEA}|"
                   rf"^\s*(meine |die )?{_IDEA}\s*$|was steht auf (der |den )?{_IDEA})")
_DONE = re.compile(rf"^\s*(?:jarvis\s+)?(?:streich|lösch|entfern)\w*\s+(?:die\s+)?{_IDEA}\s+(?P<item>.{{2,80}}?)\s*$"
                   r"|^\s*(?:jarvis\s+)?(?:der|die|das)\s+(?P<item2>.{2,60}?)\s+(?:ist|sind)\s+(?:fertig\s+)?gedruckt\s*$")
_MAKER = re.compile(r"^\s*(?:jarvis\s+)?(?:such|zeig|öffne|find)\w*\s+(?:mir\s+)?(?:die\s+druck ?idee\s+)?"
                    r"(?:nach\s+)?(?:einen?\s+|den\s+|die\s+|das\s+)?(?P<item>.{2,60}?)\s+(?:auf|bei|in)\s+maker ?world\s*$")


def cased(fragment):
    """The user's own spelling of a fragment the router lower-cased ("blaue kiste im flur" -> "blaue Kiste im Flur")."""
    import router
    raw = " ".join(re.sub(r"[^\wäöüßÄÖÜ%]+", " ", router._raw() or "").split())
    frag = " ".join((fragment or "").split())
    i = raw.lower().find(frag.lower())
    return raw[i:i + len(frag)] if frag and i >= 0 else frag


def _lists():
    import lists
    lists.LISTS.setdefault(KEY, {"title": "Druckideen", "entity": ""})
    return lists


def handle(n):
    m = _MAKER.match(n)
    if m:
        import jarvis_mcp
        item = cased(m.group("item").strip())
        url = "https://makerworld.com/de/search/models?keyword=" + urllib.parse.quote(item)
        jarvis_mcp._open(url)
        return f"Ich habe MakerWorld mit „{item}“ geöffnet, Sir."
    lists = _lists()
    m = _DONE.match(n)
    if m:
        item = (m.group("item") or m.group("item2") or "").strip()
        gone = lists.remove(KEY, item)
        if gone:
            return f"„{gone}“ von den Druckideen gestrichen, Sir."
        return None if m.group("item2") else f"„{item}“ steht nicht bei den Druckideen, Sir."
    m = _ADD2.match(n) or _ADD.match(n)
    if m and not _READ.search(n):
        item = cased(re.sub(r"^(?:für\s+)?", "", m.group("item").strip(" :,.")))
        new = lists.add(KEY, [item[:1].upper() + item[1:]])
        return f"Druckidee notiert: {new[0]}, Sir." if new else "Die Idee steht schon drauf, Sir."
    if _READ.search(n):
        ideas = lists.entries(KEY)
        if not ideas:
            return "Noch keine Druckideen, Sir. Sagen Sie zum Beispiel: Merk dir als Druckidee einen Kabelhalter."
        return f"Ihre Druckideen ({len(ideas)}): " + lists.say_list(ideas[:8]) + "."
    return None
