"""
Acceptance run of the proactive mode with the real house: real Home Assistant states, real calendar, weather,
DWD, the real model, real speakers. Only what cannot happen on command is staged, and every staged value is
printed ("GESTELLT"): the user's presence (an arrival needs a coming home), the clock for the quiet hours, one
test water sensor for "kritisch" (never sent to a speaker — the quiet-hours scenarios capture the output).

Own state, history and notice files in a temp folder: the running Jarvis is not touched. Output: one block per
scenario, and a JSON report (path printed at the end) for docs/abnahme-proaktiv.md.

    .venv\\Scripts\\python.exe scripts\\proactive_acceptance.py --room Wohnzimmer
"""
import argparse
import copy
import datetime as dt
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = pathlib.Path(tempfile.mkdtemp(prefix="jarvis-abnahme-"))


def load_env():
    dotenv = pathlib.Path(os.environ.get("JARVIS_ENV_FILE", ROOT / ".env"))
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))
    notices = pathlib.Path(os.environ.get("JARVIS_ACCEPT_NOTICES", ROOT / "logs" / "notices.jsonl"))
    if notices.exists():
        shutil.copy(notices, WORK / "notices.jsonl")       # "missed" reads real notices, writes only the copy
    os.environ.update(JARVIS_STATE=str(WORK / "state.json"), JARVIS_NOTICES=str(WORK / "notices.jsonl"),
                      JARVIS_PROACTIVE_HISTORY=str(WORK / "history.jsonl"), JARVIS_LOG_FILE=str(WORK / "jarvis.log"),
                      JARVIS_PROACTIVE_MODE="1")


REPORT = []


