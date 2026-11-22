"""
Sets up Home Assistant Assist for the Home Assistant Voice PE with Jarvis as the brain. Safe to run again:
it only adds what is missing and brings the pipeline up to date.

  Wyoming Whisper  127.0.0.1:10300 on the Pi (pi/voice/compose.yml): speech to text, German
  Wyoming Piper    127.0.0.1:10200 on the Pi: text to speech, voice de_DE-thorsten-medium
  Pipeline "Jarvis" (preferred): stt.jarvis (the PC's GPU Whisper, falls back to the Pi's) ->
                   conversation.jarvis -> answer on the room's speaker. The Voice PE is only the microphone
                   (Nutzer, 17.11.2026): the Jarvis integration gives a satellite empty speech, so the
                   pipeline skips Piper, and the wake sound is off. Piper must stay in the pipeline all the
                   same: the satellite asks for that stage and blinks red without it.
                   Everything goes to Jarvis (ha/custom_components/jarvis); with the PC off the integration
                   hands over to the Pi's Whisper and Home Assistant's agent. Without that integration the
                   pipeline uses the Pi's Whisper and Home Assistant's agent; run this again then.
  Voice PE         its wake word is set to "Hey Jarvis" (on the device, no wake word server needed)

The Jarvis integration itself is added by hand (Settings -> Devices & services -> Add integration ->
Jarvis), because its key is yours to enter. Reads HA_URL and HA_TOKEN from .env. From the repository root:

    .venv\\Scripts\\python.exe scripts\\ha_voice_setup.py           # shows what is there and what is missing
    .venv\\Scripts\\python.exe scripts\\ha_voice_setup.py --apply   # sets it up
"""
import asyncio
import json
import os
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
PIPELINE = "Jarvis"
WYOMING = {"stt": ("127.0.0.1", 10300), "tts": ("127.0.0.1", 10200)}
TTS_VOICE = "de_DE-thorsten-medium"
WAKE_WORD = "Hey Jarvis"
MIC_ONLY = "--with-voice" not in sys.argv     # only the wake sound; see above
AGENT = "conversation.jarvis"
JARVIS_STT = "stt.jarvis"
FALLBACK_AGENT = "conversation.home_assistant"


def env():
    dotenv = pathlib.Path(os.environ.get("JARVIS_ENV_FILE", ROOT / ".env"))
    for line in (dotenv.read_text(encoding="utf-8").splitlines() if dotenv.exists() else []):
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))
    return os.environ.get("HA_URL", "").rstrip("/"), os.environ.get("HA_TOKEN", "")


class HA:
    def __init__(self, url, token):
        self.url, self.token, self.ws, self.n = url, token, None, 0

    def rest(self, method, path, body=None):
        req = urllib.request.Request(self.url + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"null")

    async def open(self):
        import websockets
        self.ws = await websockets.connect(self.url.replace("http", "ws", 1) + "/api/websocket", max_size=2 ** 24)
        await self.ws.recv()
        await self.ws.send(json.dumps({"type": "auth", "access_token": self.token}))
        if json.loads(await self.ws.recv()).get("type") != "auth_ok":
            sys.exit("HA_TOKEN rejected")

    async def call(self, type_, **kw):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "type": type_, **kw}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self.n:
                if not msg.get("success", True):
                    raise RuntimeError(f"{type_}: {msg.get('error')}")
                return msg.get("result")


def wyoming_entries(ha):
    return [e for e in ha.rest("GET", "/api/config/config_entries/entry") if e["domain"] == "wyoming"]


def add_wyoming(ha, host, port):
    flow = ha.rest("POST", "/api/config/config_entries/flow", {"handler": "wyoming", "show_advanced_options": False})
    res = ha.rest("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", {"host": host, "port": port})
    if res.get("type") != "create_entry":
        raise RuntimeError(f"Wyoming {host}:{port}: {res.get('errors') or res.get('reason') or res.get('type')}"
                           " — läuft der Container auf dem Pi? (pi/voice/compose.yml)")
    return res["result"]["entry_id"]


