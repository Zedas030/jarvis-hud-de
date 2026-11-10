[← Zurück zum README](../README.md)

# Architektur und Effizienz

Die Komponenten, warum ein Aufruf so wenige Tokens kostet, und die Projektstruktur.

## Architektur

Das Bild dazu steht im [README](../README.md#architektur). Hier die Komponenten im Einzelnen:

| Komponente | Aufgabe |
|---|---|
| `server.py` | HTTP-Server (nur `127.0.0.1`), HUD, API-Endpunkte, Bestätigungskarten, Verbrauchsprotokoll, Selbst-Neustart bei Codeänderungen |
| `router.py` | Intent-Router: lokale Handler, Wahl der Werkzeuggruppen und des Modells |
| `runtime.py` | Startet `claude -p` schlank (`--system-prompt`, `--tools`, `--strict-mcp-config`, `--setting-sources ""`, `--no-session-persistence`, `--effort`) mit vorgewärmtem Prozess-Pool |
| `commands.py` | Prompts, Gesprächsfenster, Zustandsdatei, Slash-Befehle |
| `jarvis_mcp.py` | MCP-Server mit allen Werkzeugen in Gruppen, Bestätigungs-Schranke, Ergebnis-Obergrenze |
| `memory.py` | Vault → Wissensgraph, Kern-Erinnerungen, Protokoll, Aufräumen |
| `voice.py` · `tts_chain.py` | Spracherkennung und Sprachausgabe-Kette mit Adaptern |
| `local_tts_server.py` | Lokale GPU-Stimme (Chatterbox Multilingual) in eigener Umgebung |
| `briefing.py` · `dashboard.py` | Tagesbriefing, Netzwerk-Wächter, HUD-Daten |
| `icloud_calendar.py` · `billing.py` | CalDAV-Kalender, API-Guthaben (Cost API) |
| `jarvis_profile.py` | Profil und Geräte-Zuordnung aus `.env` und `config/home.json` |
| `scheduler.py` | Timer, Wecker, Erinnerungen und geplante Befehle: deutsche Zeitangaben lesen, klingeln lassen |
| `lists.py` · `routines.py` | Einkaufs-/To-do-Liste (HA-To-do oder Vault-Notiz), Routinen aus `config/routines.json` |
| `events.py` · `watchers.py` | Push-Kanal zum HUD (Server-Sent Events, Ruhezeiten) und die Regeln für Meldungen von selbst |
| `wakeword.py` | Aktivierungswort: Text-Erkennung oder openWakeWord am Mikrofon des PCs |
| `voice_engine.py` · `voice_ws.py` | Full-Duplex-Sprach-Engine: Zustandsmaschine, Silero VAD, adaptives Satzende, Barge-in, Fortsetzen; WebSocket für Browser und Satelliten (`config/voice.yaml`) |
| `fallback_llm.py` | Notbetrieb: Ersatz-Kette Groq → Gemini → Ollama mit Jarvis-Werkzeugen |
| `learning.py` · `answer_cache.py` | Gelernte Formulierungen (`config/learned.json`), Verbrauchsbericht, Antwort-Zwischenspeicher |
| `commute.py` · `parcels.py` | Fahrzeit zu Terminen (Nominatim + OSRM), Paketstatus aus Versandmails |
| `pc.py` · `pronounce.py` | Lautstärke, Apps, Bildschirmsperre, Spotify-Ducking (pycaw); Aussprache-Lexikon |
| `spotify.py` | Spotify-Web-API: Pause beim Aktivierungswort, danach weiter; play/pause/next auf jedem Gerät |
| `ui/` | HUD (Vanilla JS, 3D-Wissensgraph ohne Abhängigkeiten) |

## Effizienz: Router und schlanker Modus

Ein Standard-Aufruf von Claude Code schickt bei jeder Anfrage ~35.000 Tokens Grundlast mit:

| Bestandteil | Tokens |
|---|---|
| Eingebaute Werkzeuge | ~19.700 |
| Standard-Prompt | ~3.400 |
| Konnektoren | ~2.000 |
| Projekt-Prompt | (variabel) |

Dazu kommt bei jedem Werkzeug ein zusätzlicher Zwischenschritt.

**Messung** (Input-Tokens pro Anfrage, `scripts/measure_tokens.py`):

| Anfrage | Standard | JARVIS | Weg |
|---|---:|---:|---|
| Uhrzeit | 34.575 | **0** | lokal |
| Energiestatus | 110.515 | **0** | lokal |
| Wetter | 194.243 | **0** | lokal |
| Frage ans Gedächtnis | 34.999 | **3.981** | Haiku |
| Postfach | 110.851 | **9.591** | Haiku |
| Web-Recherche | 151.973 | **10.827** | Sonnet |

**Bausteine:**
- **Intent-Router** mit Regeln statt LLM. Fragen und Befehle werden getrennt, geschützte Geräte nie automatisch ausgeschaltet.
- **Eigener Prompt** (~1.300 Tokens): Stabiles steht vorn, damit der Prompt-Cache greift. Dynamisches (Uhrzeit, letzte Wortwechsel, passende Notizen) wandert in die Nachricht.
- **Werkzeuggruppen** (`memory`, `web`, `home`, `mail`, `calendar`, `systems`, `health`, `music`, `pc`): Pro Anfrage werden nur die passenden geladen, direkt und ohne Tool-Search.
- **Zustandslos:** kein wachsender Sitzungsverlauf, sondern ein gleitendes Fenster der letzten Wortwechsel.
- **Ergebnis-Obergrenze** pro Werkzeug, Haiku/Sonnet-Auswahl, `--effort low`, gedeckelte Antwortlänge.
- **Hintergrundjobs ohne LLM:** Dashboard, Netzwerk-Wächter, Briefing-Fakten.

Jede Anfrage landet in `logs/usage.jsonl` (Route, Modell, Gruppen, Input/Cache/Output, API-Aufrufe).

## Projektstruktur

```
├─ server.py           Start (Supervisor + HTTP-Server), lädt paths.py
├─ paths.py            macht die Ordner unter jarvis/ importierbar (Importnamen bleiben kurz)
├─ jarvis/
│  ├─ core/            router · runtime · commands · events · scheduler · background · jarvis_profile ·
│  │                   personality · context · tasks · focus
│  ├─ safety/          policy · privacy · audit · emergency · proposals
│  ├─ tools/           jarvis_mcp (Werkzeug-Server) · desktop · infra · assistant · pc · lists · routines ·
│  │                   devices · dashboard · commute · parcels · icloud_calendar · billing · paperless ·
│  │                   unifi · jellyfin · phone_actions · power_costs
│  ├─ memory/          memory · memory_tools · memory_intel · learning · skill_factory · answer_cache ·
│  │                   reflection · docsearch · episodes · selfcheck
│  ├─ voice/           voice · tts_chain · local_tts_server · wakeword · pronounce · vision · daytime ·
│  │                   voice_engine · voice_ws · voice_vad · voice_endpoint · voice_stt · voice_tts ·
│  │                   voice_dsp · voice_wake · voice_state · voice_metrics · voice_config
│  ├─ llm/             fallback_llm · resources
│  └─ watch/           watchers · homelab_watch · monitoring · briefing · weekly_review · suggestions ·
│                      user_habits · wellbeing · departure · maintenance
├─ ui/                 index.html · app.js · voice-client.js · graph.js · styles.css · pair.html
├─ scripts/            measure_tokens.py · lean_probe.py · mail_to_trash.py · router_report.py ·
│                      install-autostart.ps1 · ocr_screen.ps1
├─ tests/              ein Testmodul pro Bereich, isolate.py zuerst (ohne Haus, Claude, Netz und Ton)
├─ config/             *.example.json (eigene Dateien bleiben lokal) · voice.yaml (Sprach-Engine)
├─ docs/              Handbuch in Kapiteln (funktionen · installation · bedienung · sprache · gedaechtnis ·
│                      architektur · sicherheit) · screenshots/
├─ pi/                 Monitoring und Doku des Raspberry Pi (anonymisierte Kopien im veröffentlichten Stand)
├─ CLAUDE.md · ARCHITECTURE.md · SECURITY.md · ROADMAP.md · BESTANDSAUFNAHME.md
├─ requirements.txt · requirements-gpu.txt · requirements-windows.txt · requirements-tts.txt · requirements-dev.txt
└─ start-jarvis.bat
```
