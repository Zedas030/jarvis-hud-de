"""
Speakers in other rooms and the TV — through Home Assistant, no new hardware.

  announce(room, text)  a spoken announcement in a room: an Echo speaks with Alexa's voice
                        (notify.alexa_media_*, type announce), a Sonos with Home Assistant's
                        TTS (tts.speak, German). Jarvis' own voice stays on the PC/iPad.
                        In the quiet hours both speak quietly: the Echo is turned down to
                        JARVIS_ALEXA_NIGHT_VOLUME and back, the Sonos announces at
                        JARVIS_SONOS_NIGHT_VOLUME and returns to its own volume.
                        No room (or "hier") = the room the presence sensors see the user in.
  tv(command, app)      the Fire TV through Alexa Media Player: the command is sent to the TV's
                        Alexa as if spoken there (media_player.play_media, type custom).
                        Only fixed commands; an app name is checked — no free text reaches
                        Alexa, so nothing can be bought or called that way.

  spotify_device(room)  which Spotify Connect device plays in that room (for spotify.transfer): the
                        `spotify` entry of the room, else the room's name — Sonos and Alexa groups
                        usually carry the room's name in Spotify anyway.
  spotify_fallback(room) the room's `spotify_fallback` device: a worse speaker that only plays when the
                        first one stays unreachable (the Echo Dot alone when the soundbar is not in
                        Spotify's device list), or "".

config/home.json:
  "speakers": {"Wohnzimmer": {"notify": "notify.alexa_media_nutzer_echo_dot", "spotify": "Wohnzimmer",
                              "spotify_fallback": "Nutzer' Echo Dot"},
               "Schlafzimmer": {"tts": "tts.google_translate_en_com",
                                "player": "media_player.schlafzimmer_schlafzimmer"}},
  "tv": "media_player.nutzer_fire_tv"
"""
import re

TV_COMMANDS = {
    "pause": "pause", "weiter": "weiter", "lauter": "lauter", "leiser": "leiser", "stumm": "stummschalten",
    "laut": "stummschaltung aufheben", "zurueck": "zurück", "home": "geh zum startbildschirm",
    "aus": "schalte den fernseher aus", "an": "schalte den fernseher ein",
}
APP = re.compile(r"^[a-zA-Z0-9äöüÄÖÜß+ .-]{2,30}$")
HERE = re.compile(r"^(hier|bei mir|zu mir|wo ich bin|da wo ich bin|in meinem raum|aktuell|current)$", re.I)
# words that must never reach Alexa inside an app name
FORBIDDEN = re.compile(r"kauf|bestell|order|ruf|anruf|nachricht|einkauf|bezahl|zahl|abo|alexa|routine|skill", re.I)


def _quiet():
    try:
        import events
        return events.quiet_now()
    except Exception:  # noqa: BLE001
        return False


def _cfg():
    import jarvis_profile as profile
    return profile.ha("speakers", {}) or {}, profile.ha("tv")


def rooms():
    return sorted(_cfg()[0])


def _room(word, speakers):
    w = (word or "").strip().lower()
    hits = [r for r in speakers if r.lower() in w or w in r.lower()] if w else []
    return hits[0] if len(hits) == 1 else None


def spotify_device(room):
    """(room name, Spotify device name, Home Assistant player or "") for a room with a speaker; (None, None, "")
    when the room is unknown. The player is the way round Spotify Connect: a Sonos is only in Spotify's device
    list while it plays, but Home Assistant can hand it a Spotify playlist or track directly."""
    speakers, _ = _cfg()
    name = _room(room, speakers)
    if not name:
        return None, None, ""
    return name, (speakers[name].get("spotify") or name), (speakers[name].get("player") or "")


def spotify_fallback(room):
    """The room's second-choice Spotify Connect device, or ""."""
    speakers, _ = _cfg()
    name = _room(room, speakers)
    return (speakers[name].get("spotify_fallback") or "") if name else ""


def announce(room, text):
    import jarvis_mcp as j
    import whereabouts
    speakers, _ = _cfg()
    room = " ".join(str(room or "").split())
    if not room or HERE.match(room):
        room = whereabouts.room()
        if not room:
            return ("Ich weiß gerade nicht, in welchem Raum Sie sind. Sagen Sie mir den Raum: "
                    f"{', '.join(sorted(speakers)) or 'keiner eingerichtet'}.")
    name = _room(room, speakers)
    if not name:
        return f"Einen Lautsprecher für „{room}“ kenne ich nicht. Ich kann sprechen in: {', '.join(sorted(speakers)) or 'nirgends'}."
    text = " ".join(str(text or "").split())[:300]
    if not text:
        return "Was soll ich durchsagen?"
    sp = speakers[name]
    try:
        if sp.get("notify"):
            import alexa_voice
            alexa_voice.night_down(sp["notify"], alexa_voice.seconds(text) + 2)   # quiet hours: Echo turned down
            service = sp["notify"].split(".", 1)[1]
            j._ha(f"/api/services/notify/{service}", {"message": text, "data": {"type": "announce"}})
        elif sp.get("tts") and sp.get("player") and _quiet():
            # quiet hours: an announcement at the night volume, the Sonos goes back to its own afterwards
            import urllib.parse
            import sonos
            uri = f"media-source://tts/{sp['tts']}?" + urllib.parse.urlencode({"message": text, "language": "de"})
            j._ha("/api/services/media_player/play_media", {
                "entity_id": sp["player"], "media_content_id": uri, "media_content_type": "music",
                "announce": True, "extra": {"volume": sonos._percent(sonos.NIGHT_VOLUME)}})
        elif sp.get("tts") and sp.get("player"):
            j._ha("/api/services/tts/speak", {"entity_id": sp["tts"], "media_player_entity_id": sp["player"],
                                              "message": text, "language": "de"})
        else:
            return f"Für {name} ist kein Lautsprecher eingetragen."
    except Exception as e:  # noqa: BLE001
        return f"Die Durchsage im {name} hat nicht geklappt ({str(e)[:60]})."
    return f"Durchgesagt im {name}."


def tv(command, app=""):
    import jarvis_mcp as j
    _, player = _cfg()
    if not player:
        return "Für den Fernseher ist nichts eingetragen (config/home.json → tv)."
    command = (command or "").strip().lower().replace("ü", "ue")
    if command == "app":
        app = " ".join(str(app or "").split())
        if not APP.match(app) or FORBIDDEN.search(app):
            return f"„{app}“ öffne ich nicht — nur Namen von Apps."
        spoken = f"öffne {app}"
    elif command in TV_COMMANDS:
        spoken = TV_COMMANDS[command]
    else:
        return f"Das kann ich am Fernseher nicht. Möglich: {', '.join(sorted(TV_COMMANDS))} oder eine App öffnen."
    result = j._home("home_control", {"entity_id": player, "service": "play_media",
                                      "data": {"media_content_type": "custom", "media_content_id": spoken}})
    if result and result.startswith(("Abgelehnt", "Home Assistant meldet", "Home Assistant noch")):
        return result
    return "Erledigt." if command != "app" else f"{app} wird geöffnet."