async def main(apply):
    url, token = env()
    if not (url and token):
        sys.exit("HA_URL / HA_TOKEN fehlen in .env")
    ha = HA(url, token)
    await ha.open()
    todo = []

    entries = wyoming_entries(ha)
    have = {(e.get("title") or "").lower() for e in entries}
    print("Wyoming-Einträge:", [e.get("title") for e in entries] or "keine")
    registry = await ha.call("config/entity_registry/list")
    by_entry = {}
    for ent in registry:
        by_entry.setdefault(ent.get("config_entry_id"), []).append(ent)

    def engine(domain):
        for e in wyoming_entries(ha):
            for ent in by_entry.get(e["entry_id"], []):
                if ent["entity_id"].startswith(domain + "."):
                    return ent["entity_id"]
        return None

    for kind, (host, port) in WYOMING.items():
        if engine(kind):
            continue
        todo.append(f"Wyoming {kind.upper()} {host}:{port} verbinden")
        if apply:
            add_wyoming(ha, host, port)
            registry = await ha.call("config/entity_registry/list")
            by_entry = {}
            for ent in registry:
                by_entry.setdefault(ent.get("config_entry_id"), []).append(ent)
    stt, tts = engine("stt"), engine("tts")
    states = {s["entity_id"]: s for s in ha.rest("GET", "/api/states")}
    if JARVIS_STT in states and stt:
        stt = JARVIS_STT          # the PC's GPU; it hands the audio to the Pi's Whisper when the PC is off
    print("STT:", stt, "| TTS:", tts, "| bekannt:", sorted(have))

    agent = AGENT if AGENT in states else FALLBACK_AGENT
    if agent != AGENT:
        print(f"Hinweis: {AGENT} fehlt noch (Integration Jarvis nicht eingerichtet) — die Pipeline nutzt vorerst "
              f"{FALLBACK_AGENT}. Nach dem Einrichten dieses Skript noch einmal starten.")

    # With Jarvis as the agent everything goes to him — his router switches "Licht aus" with 0 tokens and knows
    # the room; with the PC off his integration hands over to Home Assistant's agent by itself.
    want = {"name": PIPELINE, "language": "de", "conversation_engine": agent, "conversation_language": "de",
            "stt_engine": stt, "stt_language": "de" if stt else None,
            "tts_engine": tts, "tts_language": "de_DE" if tts else None, "tts_voice": TTS_VOICE if tts else None,
            "wake_word_entity": None, "wake_word_id": None, "prefer_local_intents": agent != AGENT}
    listing = await ha.call("assist_pipeline/pipeline/list")
    current = next((p for p in listing["pipelines"] if p["name"] == PIPELINE), None)
    if current:
        # The Voice PE wakes on the device; the ESP32-S3 Box wakes through openWakeWord in Home Assistant, set
        # in this pipeline by hand. An update replaces every field, so keep whatever wake word is there.
        want["wake_word_entity"] = current.get("wake_word_entity")
        want["wake_word_id"] = current.get("wake_word_id")
    if not (stt and tts):
        todo.append("Pipeline erst, wenn Whisper und Piper verbunden sind")
    elif current is None:
        todo.append(f"Pipeline {PIPELINE} anlegen und bevorzugen")
        if apply:
            current = await ha.call("assist_pipeline/pipeline/create", **want)
    else:
        diff = {k: v for k, v in want.items() if current.get(k) != v}
        if diff:
            todo.append(f"Pipeline {PIPELINE} aktualisieren: {sorted(diff)}")
            if apply:
                await ha.call("assist_pipeline/pipeline/update", pipeline_id=current["id"], **want)
    if current and listing.get("preferred_pipeline") != current.get("id"):
        todo.append(f"Pipeline {PIPELINE} als bevorzugte setzen")
        if apply:
            await ha.call("assist_pipeline/pipeline/set_preferred", pipeline_id=current["id"])

    sats = [e for e in states if e.startswith("assist_satellite.")]
    print("Satelliten:", sats or "noch keiner (Voice PE noch nicht eingebunden)")
    wake = [ent["entity_id"] for ent in registry
            if ent["entity_id"].startswith("select.") and ent.get("platform") == "esphome"
            and ent.get("translation_key") == "wake_word"]
    for eid in wake:
        st = states.get(eid) or {}
        options = (st.get("attributes") or {}).get("options") or []
        if st.get("state") == WAKE_WORD:
            continue
        if WAKE_WORD not in options:
            print(f"{eid}: kein „{WAKE_WORD}“ in {options}")
            continue
        todo.append(f"{eid}: Aktivierungswort „{WAKE_WORD}“")
        if apply:
            ha.rest("POST", "/api/services/select/select_option", {"entity_id": eid, "option": WAKE_WORD})

    if MIC_ONLY:
        # the beep after "Hey Jarvis" is the satellite speaking too; the LED ring still shows it listens
        for eid in [e for e in states if e.startswith("switch.") and e.endswith("_wake_sound")]:
            if states[eid].get("state") == "on":
                todo.append(f"{eid}: Weckton aus (nur Mikrofon)")
                if apply:
                    ha.rest("POST", "/api/services/switch/turn_off", {"entity_id": eid})

    await ha.ws.close()
    if not todo:
        print("Alles eingerichtet.")
    for t in todo:
        print(("erledigt: " if apply else "fehlt: ") + t)


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
