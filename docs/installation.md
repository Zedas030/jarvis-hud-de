[← Zurück zum README](../README.md)

# Installation und Konfiguration

Voraussetzungen, Schnellstart, lokale Stimmen, alle Einstellungen aus `.env` und die Werkzeuge für Entwicklung und Diagnose.

## Schnellstart

**Voraussetzungen:** Windows 11 · Python 3.12 · Node.js · ein Claude-Abo. Optional: NVIDIA-GPU (CUDA 12.8) für Whisper `large-v3-turbo` und die lokale Stimme.

```powershell
# 1  Claude Code installieren und anmelden (Abo, kein API-Key)
npm.cmd install -g @anthropic-ai/claude-code
claude auth login

# 2  Python-Umgebung
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
# mit NVIDIA-GPU zusätzlich: -r requirements-gpu.txt · auf dem Windows-PC: -r requirements-windows.txt

# 3  Konfiguration
copy .env.example .env                      # Werte eintragen
copy config\home.example.json config\home.json   # HA-Entity-IDs eintragen (optional)
mkdir vault\Erinnerungen

# 4  Start
start-jarvis.bat
```

**Mit dem PC starten (optional):** eine Verknüpfung auf `start-jarvis.bat` im Autostart-Ordner (`shell:startup`, Arbeitsordner das Repo, Fenster minimiert) startet Jarvis beim Anmelden. Abschalten im Task-Manager unter „Autostart-Apps“ oder die Verknüpfung löschen.

**Lokale Stimmen (optional)**

```powershell
# Piper (CPU, sofort einsatzbereit)
mkdir models\piper
curl -L -o models\piper\de_DE-thorsten-high.onnx      https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx
curl -L -o models\piper\de_DE-thorsten-high.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx.json

# Chatterbox Multilingual (GPU, ~5 GB) — Details in requirements-tts.txt
py -3.12 -m venv .venv-tts
.venv-tts\Scripts\python.exe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
.venv-tts\Scripts\python.exe -m pip install --no-deps chatterbox-tts
.venv-tts\Scripts\python.exe -m pip install -r requirements-tts.txt
# Sprachprobe (~10 s, eigene Rechte): models\voice\reference.wav
```

Der Server startet die GPU-Stimme automatisch, sobald `.venv-tts` existiert.

## Konfiguration

Alle Einstellungen stehen kommentiert in [`.env.example`](../.env.example). Persönliche Daten gehören ausschließlich in `.env` und `config/home.json`, beide sind in `.gitignore`.

