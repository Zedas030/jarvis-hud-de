"""
False wake-ups -> counter-examples for the next wake word training (jarvis/voice/wake_learn.py collects them).

  .venv\\Scripts\\python.exe scripts\\wake_negatives.py            what would be copied (dry run)
  .venv\\Scripts\\python.exe scripts\\wake_negatives.py --apply    copy into the training samples

  *-trigger.wav of confirmed false wake-ups ("Stopp" with nothing to stop)  -> <samples>/jwfalse_trig_1
      the sound that set the satellite off; the training keeps the end of each clip
  *-after.wav (what the room sounded like after a false or silent wake-up)   -> <samples>/jwfalse_room_1

A trigger clip in which Whisper hears "Jarvis" is never copied: then the wake word was right and the "Stopp"
was a change of mind — as a counter-example it would teach the model to miss the name. Those are listed as
"zum Anhören". Without Whisper (--unchecked) nothing is checked: listen to the clips first.

The training itself runs in WSL: ~/work/hjww/tools/07k_jw2.sh (features jwfalse, then jw2 from jw1).
"""
import argparse
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import paths  # noqa: E402,F401

import voice_dsp  # noqa: E402
import voice_wake  # noqa: E402
import wake_learn  # noqa: E402

SAMPLES = pathlib.Path(r"\\wsl.localhost\Ubuntu\root\work\hjww\data\samples")


def plan(entries, clips_dir, says_name):
    """-> (copies [(file, set)], held back [file]). says_name(path) -> True when "Jarvis" is in the clip."""
    copies, held, seen = [], [], set()
    for e in entries:
        for name in e.get("clips") or []:
            f = clips_dir / name
            if name in seen or not f.exists():
                continue
            seen.add(name)
            if name.endswith("-trigger.wav"):
                if not e.get("confirmed"):
                    continue
                if says_name(f):
                    held.append(f)
                else:
                    copies.append((f, "jwfalse_trig_1"))
            elif name.endswith("-after.wav"):
                copies.append((f, "jwfalse_room_1"))
    return copies, held


def whisper_check():
    import voice_config
    import voice_stt
    rec = voice_stt.Recognizer(voice_config.load().stt)
    if not rec.available:
        return None

    def says_name(path):
        pcm, rate = voice_dsp.read_wav(path.read_bytes())[:2]
        return voice_wake.names(rec.final(voice_dsp.resample(pcm, rate, 16000) if rate != 16000 else pcm))
    return says_name


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="copy (default: only list)")
    ap.add_argument("--days", type=int, default=3650, help="only false wake-ups of the last N days")
    ap.add_argument("--to", type=pathlib.Path, default=SAMPLES, help="the training's samples folder")
    ap.add_argument("--unchecked", action="store_true", help="copy trigger clips without the Whisper check")
    a = ap.parse_args()
    check = (lambda f: False) if a.unchecked else whisper_check()
    if check is None:
        sys.exit("Whisper ist nicht verfügbar: Clips erst anhören, dann mit --unchecked kopieren.")
    copies, held = plan(wake_learn.entries(a.days), wake_learn.CLIPS, check)
    new = [(f, s) for f, s in copies if not (a.to / s / f.name).exists()]
    for f, s in new:
        print(("kopiert  " if a.apply else "würde kopieren  ") + f"{s}/{f.name}")
        if a.apply:
            (a.to / s).mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, a.to / s / f.name)
    for f in held:
        print(f"zum Anhören (Whisper hört „Jarvis“, nicht kopiert): {f}")
    total = {s: sum(1 for _, t in copies if t == s) for s in ("jwfalse_trig_1", "jwfalse_room_1")}
    print(f"{len(new)} neu, insgesamt {total['jwfalse_trig_1']} Auslöser-Clips und {total['jwfalse_room_1']} Raum-Clips. "
          + ("" if a.apply else "Nichts kopiert (--apply)."))


if __name__ == "__main__":
    main()
