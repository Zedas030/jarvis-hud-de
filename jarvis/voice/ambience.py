"""
What is playing in the room — music, sport, TV — so the HUD can be stricter with "Jarvis".

Home Assistant knows which media players are playing; a loop asks every AMBIENCE_EVERY
seconds and keeps the answer, so /api/status never waits for the house. The HUD adds its
own ear (steady level without speech pauses) and decides; this side only reports.
JARVIS_AMBIENCE=0 switches the house check off.
"""
import os
import re
import threading
import time

ON = os.environ.get("JARVIS_AMBIENCE", "1").strip().lower() not in ("0", "off", "aus", "false", "no", "nein")
EVERY = float(os.environ.get("JARVIS_AMBIENCE_EVERY", "20"))
# Players that never make sound in the room (a phone's media session, a cast group's copy, …).
IGNORE = re.compile(os.environ.get("JARVIS_AMBIENCE_IGNORE", r"^$"), re.I)
STALE_S = 120            # an answer older than this counts as "nothing known"
# HA's Spotify integration is the whole account, not a speaker: "playing" there may be the phone on the
# road. The speaker that plays at home reports itself (Sonos, Echo); the account is judged in step().
SPOTIFY_ACCOUNT = re.compile(os.environ.get("JARVIS_AMBIENCE_SPOTIFY_ACCOUNT", r"^media_player\.spotify_"), re.I)
# Spotify Connect device types that travel with a person: they only count while someone is at home.
PORTABLE = {"smartphone", "tablet", "wearable", "automobile", "watch"}

# Titles, channels and apps that mean a match is on; everything else with video is TV.
SPORT = re.compile(
    r"sport|bundesliga|champions league|fu(ss|ß)ball|dazn|sky sport|magentasport|sportschau|"
    r"sportstudio|formel ?1|formula ?1|\bf1\b|motogp|tennis|handball|basketball|\bnba\b|\bnfl\b|"
    r"eishockey|\bnhl\b|darts|boxen|\bufc\b|tour de france|olympia|wm 20\d\d|em 20\d\d|eurosport|ran\.de",
    re.I)
MUSIC_APPS = re.compile(r"spotify|music|deezer|tidal|soundcloud|radio|tunein|amazon music|plex amp|plexamp", re.I)

_STATE = {"kind": "", "players": [], "at": 0.0}
_LOCK = threading.Lock()


def classify(states):
    """HA states -> ("sport" | "music" | "tv" | "", [names playing]). Sport wins over TV over music:
    the stricter the sound, the more it talks like a person."""
    kinds, names = set(), []
    for s in states or []:
        eid = s.get("entity_id") or ""
        if not eid.startswith("media_player.") or s.get("state") != "playing":
            continue
        a = s.get("attributes") or {}
        name = a.get("friendly_name") or eid
        if IGNORE.search(eid) or IGNORE.search(name) or SPOTIFY_ACCOUNT.search(eid):
            continue
        if a.get("is_volume_muted") is True:
            continue
        text = " ".join(str(a.get(k) or "") for k in ("media_title", "media_series_title", "media_channel",
                                                       "app_name", "source", "media_album_name"))
        ctype = str(a.get("media_content_type") or "").lower()
        if SPORT.search(text):
            kinds.add("sport")
        elif ctype == "music" or MUSIC_APPS.search(text) or a.get("media_artist"):
            kinds.add("music")
        else:
            kinds.add("tv")
        names.append(name)
    for k in ("sport", "tv", "music"):
        if k in kinds:
            return k, names
    return "", names


def someone_home(states):
    """True when a person.* is home — or when HA has no person at all (nothing to judge by)."""
    people = [s.get("state") for s in states or [] if (s.get("entity_id") or "").startswith("person.")]
    return not people or "home" in people


def spotify_at_home(states):
    """Spotify plays and it is audible here: a speaker or the PC, or a phone/tablet while someone is home."""
    import spotify
    if not spotify.configured():
        return False
    on, dev = spotify.active_device()
    if not on:
        return False
    return str(dev.get("type") or "").lower() not in PORTABLE or someone_home(states)


def step():
    import briefing
    states = briefing._ha_states()
    kind, players = classify(states)
    spotify_playing = spotify_at_home(states)
    if kind == "music" and not spotify_playing:
        kind, players = "", []
    elif not kind and spotify_playing:
        kind, players = "music", ["Spotify"]
    with _LOCK:
        _STATE.update(kind=kind, players=players[:4], at=time.time())


def status():
    """For /api/status: kind is empty when nothing plays or the house has not answered lately."""
    with _LOCK:
        fresh = time.time() - _STATE["at"] < STALE_S
        return {"kind": _STATE["kind"] if fresh else "", "players": _STATE["players"] if fresh else []}


def start():
    if not ON:
        return

    def run():
        while True:
            try:
                step()
            except Exception as e:  # noqa: BLE001 — no house, no hint; the HUD's ear still works
                with _LOCK:
                    _STATE.update(kind="", players=[], at=0.0)
                print(f"  ambience: {str(e)[:120]}", flush=True)
            time.sleep(EVERY)
    threading.Thread(target=run, daemon=True, name="ambience").start()
