"""
When Jarvis asks back, the satellite listens again by itself — no second "Okay Jarvis" (Nutzer, 19.11.2026).

  "Wie sehen meine Mails aus?" — "Meinen Sie Ihre Mails, Sir?" — "Ja." : a conversation, not three starts.

The answer comes from the room's speaker (Echo/Sonos), not from the satellite, so Home Assistant's own
continue_conversation would open the microphone while Jarvis is still talking (and satellite_quiet would
cut him off). Instead, once the room's speaker should be done, Jarvis calls
assist_satellite.start_conversation on the satellite that asked: it "plays" a quarter second of silence
(the Voice PE never says anything) and starts listening without a wake word. Nobody answers: the Voice PE
gives up after 5 s (firmware no_speech_timeout).

  JARVIS_SAT_FOLLOWUP              1 (default) / 0 = off
  JARVIS_SAT_FOLLOWUP_MEDIA        the silence, served by HA (www/jarvis/stille.wav)
  JARVIS_SAT_FOLLOWUP_ECHO_TAIL_S  2.5 — after the Echo's estimated end (Alexa starts late and reads at her pace)
  JARVIS_SAT_FOLLOWUP_SONOS_TAIL_S 1.2 — after Jarvis' Sonos stream closed (the Sonos buffers)
"""
import os
import re
import threading
import time

import jarvis_log

log = jarvis_log.get(__name__)

ENABLED = os.environ.get("JARVIS_SAT_FOLLOWUP", "1").strip().lower() not in ("0", "false", "no", "off", "aus")
MEDIA = os.environ.get("JARVIS_SAT_FOLLOWUP_MEDIA", "").strip()
ECHO_TAIL_S = float(os.environ.get("JARVIS_SAT_FOLLOWUP_ECHO_TAIL_S", "2.5"))
SONOS_TAIL_S = float(os.environ.get("JARVIS_SAT_FOLLOWUP_SONOS_TAIL_S", "1.2"))
MAX_WAIT_S = 60.0
LAST_MAX_AGE_S = 120.0       # the satellite that listened last is the one that asked

_LOCK = threading.Lock()
_ST = {"last": "", "last_at": 0.0, "pending": 0}
_SENTENCE = re.compile(r"[^.!?]+[.!?]*")


def heard(satellite):
    """satellite_quiet: this satellite just started listening."""
    with _LOCK:
        _ST["last"], _ST["last_at"] = satellite, time.time()


def last_satellite(now=None):
    now = time.time() if now is None else now
    with _LOCK:
        return _ST["last"] if _ST["last"] and now - _ST["last_at"] <= LAST_MAX_AGE_S else ""


def pending():
    """A follow-up is waiting to open the microphone (listen_duck keeps the room quiet meanwhile)."""
    with _LOCK:
        return _ST["pending"] > 0


def asks_back(answer):
    """Jarvis' answer ends in a question — also "Meinen Sie X? Falls ja, sage ich ...": a question in the
    last two sentences."""
    parts = [p.strip() for p in _SENTENCE.findall(str(answer or "")) if p.strip()]
    return any(p.endswith("?") for p in parts[-2:])


def _media_url():
    if MEDIA:
        return MEDIA
    import jarvis_mcp
    return (jarvis_mcp.HA_URL or "").rstrip("/") + "/local/jarvis/stille.wav"


def _speaker_done_at(target):
    """Epoch seconds when the room's speaker should be quiet, or None while that is not known yet."""
    import alexa_voice
    import sonos
    if alexa_voice.is_alexa(target):
        with alexa_voice._LOCK:
            queued = bool(alexa_voice._PENDING.get(target)) or any(t.is_alive() for t in alexa_voice._TIMERS.get(target, []))
            end = max(alexa_voice._SAYING.get(target, 0.0), alexa_voice._BUSY.get(target, 0.0))
        return None if queued else end + ECHO_TAIL_S
    if sonos.speaking(target):
        return None
    return time.time() + SONOS_TAIL_S


def alexa_voice_target(target):
    import alexa_voice
    return alexa_voice.is_alexa(target)   # an Echo's end estimate can grow; a Sonos' tail counts from the first quiet look


def _open(satellite):
    import jarvis_mcp
    import wake_learn
    wake_learn.opened(satellite)          # this listening is Jarvis' doing, not a wake word
    jarvis_mcp._ha("/api/services/assist_satellite/start_conversation",
                   {"entity_id": satellite, "start_media_id": _media_url(), "preannounce": False})


def _wait_and_open(satellite, target):
    deadline = time.time() + MAX_WAIT_S
    done_at = None
    try:
        while time.time() < deadline:
            at = _speaker_done_at(target)
            if at is None:                # still speaking (or more queued): start over
                done_at = None
            else:
                done_at = at if done_at is None else max(done_at, at) if alexa_voice_target(target) else done_at
                if time.time() >= done_at:
                    break
            time.sleep(0.1)
        else:
            log.info("Nachfrage: %s spricht noch nach %d s, Mikro bleibt zu", target, MAX_WAIT_S)
            return False
        if last_satellite() != satellite:
            return False                  # he used another satellite meanwhile, or it is too long ago
        _open(satellite)
        log.info("Nachfrage: %s hört ohne Aktivierungswort zu", satellite)
        return True
    except Exception as e:  # noqa: BLE001 — HA down: he says "Okay Jarvis" as before
        log.warning("Nachfrage: %s", str(e)[:160])
        return False
    finally:
        with _LOCK:
            _ST["pending"] -= 1


def after_answer(answer, target, satellite=None):
    """remote_ask: the answer was said on `target`. A question in it -> the satellite listens again."""
    satellite = satellite or last_satellite()
    if not (ENABLED and target and satellite and asks_back(answer)):
        return None
    with _LOCK:
        _ST["pending"] += 1
    t = threading.Thread(target=_wait_and_open, args=(satellite, target), daemon=True, name="satellite-followup")
    t.start()
    return t


def reset():
    """For the tests."""
    with _LOCK:
        _ST.update(last="", last_at=0.0, pending=0)