| Bereich | Variablen | Zweck |
|---|---|---|
| Profil | `JARVIS_USER_NAME`, `JARVIS_PLACE`, `JARVIS_WEATHER_LAT/LON`, `JARVIS_TIMEZONE`, `JARVIS_BROWSER` | Anrede, Ort, Browser |
| Gehirn | `JARVIS_LEAN`, `JARVIS_LOCAL_ROUTER`, `JARVIS_MODEL_SIMPLE/SMART`, `JARVIS_EFFORT`, `JARVIS_MAX_OUTPUT_TOKENS`, `JARVIS_HISTORY_TURNS` | Router, Modellwahl, Kontextgröße |
| Stimme | `TTS_CHAIN`, `TTS_ANNOUNCE_SWITCH`, `FISH_AUDIO_*`, `ELEVENLABS_*`, `AZURE_*`, `GOOGLE_TTS_*`, `EDGE_*`, `LOCAL_TTS_URL` | Anbieter-Reihenfolge und Zugänge |
| Hören | `JARVIS_STT`, `WHISPER_LOCAL_*` | Spracherkennung |
| Aktivierungswort | `JARVIS_WAKE` (`off`/`text`/`oww`), `JARVIS_WAKE_*`, `JARVIS_FOLLOWUP_S` | „Jarvis …“ statt Dauer-Mikrofon. Ohne „Jarvis“ wird nichts bearbeitet oder protokolliert; `JARVIS_FOLLOWUP_S` > 0 erlaubt kurz Folgefragen ohne „Jarvis“ (Standard: aus) |
| Von selbst melden | `JARVIS_PROACTIVE`, `JARVIS_QUIET_HOURS`, `JARVIS_EVENT_LEAD_MIN`, `JARVIS_STORAGE_FULL` | Meldungen und Ruhezeiten (Wecker klingeln immer) |
| Listen | `JARVIS_SHOPPING_LIST`, `JARVIS_TODO_LIST` | HA-To-do-Entity, leer = Notiz unter `vault/Erinnerungen/Listen/` |
| Notbetrieb | `JARVIS_FALLBACK` (`groq,gemini,ollama`), `GROQ_API_KEY`, `GROQ_MODEL`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `OLLAMA_URL`, `OLLAMA_MODEL`, `JARVIS_FALLBACK_PRIVATE` | Ersatz-Kette, wenn Claude ausfällt; siehe [Notbetrieb](sprache.md#notbetrieb-ersatz-kette) |
| Sparen | `JARVIS_CACHE_S` (Standard 300, 0 = aus) | Gleiche Lese-Fragen so lange aus dem Zwischenspeicher |
| Pendeln | `JARVIS_HOME_LAT/LON` (sonst Wetter-Koordinaten), `JARVIS_TRAFFIC_FACTOR` (1.25), `JARVIS_COMMUTE_BUFFER_MIN` (5), `JARVIS_LEAVE_WARN_MIN` (5) | Startpunkt, Verkehrszuschlag, Puffer, Vorwarnung |
| PC und Lautstärke | `JARVIS_DUCK` (0.3, 0 = aus), `JARVIS_DUCK_APPS` (spotify.exe), `JARVIS_NIGHT_VOLUME` (0.5) | Musik leiser beim Sprechen, Stimme in den Ruhezeiten; eigene Apps in `config/apps.json` |
| Spotify | `JARVIS_SPOTIFY_CLIENT_ID`, `JARVIS_SPOTIFY_HOLD` (1), `JARVIS_SPOTIFY_HOLD_MAX_S` (120), `JARVIS_SPOTIFY_HOLD_MODE` (leise), `JARVIS_SPOTIFY_QUIET_FACTOR` (0.3), `JARVIS_SPOTIFY_QUIET_PCT` (20) | Bei „Jarvis“ und bei jeder Ansage, auch wenn er sich von selbst meldet, geht die Musik auf dem Gerät des Kontos, das gerade spielt (auch Handy an der Anlage), auf ein Drittel ihrer Lautstärke, höchstens 20 %, und kommt danach auf die alte Lautstärke zurück; hat man sie zwischendurch von Hand verstellt, bleibt sie so. Mit `JARVIS_SPOTIFY_HOLD_MODE=pause` hält sie stattdessen an, ebenso auf Geräten, deren Lautstärke Spotify nicht setzen kann; Musikbefehle gehen übers Konto. Einmalig App auf developer.spotify.com (Redirect URI `http://127.0.0.1:8722/callback`), dann `scripts\spotify_login.py` |
| Aussprache | `config/aussprache.json` | Eigene Einträge, entsteht per Sprache |
| Dienste | `ICLOUD_*`, `MAIL_*`, `HA_*`, `SYNOLOGY_*`, `JARVIS_PI_*`, `GARMIN_*`, `PAPERLESS_*`, `UNIFI_*`, `JELLYFIN_*` | Integrationen; jede ist optional |
| Smarter | `JARVIS_USER_HABITS` (1), `JARVIS_WELLBEING` (1), `JARVIS_WB_SLEEP_LOW/STRESS_HIGH/BATTERY_LOW` (60/50/30), `JARVIS_WB_BATTERY_EVENING` (25), `JARVIS_DEPARTURE_PREP_MIN` (30), `JARVIS_POWER_BUDGET_EUR` (0 = aus), `JARVIS_MAINTENANCE` (1), `JARVIS_WHISPER_HOURS` (= Ruhezeiten), `JARVIS_LATE_FROM` (21), `JARVIS_LATE_VOLUME` (0.8), `JARVIS_FOCUS_WARN_MIN` (10), `JARVIS_FOCUS_DISTRACT`, `JARVIS_SELFCHECK_SECONDS` (25), `JARVIS_PHONE_ACTION_ENTITY` | Gewohnheiten, Garmin-Auslöser, Losfahren, Budget, Wartung, Stimme nach Tageszeit, Fokus, Selbstkontrolle, Handy-Rückkanal |
| Geräte | `config/home.json` | Zuordnung der Dashboard-Felder (Solar, Speicher, Räume, Router, Luft) zu Home-Assistant-Entities, dazu `appliances` (Leistungsmesser für „… ist fertig“) und `device_aliases` (weitere Wörter je Gerät, z. B. „Wohnzimmer“ und „Lampe“ für ein Licht, das in Home Assistant nur „LED“ heißt; greift ohne Neustart nach spätestens 5 Minuten) |
| Routinen | `config/routines.json` | Entsteht per Sprache; Format in `config/routines.example.json` |

> **Wichtig:** `ANTHROPIC_API_KEY` nie setzen. Claude Code würde sonst per API statt über das Abo abrechnen. Der Server entfernt die Variable vorsorglich aus der Umgebung der Kindprozesse.

## Entwicklung und Diagnose

```powershell
# Tests: Router, Timer, Listen, Routinen (Home Assistant wird simuliert, nichts wird geschaltet)
.venv\Scripts\python.exe -m unittest discover -s tests -v

# Token-Messung: Bestandteile, Standard-Anfragen, schlanker Weg
.venv\Scripts\python.exe scripts\measure_tokens.py components
.venv\Scripts\python.exe scripts\measure_tokens.py new

# Was geht noch ans LLM, was hat der Router gelernt?
.venv\Scripts\python.exe scripts\router_report.py 30

# Autostart bei der Anmeldung (Aufgabenplanung, ohne Adminrechte); -Remove entfernt ihn
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1

# Router + schlanken Aufruf ohne Server testen
.venv\Scripts\python.exe scripts\lean_probe.py "Wie geht es dem NAS?"

# Testinstanz neben der laufenden (eigener Port, eigene Zustandsdatei)
set JARVIS_PORT=8721& set JARVIS_STATE=%TEMP%\state_test.json& .venv\Scripts\python.exe server.py
```

| Symptom | Ursache / Lösung |
|---|---|
| Live-Button tut nichts | `F5`. Bei „Mikrofon blockiert“ in der Adressleiste das Mikrofon freigeben. |
| Wird mitten im Satz abgeschnitten | `endpoint.base_ms` / `unfinished_extra_ms` in `config/voice.yaml` erhöhen (siehe Tuning-Protokoll); ohne Engine: `SILENCE_MS` in `ui/app.js` |
| Stockt beim Sprechen, ohne dass jemand reinredet | `vad.barge_threshold` in `config/voice.yaml` erhöhen; ohne Engine: `BARGE_FACTOR` in `ui/app.js` |
| HUD meldet keine Engine („Sprach-Engine aus“ im Startbanner) | Port 8724 belegt oder `websockets` fehlt: `pip install websockets pyyaml`; `engine.enabled` in `config/voice.yaml` |
| Keine Stimme | Feld **STIMME** im Dashboard zeigt gesperrte Anbieter und den Grund |
| GPU-Stimme startet nicht | `logs/local_tts.log`; der erste Start lädt ~3 GB |
| Hoher Verbrauch | `logs/usage.jsonl` nach Route, Modell und Gruppen auswerten |
| Was ist schiefgelaufen? | `logs/jarvis.log` (JSON-Zeilen, Warnungen und Fehler aller Module; `JARVIS_LOG_DEBUG=router` für ein Modul auf DEBUG) und `http://localhost:8720/metrics` (Zähler je Route, Karte, Job, Modul) |
| Reagiert nicht auf „Jarvis“ | `JARVIS_WAKE` prüfen. Bei `oww` zeigt das Startbanner, ob openWakeWord läuft; sonst gilt die Text-Erkennung. `JARVIS_WAKE_THRESHOLD` senken. |
| Meldungen nur angezeigt, nicht gesprochen | Ruhezeit (`JARVIS_QUIET_HOURS`) |
| Notbetrieb meldet sich nicht | Tooltip auf „Brain“ in der Kopfzeile zeigt je Anbieter den Grund (Key fehlt, Limit bis …, Ollama aus). Ollama: `ollama pull qwen2.5:7b-instruct` |
| Jarvis startet ständig neu | `logs/crash.log`; nach fünf Abstürzen in zehn Minuten hört der Supervisor auf |
| Falsche Fahrzeit | `JARVIS_HOME_LAT/LON` auf die Wohnadresse setzen (Standard sind die Wetter-Koordinaten) |
| Gelernte Formulierung falsch | Eintrag in `config/learned.json` löschen |
