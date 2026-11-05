"""
Outage map — asks Blast-Radius (dependency map on the Pi, :8098) "what fails if X fails?"; read-only, 0 tokens.

    ask(question)       raw answer of GET /api/ask (matched node, failed/degraded functions)
    spoken(answer)      German sentence for the voice output
    describe(question)  text for the LLM tool `outage_impact`

The spoken names come from `say:` in Blast-Radius' overrides.yaml (e.g. "die Heizung"); without one the
English label is used. BLAST_RADIUS_URL in .env, default http://<JARVIS_PI_HOST>:8098.
"""
import json
import os
import urllib.parse
import urllib.request

_PI = os.environ.get("JARVIS_PI_HOST", "").strip()
URL = os.environ.get("BLAST_RADIUS_URL", "").strip().rstrip("/") or (f"http://{_PI}:8098" if _PI else "")
TIMEOUT = float(os.environ.get("BLAST_RADIUS_TIMEOUT", "4"))
MAX_NAMES = 6        # read aloud: more names than this are counted, not listed


def ask(question):
    if not URL:
        raise RuntimeError("Blast-Radius nicht eingerichtet (BLAST_RADIUS_URL oder JARVIS_PI_HOST in .env)")
    with urllib.request.urlopen(f"{URL}/api/ask?{urllib.parse.urlencode({'q': question})}", timeout=TIMEOUT) as r:
        return json.loads(r.read())


def _name(item):
    return (item.get("say") or item.get("label") or "").strip()


def _join(names):
    names = [n for n in names if n]
    if len(names) > MAX_NAMES:
        rest = len(names) - MAX_NAMES
        return ", ".join(names[:MAX_NAMES]) + f" und {rest} weitere"
    if len(names) > 1:
        return ", ".join(names[:-1]) + " und " + names[-1]
    return names[0] if names else ""


def _cap(s):
    return s[:1].upper() + s[1:]


def spoken(answer):
    """German sentence, or None when Blast-Radius did not know which device was meant."""
    m = answer.get("matched")
    if not m:
        return None
    who = _name(m)
    fn = answer.get("functions") or {}
    failed, degraded = fn.get("failed") or [], fn.get("degraded") or []
    total = fn.get("total") or 0
    if failed:
        verb = "fällt" if len(failed) == 1 else "fallen"
        s = f"Wenn {who} ausfällt, {verb} {len(failed)} von {total} Funktionen aus: {_join([_name(f) for f in failed])}."
    else:
        s = f"Wenn {who} ausfällt, fällt keine Funktion komplett aus."
    if degraded:
        s += f" Eingeschränkt: {_join([_name(f) for f in degraded])}."
    return _cap(s)


def describe(question):
    a = ask(question)
    if not a.get("matched"):
        return "Blast-Radius hat kein passendes Gerät oder keinen Dienst gefunden. " \
               "Frag mit dem Namen, z. B. Pi, Fritzbox, Zigbee-Koordinator, NAS."
    lines = [spoken(a), f"(Knoten: {a['matched'].get('label')}; technisch: {a.get('summary', '')})"]
    alts = [x.get("label") for x in a.get("alternatives") or []][:3]
    if alts:
        lines.append("Andere Treffer: " + ", ".join(alts))
    return "\n".join(lines)
