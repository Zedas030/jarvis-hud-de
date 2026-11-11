"""
Is this PC playing sound right now — a YouTube video, a film, Spotify? Then the microphone hears
that too, and the voice engine is as strict as with a TV in the room (only "Jarvis" at the start or
end, no follow-up without the name). Home Assistant (ambience.py) knows the TV, not the browser.

A sampler thread reads the peak meter of every Windows audio session once a second (pycaw, which
pc.py already uses). Jarvis' own voice also comes out of the browser, so samples while a session
of the engine speaks — and a moment after — are skipped (own()).

  playing()   True when other sound came out of this PC in most of the last few seconds
  own(on)     the engine starts or stops speaking on this PC

JARVIS_PC_AUDIO=0 switches it off; no pycaw or not Windows: playing() is always False.
"""
import os
import threading
import time

ON = os.environ.get("JARVIS_PC_AUDIO", "1").strip().lower() not in ("0", "off", "aus", "false", "no", "nein")
PEAK = float(os.environ.get("JARVIS_PC_AUDIO_PEAK", "0.02"))      # quieter than this is silence
EVERY_S = 1.0
WINDOW = 6            # samples kept...
NEEDED = 3            # ...and how many of them must have sound
HOLD_S = 2.0          # after his own voice: the browser's buffer may still play
# processes whose sound is never media (Jarvis' local voice, system sounds)
IGNORE = {p.strip().lower() for p in os.environ.get("JARVIS_PC_AUDIO_IGNORE", "python.exe,pythonw.exe").split(",")
          if p.strip()}

_LOCK = threading.Lock()
_STATE = {"samples": [], "own": 0, "own_end": 0.0, "started": False, "error": ""}


def own(on):
    with _LOCK:
        _STATE["own"] = max(0, _STATE["own"] + (1 if on else -1))
        if not on:
            _STATE["own_end"] = time.time()


def _busy_self(now):
    return _STATE["own"] > 0 or now - _STATE["own_end"] < HOLD_S


def add_sample(loud, now=None):
    """One sample; ignored while he speaks himself. Public for the tests."""
    now = time.time() if now is None else now
    with _LOCK:
        if _busy_self(now):
            return
        _STATE["samples"] = (_STATE["samples"] + [(now, bool(loud))])[-WINDOW:]


def playing(now=None):
    if not ON:
        return False
    _start()
    now = time.time() if now is None else now
    with _LOCK:
        fresh = [loud for at, loud in _STATE["samples"] if now - at <= WINDOW * EVERY_S + 2]
        return sum(fresh) >= NEEDED


def reset():
    with _LOCK:
        _STATE.update(samples=[], own=0, own_end=0.0)


def _peak():
    """Loudest session of another app, 0..1."""
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
    loudest = 0.0
    for s in AudioUtilities.GetAllSessions():
        name = (s.Process.name() if s.Process else "").lower()
        if not name or name in IGNORE:
            continue
        try:
            loudest = max(loudest, s._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue())
        except Exception:  # noqa: BLE001 — a session that just ended
            continue
    return loudest


def _run():
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception as e:  # noqa: BLE001
        _STATE["error"] = str(e)[:120]
        return
    while True:
        try:
            add_sample(_peak() >= PEAK)
        except Exception as e:  # noqa: BLE001 — no pycaw: nothing known, nothing strict
            _STATE["error"] = str(e)[:120]
            return
        time.sleep(EVERY_S)


def _start():
    if _STATE["started"] or os.name != "nt":
        return
    with _LOCK:
        if _STATE["started"]:
            return
        _STATE["started"] = True
    threading.Thread(target=_run, daemon=True, name="pc-audio").start()
