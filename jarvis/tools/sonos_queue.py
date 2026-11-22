"""
The rest of the playlist behind a song the HA automation "Jarvis: Musik wandert mit" moved onto the bedroom Sonos.

Spotify Connect never lists the Sonos Era 100, so HA plays only the running track there, by its Spotify link (HA
knows no playlist). Just before, HA fires the event `jarvis_musik_sonos` {player, track}. Jarvis reads Spotify's
queue at once — the old device still holds the session — waits until the Sonos plays that very track and adds the
queue behind it (media_player.play_media, enqueue add). Without Jarvis the bedroom stops after the one song.

Chosen by Nutzer on 19.11.2026: an exception to "background jobs only propose" (CLAUDE.md, SECURITY.md). It only
fills the queue of a speaker HA just started on his own walk; it never starts, stops or turns up anything.

  on_event(data)              called by ha_events.py for every `jarvis_musik_sonos`
  append(player, track)       the work, in the calling thread; returns a short German result for the log

  JARVIS_SONOS_QUEUE          1 (default) / 0 = off
  JARVIS_SONOS_QUEUE_MAX      20 — Spotify's queue endpoint hands out about that many
  JARVIS_SONOS_QUEUE_WAIT_S   20 — how long the Sonos may take to start the track
"""
import os
import re
import threading
import time

import jarvis_log

log = jarvis_log.get(__name__)

ENABLED = os.environ.get("JARVIS_SONOS_QUEUE", "1").strip().lower() not in ("0", "false", "no", "off", "aus")
MAX = int(os.environ.get("JARVIS_SONOS_QUEUE_MAX", "20") or 20)
WAIT_S = float(os.environ.get("JARVIS_SONOS_QUEUE_WAIT_S", "20") or 20)
POLL_S = 1.0
_TRACK = re.compile(r"^spotify:track:[A-Za-z0-9]{22}$")
_CONTEXT = ("spotify:playlist:", "spotify:album:", "spotify:artist:")
_RECENT = {}             # (player, track) -> time: HA restarts its run on sensor flicker, the event can come twice
_LOCK = threading.Lock()


def _ha(path, body=None):
    import jarvis_mcp
    return jarvis_mcp._ha(path, body)


def _logbook(message):
    try:
        _ha("/api/services/logbook/log", {"name": "Jarvis Musik", "entity_id": "input_select.nutzer_raum",
                                          "message": message})
    except Exception:  # noqa: BLE001 — the Jarvis log has it as well
        pass


def _plays(player, track):
    s = _ha(f"/api/states/{player}") or {}
    return s.get("state") == "playing" and track.split(":")[-1] in str((s.get("attributes") or {}).get("media_content_id") or "")


def append(player, track):
    import spotify
    tracks = spotify.upcoming(MAX)
    if not tracks:
        ctx = spotify.now_playing()[0]
        tracks = [ctx] if ctx.startswith(_CONTEXT) else []
    if not tracks:
        return "nichts anzuhängen (Spotify kennt keine Warteschlange)"
    end = time.time() + WAIT_S
    while True:
        try:
            if _plays(player, track):
                break
        except Exception:  # noqa: BLE001 — HA restarting: try again until the time is up
            pass
        if time.time() >= end:
            return "Sonos hat den Titel nicht gestartet, nichts angehängt"
        time.sleep(POLL_S)
    added = 0
    for uri in tracks:
        kind = "playlist" if uri.startswith(_CONTEXT) else "music"
        try:
            _ha("/api/services/media_player/play_media",
                {"entity_id": player, "media_content_type": kind, "media_content_id": uri, "enqueue": "add"})
            added += 1
        except Exception as e:  # noqa: BLE001 — one track Sonos does not take must not cost the rest
            log.warning("Sonos-Warteschlange %s: %s", uri, str(e)[:120])
    if not added:
        return "Sonos hat nichts angenommen"
    what = "die Playlist" if tracks[0].startswith(_CONTEXT) else f"{added} Titel aus der Spotify-Warteschlange"
    _logbook(f"Jarvis hängt {what} hinter das Lied (der Sonos spielt danach weiter).")
    return f"{added} angehängt"


def on_event(data):
    if not ENABLED:
        return False
    player = str((data or {}).get("player") or "")
    track = str((data or {}).get("track") or "")
    if not (player.startswith("media_player.") and _TRACK.match(track)):
        return False
    now = time.time()
    with _LOCK:
        for k, t in list(_RECENT.items()):
            if now - t > 120:
                _RECENT.pop(k, None)
        if (player, track) in _RECENT:
            return False
        _RECENT[(player, track)] = now

    def run():
        try:
            log.info("Musik wandert ins Schlafzimmer: %s", append(player, track))
        except Exception as e:  # noqa: BLE001 — never let it break the live view
            log.warning("Sonos-Warteschlange: %s", str(e)[:160])

    threading.Thread(target=run, daemon=True, name="sonos-queue").start()
    return True