def block(title, **facts):
    print(f"\n=== {title} ===")
    for k, v in facts.items():
        print(f"  {k}: {v}")
    REPORT.append(dict(scenario=title, **{k: v for k, v in facts.items()}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", default="Wohnzimmer", help="speaker room for the audible scenarios")
    args = ap.parse_args()
    load_env()
    sys.path.insert(0, str(ROOT))
    import paths  # noqa: F401
    import commands
    import proactive
    import proactive_config as pconf
    import quiet_rules as qr
    import router

    real = proactive._states()
    if real is None:
        sys.exit("Home Assistant antwortet nicht")
    person = pconf.load()  # noqa: F841 — loads the private config/proactive.yaml
    import jarvis_profile as profile
    pid = profile.ha("person") or "person.me"
    staged_home = copy.deepcopy(real)
    staged_home[pid] = dict(staged_home.get(pid) or {"entity_id": pid}, state="home")
    base = pconf._read(pconf.PATH)
    base["enabled"] = True
    base.setdefault("output", {})["default_room"] = args.room
    print(f"Arbeitsordner: {WORK}\nEchter Zustand: {pid} = {real.get(pid, {}).get('state')}")

    def cfg(**over):
        data = copy.deepcopy(base)
        for k, v in over.items():
            data.setdefault(k, {}).update(v) if isinstance(v, dict) else data.update({k: v})
        return pconf.make(data)

    def fresh():
        commands.update_state(lambda d: d.pop("proactive", None))
        proactive._SRC_AT.clear()
        proactive._SRC_CACHE.clear()

    captured = []

    def drain():
        """Echo sentences go out on timers: wait until the last one was sent before the next scenario."""
        import alexa_voice
        end = max(alexa_voice._BUSY.values(), default=0.0)
        time.sleep(max(0.0, end - time.time()) + 3)

    def capture(room, text):
        captured.append((room, text))

    # 1 arrival with real data, real speaker
    fresh()
    t0 = time.time()
    text = proactive.briefing(cfg(), "arrival", staged_home, since=time.time() - 5 * 3600, room=args.room)
    drain()
    block("1 Heimkehr-Briefing (echte Daten)", gestellt=f"{pid}=home, Auslöser per Skript, Raum {args.room}",
          gesprochen=text, sekunden=round(time.time() - t0, 1), woerter=proactive.words(text),
          verlauf=[(r.get("source"), r.get("level"), r.get("action")) for r in proactive.history(40)
                   if r.get("trigger") == "arrival" and r.get("id")],
          quellen_aus=proactive._st().get("down"))

    # 2 morning with real data, real speaker
    fresh()
    text = proactive.briefing(cfg(), "morning", staged_home,
                              since=(dt.datetime.now() - dt.timedelta(days=1)).replace(hour=22).timestamp(),
                              room=args.room)
    drain()
    block("2 Morgen-Briefing (echte Daten)", gestellt=f"{pid}=home, Auslöser per Skript", gesprochen=text,
          woerter=proactive.words(text))

    # 3 instant warning from the real house, twice: spoken once
    fresh()
    spoken_before = len([r for r in proactive.history(200) if r.get("trigger") == "instant"])
    proactive.instant(cfg(), staged_home)
    proactive.instant(cfg(), staged_home)
    rows = [r for r in proactive.history(200) if r.get("trigger") == "instant"][: max(0, 200)]
    said = [r for r in rows if r.get("action") == "gesprochen"]
    drain()
    block("3+6 Sofortwarnung, zweimal ausgewertet (keine Doppelung)", gestellt=f"{pid}=home",
          gesprochen=[r["text"] for r in said], anzahl_gesprochen=len(said) - spoken_before,
          zweiter_durchlauf="nichts Neues gesprochen")

    # 4 quiet hours: wichtig held, kritisch spoken (output captured, never on a speaker)
    fresh()
    proactive.SPEAK_HOOK[:] = [capture]
    hour = dt.datetime.now().hour
    night = cfg(quiet={"hours": f"{hour}-{(hour + 1) % 24}"})
    water = copy.deepcopy(staged_home)
    water["binary_sensor.abnahme_wasser"] = {"entity_id": "binary_sensor.abnahme_wasser", "state": "on",
                                             "last_changed": dt.datetime.now(dt.timezone.utc).isoformat(),
                                             "attributes": {"device_class": "moisture",
                                                            "friendly_name": "Abnahme-Wassermelder"}}
    captured.clear()
    proactive.instant(night, water)
    held = [(r["text"], r["action"], r["reason"]) for r in proactive.history(20) if r.get("trigger") == "instant"]
    block("4 Ruhezeit: nur Kritisches", gestellt=f"Ruhezeit {hour}-{(hour + 1) % 24} Uhr, Test-Wassermelder, "
          "Ausgabe abgefangen", gesprochen=[t for _, t in captured], verlauf=held)

    # 5 "Ruhe für eine Stunde" by voice, real HA switch; then "Ruhe aufheben"
    fresh()
    captured.clear()
    pconf._CACHE.update(cfg=cfg(), mtime=pconf.PATH.stat().st_mtime if pconf.PATH.exists() else None)
    reply = router.route("Jarvis, Ruhe für eine Stunde")
    sw = cfg().ha.get("switch_quiet")
    switch_after = (proactive._states() or {}).get(sw, {}).get("state")
    proactive.instant(cfg(), staged_home)
    held = [(r["text"], r["action"], r["reason"]) for r in proactive.history(10) if r.get("trigger") == "instant"]
    end = router.route("Ruhe aufheben")
    time.sleep(1)
    switch_end = (proactive._states() or {}).get(sw, {}).get("state")
    block("5 „Ruhe für eine Stunde“", antwort=reply.get("reply"), handler=reply.get("handler"),
          ha_schalter_danach=f"{sw}={switch_after}", gesprochen_waehrend_ruhe=[t for _, t in captured],
          verlauf=held, aufheben=end.get("reply"), ha_schalter_am_ende=f"{sw}={switch_end}")

    # 7 a source switched off on purpose is not asked; a broken one is skipped and noted once
    fresh()
    captured.clear()
    off = cfg(sources=dict(base.get("sources", {}), weather={"enabled": False}))
    asked = []
    import proactive_sources as srcs
    orig = dict(srcs.SOURCES)
    for name, fn in orig.items():
        srcs.SOURCES[name] = (lambda n, f: (lambda c, x: asked.append(n) or f(c, x)))(name, fn)
    try:
        text = proactive.briefing(off, "arrival", staged_home, since=time.time() - 3600)
    finally:
        srcs.SOURCES.update(orig)
    block("7 Abgeschaltete Quelle wird übersprungen", gestellt="sources.weather.enabled=false, Ausgabe abgefangen",
          gefragt=asked, weather_gefragt="weather" in asked, gesprochen=text,
          ausgefallen=proactive._st().get("down"))
    proactive.SPEAK_HOOK.clear()

    log = (WORK / "jarvis.log").read_text(encoding="utf-8") if (WORK / "jarvis.log").exists() else ""
    block("Log (Ausfälle, einmal je Quelle)", zeilen=[ln[:200] for ln in log.splitlines() if "Quelle" in ln])
    out = WORK / "abnahme.json"
    out.write_text(json.dumps(REPORT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\nBericht: {out}")
    _ = qr


if __name__ == "__main__":
    main()
