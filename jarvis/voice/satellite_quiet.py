"""
A voice satellite that starts listening silences Jarvis (Nutzer, 18.11.2026).

The satellites (Voice PE, S3 Box) heard Jarvis' own answer from the Echo, the Sonos or the PC and handed it
to Whisper together with the user's question — at 17:28 the Box transcribed Jarvis' sentence instead of
Nutzer'. So the moment Home Assistant reports a satellite as "listening" (after its wake word), everything
Jarvis is saying stops, like talking over him at the PC:

  - every voice-engine session that speaks or thinks (PC, iPad, phone): Session.stop
  - an answer for a satellite still being said on a room's speaker (remote_ask.RoomVoice), and its run
  - any Sonos announcement or Echo sentence of Jarvis' still going (music the user plays is left alone)

ha_events.py calls on_change() for every state_changed event; the work runs in its own thread so the
WebSocket loop never waits for a speaker.

  JARVIS_SATELLITE_HUSH   1 (default) / 0 = off
"""
import os
import threading

import jarvis_log

log = jarvis_log.get(__name__)

ENABLED = os.environ.get("JARVIS_SATELLITE_HUSH", "1").strip().lower() not in ("0", "false", "no", "off", "aus")
WHY = "Satellit hört zu"


def starts_listening(data):
    """A state_changed event of an assist_satellite.* going to "listening" -> its entity id, else ""."""
    eid = str((data or {}).get("entity_id") or "")
    if not eid.startswith("assist_satellite."):
        return ""
    new = ((data.get("new_state") or {}).get("state")) or ""
    old = ((data.get("old_state") or {}).get("state")) or ""
    return eid if new == "listening" and old != "listening" else ""


def on_change(data):
    """Called by ha_events for every state change. Never raises, never blocks."""
    try:
        import listen_duck          # the room's TV/YouTube steps aside while the satellite listens
        listen_duck.on_change(data)
    except Exception as e:  # noqa: BLE001
        log.warning("listen_duck: %s", str(e)[:120])
    heard = starts_listening(data)
    if heard:
        try:
            import satellite_followup   # which satellite asked: its follow-up opens the mic there
            satellite_followup.heard(heard)
        except Exception as e:  # noqa: BLE001
            log.warning("satellite_followup: %s", str(e)[:120])
        try:
            import wake_learn           # a "Stopp" right after this wake-up marks it as a false one
            wake_learn.woke(heard)
        except Exception as e:  # noqa: BLE001
            log.warning("wake_learn: %s", str(e)[:120])
    eid = heard if ENABLED else ""
    if eid:
        threading.Thread(target=_hush_and_tell, args=(eid,), daemon=True, name="satellite-hush").start()


def _hush_and_tell(satellite):
    stopped = hush(satellite)
    try:
        import wake_learn               # something was stopped: a later "Stopp" is a real one
        wake_learn.hushed(satellite, stopped)
    except Exception as e:  # noqa: BLE001
        log.warning("wake_learn: %s", str(e)[:120])


def _engine_sessions():
    import voice_ws
    return voice_ws.sessions()


def hush(satellite=""):
    """Stops whatever Jarvis is saying, everywhere; -> list of what was stopped (for the log and tests)."""
    stopped = []
    try:
        for s in _engine_sessions():
            if s.busy():
                s.stop(WHY)
                stopped.append(f"engine:{s.device}")
    except Exception as e:  # noqa: BLE001 — the engine is off or a session just closed
        log.debug("engine: %s", str(e)[:120])
    try:
        import remote_ask
        n = remote_ask.hush()
        if n:
            stopped.append(f"satellite answer x{n}")
    except Exception as e:  # noqa: BLE001
        log.warning("remote_ask: %s", str(e)[:120])
    try:
        import alexa_voice
        import sonos
        for out in sonos.outputs():
            eid = out["id"]
            if alexa_voice.is_alexa(eid):
                if alexa_voice.speaking(eid):
                    alexa_voice.stop(eid, hard=True)
                    stopped.append(eid)
            elif sonos.speaking(eid):
                sonos.stop(eid)
                stopped.append(eid)
    except Exception as e:  # noqa: BLE001 — HA down: nothing to stop there either
        log.warning("speakers: %s", str(e)[:120])
    if stopped:
        log.info("%s: gestoppt %s", satellite or WHY, ", ".join(stopped))
    return stopped
