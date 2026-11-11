"""
Settings of the voice engine: config/voice.yaml on top of the defaults below.

Every threshold and timeout of listening, endpointing, barge-in and speaking lives in that one
file (JARVIS_VOICE_CONFIG points elsewhere). The defaults here are the same values, so a missing
key or a missing file never breaks the engine — it only runs with stock settings.

    cfg = voice_config.load()          # cached; reload() after editing the file
    cfg.vad.start_threshold            # attribute access, nested
    cfg["endpoint"]["base_ms"]         # dict access works too
"""
import copy
import os
import pathlib
import threading
import jarvis_log

log = jarvis_log.get(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATH = pathlib.Path(os.environ.get("JARVIS_VOICE_CONFIG", "") or ROOT / "config" / "voice.yaml")

DEFAULTS = {
    "engine": {"enabled": True, "bind": "127.0.0.1", "port": 8724, "sample_rate": 16000, "frame_ms": 20,
               "out_rate": 24000, "preroll_ms": 600, "debug_audio": False,
               "state_log": "logs/voice_state.jsonl", "remote": True},
    "vad": {"model": "models/silero_vad.onnx", "start_threshold": 0.55, "stop_threshold": 0.35,
            "barge_threshold": 0.70, "min_speech_ms": 200, "max_speech_ms": 25000},
    "endpoint": {"base_ms": 450, "unfinished_extra_ms": 900, "question_bonus_ms": -100, "no_text_ms": 700,
                 "max_ms": 1800, "learn_step_ms": 100, "learn_max_ms": 600, "continue_window_ms": 4000,
                 "partial_every_ms": 700, "turn_model": ""},
    "stt": {"partials": "auto", "final_beam": 5, "partial_beam": 1, "min_chars": 2,
            "vocabulary": ["Jarvis", "Home Assistant", "Grafana", "Prometheus", "Axis", "Salto", "Telenot",
                           "Dedrone", "Sonos", "Spotify", "Raspberry Pi", "Pi-hole", "UniFi", "Paperless",
                           "Jellyfin"]},
    "wake": {"conversation_s": 4.0, "window_s": 7.0, "barge_needs_name_with_ambience": True,
             "conversation_with_ambience": False, "drop_foreign": True, "echo_window_s": 60.0,
             "max_glue": 2, "max_glue_with_ambience": 1, "pc_audio": True},
    "barge": {"duck_ms": 60, "duck_gain": 0.2, "confirm_ms": 150, "fade_ms": 30, "clip_silence_ms": 500,
              "clip_max_ms": 8000, "resume_after_ms": 1500,
              "backchannel": ["mhm", "mh", "hm", "hmm", "ja", "ja ja", "jo", "genau", "okay", "ok", "aha",
                              "ach so", "alles klar", "verstehe", "gut", "stimmt", "richtig", "klar", "oh",
                              "ah", "äh", "ähm"],
              "stop_words": ["stopp", "stop", "halt", "ruhe", "ruhig", "still", "sei still", "sei ruhig",
                             "schon gut", "danke reicht", "danke das reicht", "reicht", "genug", "danke",
                             "pause", "warte", "moment", "hör auf", "aufhören", "lass gut sein", "abbrechen"],
              "echo_text_ratio": 0.6, "sonos_duck_via_ha": True},
    "tts": {"first_chunk_min_chars": 12, "first_chunk_max_chars": 70, "min_chunk_chars": 28, "gap_ms": 120,
            "provider_timeout_s": 20},
    "llm": {"thinking_cue_after_ms": 1200, "first_word_target_ms": 800},
    "metrics": {"file": "logs/voice_metrics.jsonl", "prometheus": True},
}


class Section(dict):
    """A dict whose keys are also attributes: cfg.vad.start_threshold."""
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        self[name] = value


def _wrap(d):
    return Section({k: _wrap(v) if isinstance(v, dict) else v for k, v in d.items()})


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def _read(path):
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        import yaml
    except ImportError:
        log.warning("PyYAML fehlt — Standardwerte (pip install pyyaml)")
        return {}
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        log.warning("config/voice.yaml unlesbar (%s) — Standardwerte", str(e)[:80])
        return {}
    return data if isinstance(data, dict) else {}


_LOCK = threading.Lock()
_CACHE = {"cfg": None, "mtime": None}


def load(path=None):
    """The merged settings; re-read when the file changed on disk."""
    p = pathlib.Path(path) if path else PATH
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = None
    with _LOCK:
        if path is None and _CACHE["cfg"] is not None and _CACHE["mtime"] == mtime:
            return _CACHE["cfg"]
        cfg = _wrap(_merge(DEFAULTS, _read(p)))
        if path is None:
            _CACHE.update(cfg=cfg, mtime=mtime)
        return cfg


def make(overrides=None):
    """Defaults plus overrides, without touching the file — for tests and scripts."""
    return _wrap(_merge(DEFAULTS, overrides or {}))


def reload():
    with _LOCK:
        _CACHE.update(cfg=None, mtime=None)
    return load()


def resolve(rel):
    """A path from the config, relative to the repository root unless absolute."""
    p = pathlib.Path(os.path.expanduser(str(rel)))
    return p if p.is_absolute() else ROOT / p
