[← Zurück zum README](../README.md)

# Stimme und Sprach-Engine

Die Anbieter-Kette für die Sprachausgabe, die Sprach-Engine zum Zuhören, Unterbrechen und Weitersprechen und der Notbetrieb ohne Claude.

## Sprachausgabe: Anbieter-Kette

```
Fish Audio ─▶ ElevenLabs ─▶ Lokal (Chatterbox GPU · Piper CPU) ─▶ Azure ─▶ Google ─▶ Edge
```

- **Einheitliches Interface:** Jeder Anbieter ist ein Adapter mit `configured()`, `synth()` und `balance()`. Die Reihenfolge steht in `TTS_CHAIN`.
- **Erschöpft** (402, Quota-/Credit-Fehler, ungültiger Key, Guthaben-API meldet 0): Der Anbieter ist bis zum Reset gesperrt, und das übersteht auch Neustarts. Als Reset gilt das Datum laut API, bei Free Tiers der Monatsanfang, bei ungültigem Key 6 h, sonst 24 h. Danach steht er automatisch wieder an seinem Platz.
- **Vorübergehend** (Netzwerk, Timeout, 5xx): Nur der aktuelle Satz wechselt den Anbieter und wird vollständig neu erzeugt. Es entsteht kein hörbarer Abbruch.
- **Transparenz:** Das Dashboard zeigt den aktiven Anbieter mit Restguthaben und die gesperrten Anbieter samt Datum. Beim Wechsel gibt es optional eine kurze Ansage.
- **Ton-Tags** wie `[dry]` werden von Anbietern umgesetzt, die sie beherrschen, und für alle anderen entfernt.
- Die Kette ist reiner Code, sie verbraucht keine LLM-Tokens.

## Sprach-Engine: Zuhören, Unterbrechen, Weitersprechen

Seit 02.11.2026 läuft das Gespräch nicht mehr im Browser, sondern in einer **Full-Duplex-Engine auf dem
Server** (`jarvis/voice/voice_engine.py`). Der Browser schickt das Mikrofon als 20-ms-Frames über einen
WebSocket (`ws://127.0.0.1:8724`) und spielt zurückkommende Sätze ab; alles andere (Sprachaktivität,
Satzende, Unterbrechen, Weitersprechen, Modellaufruf) entscheidet der Server. Dieselbe Schnittstelle
bekommt später ein Raum-Satellit auf dem Linux-Server. Die Analyse, die zu dem Umbau führte, steht in
[`VOICE-ANALYSE.md`](../VOICE-ANALYSE.md).

**iPad und iPhone (seit 10.11.2026):** Gekoppelte Geräte nutzen die Engine auch. Die Engine hört nur auf
127.0.0.1, und `tailscale serve` leitet nur den Remote-Port weiter; deshalb reicht der Remote-HUD einen
WebSocket auf `wss://<pc>.ts.net/voice-ws` Byte für Byte an die Engine durch (`server._voice_tunnel`), nur für
gekoppelte Geräte mit passendem Origin. Die Engine prüft danach wie am PC das Seiten-Token und erkennt das Gerät
am Cookie. An der Tailscale-Konfiguration ändert sich nichts. Zurück zum alten Weg: `engine.remote: false` in
`config/voice.yaml`.

### Zustandsmaschine

```
IDLE → LISTENING → THINKING → SPEAKING → ( INTERRUPTED → LISTENING | RESUMING → SPEAKING ) → IDLE
```

Jeder Wechsel steht mit Zeitstempel und Auslöser in `logs/voice_state.jsonl` und im HUD-Protokoll. Das
Mikrofon bleibt in SPEAKING offen.

| Zustand | Was passiert |
|---|---|
| LISTENING | **Silero VAD** (ONNX, ~0,1 ms je Frame) erkennt Sprache; alle ~0,7 s ein **Partial** aus Whisper (nur auf der GPU). Das Satzende ist **adaptiv**: fertiger Satz 450 ms, Frage 350 ms, unfertig („und dann, ähm,“) 1350 ms, ohne Text 700 ms; wer nach dem Senden weiterspricht, lehrt die Engine, 100 ms länger zu warten (bis +600 ms). Danach Whisper mit Beam 5, Aktivierungswort-Prüfung, Anfrage an `/api/run` (Router, Cache, Claude, Notbetrieb wie bisher). |
| THINKING | Sprache kurz nach dem Senden: der laufende Aufruf wird abgebrochen, beide Hälften werden zusammengeklebt. Nach 1,2 s ohne erstes Wort ein leiser Denk-Ton. |
| SPEAKING | Sätze entstehen aus dem Token-Strom (`voice_tts.py`), der erste Satz schon am ersten Komma („Guten Abend, Sir,“). Jeder Satz wird **einmal** synthetisiert und behalten. Reinreden: nach 60 ms Sprache Lautstärke auf 20 %, nach 150 ms Pause → INTERRUPTED (gemessen 143–218 ms bis zur Pause). |
| INTERRUPTED | Der Clip wird transkribiert und eingeordnet: **Geräusch / eigenes Echo** → weiter; **Rückmeldung** („mhm“, „ja“, „genau“) → weiter; **Stopp-Wort** → Ende; **ein Wort ohne Namen** → weiter; **echte Äußerung** → neue Anfrage, mit dem Hinweis „unterbrochen nach Satz N von M“ im Kontext; **nichts gesagt** nach 1,5 s → weiter. |
| RESUMING | Der unterbrochene Satz läuft vom Satzanfang, danach der Rest, ohne neue Synthese. |

Ins Gesprächsfenster (`state.json` → `window`) kommt bei einer Unterbrechung nur der **tatsächlich
gesprochene** Teil plus `[unterbrochen]`, nicht der geplante Rest.

### Gemessen (dieser PC, RTX 5080, Piper als Stimme, Router-Antwort)

| Stufe | Wert |
|---|---|
| Ende der Äußerung bis Satzende gilt | 350–470 ms |
| Whisper `large-v3-turbo` final | 130–160 ms |
| Router | 7–20 ms |
| Piper, erster Ton | 130–160 ms (2,2 s beim allerersten Satz: Modell laden) |
| **Ende der Äußerung bis erstes Wort** | **≈ 0,8–0,9 s** (Router), bei Claude plus dessen erster Satz (1–3 s) |
| Reinreden bis Pause | 143–218 ms |

`scripts/voice_scenarios.py` spielt die Aufnahmen aus `tests/fixtures/voice/` durch die echte Engine
(Silero, Whisper, Stimme) mit einem Fake-Modell und druckt Zustände, Transkripte und Metriken.

### Konfiguration

Alles steht kommentiert in [`config/voice.yaml`](../config/voice.yaml) (PyYAML; `JARVIS_VOICE_CONFIG` zeigt
auf eine andere Datei; fehlende Schlüssel fallen auf die Standardwerte in `voice_config.py` zurück):

| Block | Wichtigste Werte |
|---|---|
| `engine` | `enabled` (false = alte Browser-Pipeline), `bind`/`port` (8724), `preroll_ms`, `debug_audio` (Segmente als WAV nach `logs/voice_debug/`) |
| `vad` | `start_threshold` 0,55 (Nutzer startet), `barge_threshold` 0,70 (Nutzer unterbricht), `min_speech_ms`, `max_speech_ms` |
| `endpoint` | `base_ms`, `unfinished_extra_ms`, `question_bonus_ms`, `no_text_ms`, `max_ms`, `learn_step_ms`/`learn_max_ms`, `continue_window_ms`, `partial_every_ms`, `turn_model` (optionales ONNX-Turn-Modell) |
| `stt` | `partials` (auto/true/false), `final_beam`, `vocabulary` (wird mit `WHISPER_LOCAL_PROMPT` und den Raum-, Geräte- und Lautsprechernamen aus `config/home.json` zu Whispers Prompt) |
| `wake` | `conversation_s` (Konversationsmodus nach jeder Antwort, ×1,6 nach einer Frage), `window_s`, `barge_needs_name_with_ambience` |
| `barge` | `duck_ms`, `duck_gain`, `confirm_ms`, `fade_ms`, `clip_silence_ms`, `resume_after_ms`, `resume_skip_ratio`, `backchannel`, `stop_words`, `echo_text_ratio`, `sonos_duck_via_ha` |
| `tts` | `first_chunk_min_chars`/`first_chunk_max_chars`, `min_chunk_chars`, `gap_ms`, `provider_timeout_s` |
| `llm` | `thinking_cue_after_ms`, `first_word_target_ms` |
| `metrics` | `file` (`logs/voice_metrics.jsonl`), `prometheus` (GET `/metrics`) |

Einmalig: `.venv\Scripts\python.exe scripts\fetch_voice_models.py` lädt Silero VAD nach `models/`
(ohne die Datei läuft ein Pegel-VAD). Die Engine braucht `websockets` und `pyyaml` (`requirements.txt`).

### Metriken

`logs/voice_metrics.jsonl` hat je Gespräch eine Zeile (`wait_ms`, `stt_ms`, `llm_first_ms`,
`first_sentence_ms`, `tts_first_ms`, `first_word_ms`, `barge_react_ms`, `interrupted`, `spoken`).
`GET http://localhost:8720/metrics` liefert dasselbe als Prometheus-Text (Histogramme, Zähler
`jarvis_voice_barge_ins_total{outcome="…"}`, `jarvis_voice_echo_false_alarms_total`,
`jarvis_voice_user_continued_total`, `jarvis_voice_late_first_word_total`) für Grafana auf dem Pi;
`GET /api/voice/metrics` als JSON fürs HUD.

### Lautsprecher im Raum

| Ausgabe | Unterbrechen | Fortsetzen |
|---|---|---|
| PC / iPad selbst | 150 ms bis Pause (Ducking ab 60 ms), Browser-Echo-Unterdrückung kennt den eigenen Ausgang | am Satzanfang, ohne neue Synthese |
| Sonos | Stream wird gekappt, die Lautstärke über Home Assistant gesenkt; der Sonos puffert selbst 0,5–1,5 s, also ≈ 1 s bis Stille. Das Echo der eigenen Stimme filtert ein Pegelmodell plus Textabgleich (`EchoGuard`); ein speexdsp-Canceller wird benutzt, wenn das Paket installiert ist (nicht getestet) | am Satzanfang, die Durchsage startet neu |
| Echo (Alexa) | Alexa-TTS ist weder stopp- noch referenzierbar: nur eine Äußerung **mit „Jarvis“** zählt | – |

### Tuning-Protokoll

Erst `logs/voice_state.jsonl` und `logs/voice_metrics.jsonl` ansehen (oder
`scripts/voice_scenarios.py`), dann **einen** Wert ändern, Datei speichern (sie wird beim nächsten
Gespräch neu gelesen), zehn Sätze sprechen.

| Symptom | Wert | Richtung |
|---|---|---|
| Antwortet mitten in meinem Satz | `endpoint.base_ms` | +100 ms; bei Denkpausen `unfinished_extra_ms` +300 ms; prüfen, ob `partials` laufen (HUD meldet „Partials“ beim Start, sonst gilt `no_text_ms`) |
| Antwortet zu spät | `endpoint.base_ms` | −50 ms, `question_bonus_ms` −50 ms; `learn_max_ms` begrenzen |
| Hört Satzanfänge nicht | `vad.start_threshold` | 0,55 → 0,45; `engine.preroll_ms` +200 |
| Reagiert auf Husten, Tastatur, Fernseher | `vad.start_threshold` | → 0,65; `vad.min_speech_ms` → 300 |
| Stockt beim Sprechen ohne Reinreden | `vad.barge_threshold` | 0,70 → 0,80; `barge.confirm_ms` → 250; bei Sonos `barge.echo_text_ratio` → 0,5 |
| Reinreden wird nicht bemerkt | `vad.barge_threshold` | → 0,60; `barge.confirm_ms` → 120 |
| Nach „mhm“ hört er auf | `barge.backchannel` | das Wort ergänzen (so wie Whisper es schreibt, siehe `barge_text` im HUD-Protokoll) |
| Wartet nach einer Unterbrechung zu lange / zu kurz | `barge.resume_after_ms` | 1500 ± 500 |
| Sagt einen Satz doppelt, nachdem er sich selbst gehört hat | `barge.resume_skip_ratio` | 0.6 (kleiner = öfter weiter mit dem nächsten Satz) |
| Knackt bei Pause/Weiter | `barge.fade_ms` | 30 → 60 |
| Erster Ton kommt spät | `tts.first_chunk_max_chars` | 70 → 50; Stimme in `TTS_CHAIN` prüfen (Piper streamt, Fish streamt, Chatterbox nicht) |
| Versteht Fachbegriffe falsch | `stt.vocabulary` | Begriff ergänzen (oder `WHISPER_LOCAL_PROMPT`) |
| Spricht ohne „Jarvis“ bei Fernsehen los | `wake.barge_needs_name_with_ambience` | true lassen; `JARVIS_WAKE=Jarvis` |

## Notbetrieb: Ersatz-Kette

Ist das Claude-Kontingent erschöpft, startet die CLI nicht oder bricht ein Aufruf ab, bevor ein Wort gesagt ist, antwortet die erste verfügbare Stufe dieser Kette:

| Stufe | Modell | Kosten | Hinweis |
|---|---|---|---|
| **Groq** | gpt-oss-120b (`GROQ_MODEL`) | Gratis-Tarif, Key auf console.groq.com | sehr schnell, keine Nutzung der Daten fürs Training |
| **Gemini** | Gemini 3.5 Flash (`GEMINI_MODEL`) | Gratis-Tarif, Key auf aistudio.google.com | Google darf Gratis-Anfragen fürs Training nutzen, deshalb **nie** bei Mail, Kalender, Gesundheit oder dem Tagesbriefing |
| **Ollama** | `qwen2.5:7b-instruct` lokal | kostenlos, offline | letzter Rettungsanker |

- Die Ersatz-Modelle bekommen **dieselben Jarvis-Werkzeuge** wie Claude (die Gruppen, die der Router wählt). `jarvis_mcp.py` läuft dafür als Kindprozess, und was bei Claude eine Bestätigungskarte braucht (z. B. `calendar_add`), braucht sie auch hier. Nicht verfügbar sind nur `pc` (PowerShell) und der volle HA-MCP-Server.
- Meldet ein Anbieter sein eigenes Limit (HTTP 429), wird er bis zur genannten Wartezeit übersprungen; ein ungültiger Key oder ein nicht mehr angebotenes Modell für sechs Stunden (Grund im Tooltip).
- Hat ein Anbieter schon ein Werkzeug benutzt und fällt dann aus, übernimmt **kein** zweiter, damit nichts doppelt geschaltet wird.
- Anbieter ohne Key werden einfach übersprungen. `JARVIS_FALLBACK=ollama` stellt das alte Verhalten wieder her, `off` schaltet den Notbetrieb ab.
- `JARVIS_FALLBACK_PRIVATE` (Standard `groq,ollama`) legt fest, wer private Anfragen sehen darf.
- Im Verbrauchsprotokoll stehen die Antworten als `fallback:groq`, `fallback:gemini` bzw. `fallback:ollama`.

```
GROQ_API_KEY=...
GEMINI_API_KEY=...
```

## Home Assistant Voice PE

Der Lautsprecher mit Mikrofon von Nabu Casa ist nur das Ohr im Raum. Er hört das Aktivierungswort selbst („Hey Jarvis“) und schickt das Gesagte über Home Assistant an den PC. Dort erkennt Whisper auf der Grafikkarte den Text, Jarvis beantwortet ihn und spricht die Antwort mit seiner eigenen Stimme über den Lautsprecher des Raums. Home Assistant ist nur der Bote, weil der Voice PE ohne Home Assistant nicht arbeitet. So läuft es ab:

```
Voice PE ──"Hey Jarvis"──▶ Home Assistant (Pi) ──Audio──▶ /api/hear auf dem PC: Whisper large-v3-turbo auf der GPU ──▶ Text
                              └──Text──▶ /api/ask: Router, Claude, Werkzeuge ──▶ Antwort auf dem Lautsprecher des Raums (Sonos/Echo)
PC aus: Whisper auf dem Pi ──▶ Home Assistants eigener Agent („Licht aus“, Timer) ──▶ Piper ──▶ Voice PE
```

- **Was Jarvis kann:** alles, was er im HUD kann, auch „Licht aus“ (sein Router, 0 Tokens). Er bekommt den Raum mit, dem der Voice PE in Home Assistant zugeordnet ist. Hat der Raum keinen Lautsprecher, liest der Voice PE die Antwort selbst vor (Piper).
- **In welchem Raum er antwortet (seit 19.11.2026, `room_scope.answer_room`):** dort, wo du bist, nicht unbedingt beim Satelliten, der dich gehört hat. Sieht genau ein Präsenzmelder jemanden (`room_scope.sensors` in `config/home.json`, Bad zählt zum Schlafzimmer), antwortet dieser Raum. Sehen mehrere Räume jemanden, antwortet der Raum des Satelliten (sein Bereich in HA). Sieht gerade keiner jemanden (still sitzen, Radar-Lücke), nimmt er den Raum, den HA zuletzt für dich hält (`input_select.nutzer_raum`), und erst ohne den den Raum des Satelliten. Derselbe Raum gilt dann auch für Befehle ohne Raum („Licht an“). Anlass: Der ReSpeaker gehört zum Schlafzimmer, stand aber im Wohnzimmer und antwortete immer im Schlafzimmer. Mit `JARVIS_ROOM_SCOPE=0` gilt wieder nur der Raum des Satelliten.
- **„Jarvis, stopp“:** Schon das Aktivierungswort stoppt, was er sagt. „Stopp“, „Hör auf“, „Sei still“, „Ruhe“ oder „Das reicht“ allein erledigt der Router dann lokal und ohne Antwort (`router._stop_talk`, antwortet als stilles Ergebnis; `remote_ask` spricht stille Ergebnisse nicht aus). Vorher ging ein bloßes „Stopp“ an Claude.
- **Wie schnell:** Jarvis spricht den ersten Satz, sobald er fertig ist, und hängt jeden weiteren an dieselbe Durchsage an, während Claude noch schreibt, wie am PC (`remote_ask.RoomVoice`, derselbe Satz-Splitter wie die Sprach-Engine). Gegenüber dem Mikrofon am PC kommen etwa 1 s dazu: Home Assistant wartet nach dem letzten Wort fest ca. 0,7 s (am PC lernt die Engine 0,35–0,45 s), und der Sonos braucht einen Moment, bis die Durchsage anläuft. Unterbrechen geht über das Aktivierungswort: Sobald ein Satellit zuhört, hört Jarvis überall auf zu sprechen, am PC, auf dem Sonos und auf dem Echo (`satellite_quiet.py`, `JARVIS_SATELLITE_HUSH`). Den Echo stellt er dafür kurz auf Lautstärke 0, weil sich Alexas Vorlesen nicht abbrechen lässt; danach kommt die alte Lautstärke zurück. Sonst hätte das Mikrofon seine eigene Antwort mit aufgenommen. Etwa 1 s von ihm kann am Anfang noch drin sein. Scheitert ein Satz auf dem Lautsprecher, liest der Voice PE den Rest selbst vor.
- **Fernseher und YouTube:** Der Voice PE kann Nutzer' Stimme nicht vom Fernseher unterscheiden. Lief YouTube oder der TV laut, fand er das Satzende nicht, hörte bis zur Grenze von Home Assistant (15 s) zu, und Whisper schrieb den Fernseher mit. Darum dreht Jarvis, solange ein Wohnzimmer-Satellit zuhört, alle Programme des PCs außer sich selbst auf 10 % ihrer Lautstärke (pro Programm, die Gesamtlautstärke bleibt) und schaltet die Soundbar stumm (Samsung-Multiroom-API, `JARVIS_SOUNDBAR_HOST`). Hört der Satellit auf zuzuhören, ist alles wie vorher, spätestens nach 20 s; eine Soundbar, die Nutzer selbst stummgeschaltet hat, bleibt stumm (`listen_duck.py`, `JARVIS_LISTEN_DUCK`). Läuft der Ton des Fernsehers über seine eigenen Lautsprecher, kann Jarvis ihn nicht leiser machen: Der Samsung von 2015 ist nicht in Home Assistant.
- **Rückfragen ohne Aktivierungswort:** Endet Jarvis' Antwort mit einer Frage (auch „Meinen Sie X? Falls ja, …“), wartet er, bis der Echo oder Sonos fertig ist, und ruft dann `assist_satellite.start_conversation` für den Satelliten auf, der gefragt hat. Der Voice PE spielt eine Viertelsekunde Stille (`www/jarvis/stille.wav`, er sagt nie etwas) und hört ohne Aktivierungswort zu; kommt 5 s nichts, schließt er wieder. Home Assistants eigenes `continue_conversation` geht hier nicht: Es würde das Mikro öffnen, während Jarvis auf dem Echo noch spricht (`satellite_followup.py`, `JARVIS_SAT_FOLLOWUP`). Beginnt die Antwort auf eine Rückfrage mit Ja oder Nein, nimmt Jarvis das als Zustimmung oder Ablehnung, auch wenn Whisper den Rest verhört.
- **Warum auf dem PC:** gemessen am 17.11.2026 mit denselben fünf Sätzen. Über Home Assistant, Tailscale und die GPU braucht ein Satz 0,16 bis 0,29 s, Whisper auf dem Pi (base-int8, Raspberry Pi 4) 7 bis 17 s und verhört sich öfter („Spotify“ → „Sportdiefie“, „Nacht“ → „nach“).
- **Aus Fehlauslösungen lernen (seit 22.11.2026, `wake_learn.py`):** Wacht ein Satellit auf, ohne dass du ihn gerufen hast, und du sagst „Stopp“ (auch „Hör auf“, „Schon gut“ …), merkt Jarvis sich das als Fehlauslösung, sofern er in dem Moment nichts gesagt hat, was das Aufwachen hätte abbrechen können. Brichst du mit „Jarvis, stopp“ eine Antwort ab, zählt das nicht, und ein Mikrofon, das Jarvis für eine Rückfrage selbst geöffnet hat, auch nicht. Sagt nach dem Aufwachen niemand etwas oder war das Gesagte nicht an ihn gerichtet (IGNORIEREN), ist es nur eine vermutete Fehlauslösung. Die Satelliten erkennen das Wort auf dem Gerät: Einen Score und den Ton des Auslösemoments gibt es in Home Assistant nicht. Gespeichert wird je Fall eine Zeile in `logs/wake_false.jsonl` (Zeit, Satellit, Raum, eingestellte Empfindlichkeit, was lief: TV, Musik, Ton am PC; nie die Worte) und dazu in `logs/wake_false/` der Ton: `…-after.wav` ist, was der Satellit nach dem Aufwachen gehört hat, `…-trigger.wav` sind die letzten 6 s davor vom PC-Mikrofon, wenn der Satellit im Raum des PCs steht (`JARVIS_WAKE_LEARN_PC_ROOM`; der Ton liegt bis dahin nur im Arbeitsspeicher). Für „nicht an mich gerichtet“ wird kein Ton gespeichert. Daraus folgt zweierlei. **Empfindlichkeit:** Nach 3 sicheren Fehlauslösungen eines Satelliten in 7 Tagen (gezählt seit der letzten Änderung der Empfindlichkeit, `JARVIS_WAKE_LEARN_PROPOSE`) schlägt Jarvis vor, ihn eine Stufe unempfindlicher zu stellen; das passiert erst nach deinem Ja auf der Karte, und bei Nein fragt er eine Woche nicht wieder. **Modell:** `scripts\wake_negatives.py --apply` kopiert die Clips als Gegenbeispiele ins Trainingsset in WSL (`data/samples/jwfalse_trig_1` und `jwfalse_room_1`); einen Auslöser-Clip, in dem Whisper „Jarvis“ hört, lässt es aus. Trainiert wird von Hand mit `tools/07k_jw2.sh` (ab 20 Clips, jw2 aus jw1; noch nie gelaufen), danach wie bisher vergleichen (`tools/12j_eval_jw.sh`), Firmware bauen und flashen. „Wie oft wurdest du falsch ausgelöst?“ beantwortet Jarvis lokal. Aus: `JARVIS_WAKE_LEARN=0`. Am PC selbst gibt es kein trainiertes Aktivierungswort (Whisper plus Namensprüfung); dort ist ein „Stopp“ immer das Abbrechen einer Antwort und wird nicht gezählt.
- **PC aus:** Die Integration merkt das beim Aufwärmen, schon während du sprichst. Dann bekommt Whisper auf dem Pi dasselbe Audio, und Home Assistants eigener Agent schaltet Licht und stellt Timer. Für alles andere sagt der Voice PE: „Jarvis ist gerade nicht erreichbar, der PC ist wohl aus.“ Das ist langsam, aber es geht.
- **Freigabe-Karten** (Termin eintragen, Befehl am Pi) lehnt Jarvis vom Voice PE aus sofort ab und sagt das. Das geht nur am PC oder im HUD.

**Bausteine im Repo**

| Datei | Wohin | Was |
|---|---|---|
| `ha/custom_components/jarvis/` | `custom_components/jarvis` im HA-Konfigordner | Spracherkennung `stt.jarvis` (`/api/hear`, Rückfall auf Whisper am Pi) und Gesprächsagent `conversation.jarvis` (`/api/ask`, Rückfall auf den Agenten von Home Assistant), beide mit demselben Schlüssel |
| `pi/voice/compose.yml` | `/opt/assist-voice/compose.yml` auf dem Pi | Reserve für einen ausgeschalteten PC: Wyoming Whisper (Sprache → Text, Port 10300) und Piper (Text → Sprache, Port 10200), beide nur auf 127.0.0.1 |
| `scripts/ha_voice_setup.py` | läuft am PC | verbindet Whisper und Piper mit HA, legt die Pipeline „Jarvis“ an (`stt.jarvis`, `conversation.jarvis`, `prefer_local_intents` aus), setzt das Aktivierungswort |

**Einrichten**

1. Auf dem Pi: `cd /opt/assist-voice && docker compose up -d` (lädt beim ersten Mal etwa 570 MB Images und die Modelle).
2. Home Assistant neu starten, damit es die Integration „Jarvis“ findet.
3. Schlüssel holen (geht am PC): im HUD **📱 Geräte → Neuen Code**, dann im Browser `https://<dein-pc>.<tailnet>.ts.net/pair.html?schluessel` öffnen, Code eingeben, Name „Home Assistant“, **Schlüssel holen**, **Kopieren**. Er wird nur dieses eine Mal gezeigt und steht danach mit 🔑 in der Geräteliste. Er öffnet nur `/api/ask` und `/api/hear`, nie das HUD.
4. In Home Assistant: *Einstellungen → Geräte & Dienste → Integration hinzufügen → Jarvis*. Dort trägst du ein: die Adresse (`https://<dein-pc>.<tailnet>.ts.net`), den Schlüssel und die Tailscale-IP des PCs (`tailscale ip -4 <pc-name>`). Die IP ist nötig, weil der Pi mit `accept-dns` aus den `ts.net`-Namen nicht auflösen kann.
5. Voice PE einstecken. Die Home-Assistant-App am Handy meldet „Neues Gerät gefunden“, dort das WLAN eintragen. Dann in HA dem Gerät seinen **Bereich** geben (z. B. Schlafzimmer).
6. Am PC: `.venv\Scripts\python.exe scripts\ha_voice_setup.py --apply`. Ohne `--apply` zeigt das Skript nur, was fehlt. Nach einem Update der Integration noch einmal starten.
7. Ausprobieren: *„Hey Jarvis, wie gesund ist der Akku?“*, *„Hey Jarvis, Licht aus.“*

**Aktivierungswort doppelt:** Steht der Voice PE im selben Raum wie der PC, hören beide auf „Jarvis“. Dann entweder am Voice PE „Okay Nabu“ wählen (in HA beim Gerät unter *Aktivierungswort*) oder am PC das Aktivierungswort ausschalten (`JARVIS_WAKE`).

**Zweiter Satellit: ReSpeaker Lite im Schlafzimmer (seit 19.11.2026).** Seeed ReSpeaker Lite (XMOS XU316, zwei Mikrofone) mit aufgelötetem XIAO ESP32-S3, nur Mikrofon wie der Voice PE. Firmware auf Basis von [formatBCE/Respeaker-Lite-ESPHome-integration](https://github.com/formatBCE/Respeaker-Lite-ESPHome-integration) (Release `2026.11.2`, braucht ESPHome 2026.11), mit denselben Anpassungen wie am Voice PE: ein Aktivierungswort „Hey / Okay / Hallo Jarvis“ (eigene Modelle hj1 + hj4, melden beide „Hey Jarvis“, damit HAs Doppel-Sperre über alle Satelliten greift), dieselben Schwellen, Abbruch nach 5 s ohne Sprache, Weckton aus. In HA: Gerät „ReSpeaker Schlafzimmer“, Bereich Schlafzimmer, Platz 1 „Hey / Okay / Hallo Jarvis“, Platz 2 „Okay Nabu“, „Very sensitive“, alle eigenen Töne aus. Die Antwort kommt dadurch vom Schlafzimmer-Sonos. Ab 22 Uhr spricht Jarvis dort schon vor „Gute Nacht“ leiser (`JARVIS_SONOS_LATE_VOLUME`, bei Nutzer 0,35 statt 0,63, ab `JARVIS_SONOS_LATE_FROM` bis 7 Uhr), nach „Gute Nacht“ mit der Nachtlautstärke (`JARVIS_SONOS_NIGHT_VOLUME`).

- **Bauen:** in WSL `~/work/hjww/tools/14_fw_rl.sh` (Build in `fw/build/rl`, ESPHome-venv `fw/venv-esphome11` aus `tools/13_esphome11.sh`). Ein Update geht danach per WLAN: `fw/venv-esphome11/bin/esphome upload fw/build/rl/respeaker-schlafzimmer.yaml --device <IP>`.
- **XMOS-Firmware:** Ab Werk lief die I2S-Firmware 1.0.8. Die ESPHome-Firmware hat beim ersten Start selbst 1.1.0 (48 kHz, eigener Kanal fürs Aktivierungswort) eingespielt. Das geht nur ab 1.0.8; bei älterer Firmware vorher per USB-DFU an der Buchse neben der Klinke aktualisieren. Während die LED weiß blinkt, nicht vom Strom nehmen.
- **Antenne:** Die schwarze Folie am Kabel ist die WLAN-Antenne, nicht abreißen. Ohne sie lag das Signal bei −92 dBm, und HA verlor ständig die Verbindung; mit ihr sind es etwa −53 dBm.
- **WLAN neu eintragen** (etwa nach einem Werksreset): Improv über USB oder Bluetooth. Die Taste USR ist nicht auf D2 gelötet, darum fragt Improv BLE nicht nach einem Tastendruck.
- **LED:** lila langsam = hört zu, lila schnell = denkt, rot schnell = Fehler, rot langsam = keine Verbindung zu HA, aus = wartet.
- **Aktivierungswort „Jarvis“ (seit 19.11.2026, Firmware `2026.11.2-jarvis2`):** Mit hj1/hj4 („Hey/Okay Jarvis“) löste der ReSpeaker bei Nutzer kaum aus, „Okay Nabu“ und „Kenobi“ sofort. Nutzer sagt „Dschawis“, also ohne r; die alten Modelle kannten vor allem die englische Aussprache mit r. Darum gibt es jetzt ein eigenes Modell **jw1** für das eine Wort „Jarvis“. Trainiert wurde es mit 77 Aufnahmen von Nutzer (Aufnahmeseite, je 30-fach verfremdet), 13.500 „Dschawis“ von 6 deutschen Piper-Stimmen, 14.000 englischen Sprechern ohne r und Gegenbeispielen wie Davis, Travis, Java, Jawohl oder Dschungel. Startpunkt war hj1. Test auf 8 zurückgehaltenen Aufnahmen von Nutzer bei Schwelle 0,98: 0 von 8 überhört, 0,94 Fehlauslöser pro Stunde Party-Gemurmel (DipCo). hj1 überhörte 6 von 8. In HA: Platz 1 „Jarvis“, Platz 2 „Okay Nabu“, Empfindlichkeit „Moderately“ (0,98; „Slightly“ = 0,99, „Very“ = 0,97). Steht der ReSpeaker im selben Raum wie der PC, wachen beide bei „Jarvis“ auf. Bauen: WSL `tools/07j_jw1.sh` (Training), `tools/12j_eval_jw.sh` (Vergleich), `tools/15_fw_rl_jw.sh` (Firmware, Build `fw/build/rl_jw`).
- **Nach einem Umzug oder Neuaufsetzen von HA (19.11.2026, HA auf den neuen Pi):** Beide Satelliten waren verbunden, aber beide Aktivierungswort-Plätze standen auf „kein“, und keiner reagierte mehr. Danach in HA bei jedem Satelliten die Plätze prüfen und neu setzen (Voice PE: „Hey / Okay / Hallo Jarvis“ + „Okay Nabu“, ReSpeaker: „Jarvis“ + „Okay Nabu“). Rotes langsames Pulsieren am Satelliten heißt: keine Verbindung zu HA. Ursache: HA hatte während des Umzugs „unavailable“ als letzten Zustand der Auswahl gespeichert (`.storage/core.restore_state`), das kann es beim Start nicht wiederherstellen; selbst wählt es nichts, weil die Firmware zwei aktive Wörter meldet. Beim Neustart um 16:09 passierte es noch einmal.

**Schlüssel ändern:** Am PC unter **📱 Geräte** den Eintrag mit 🔑 löschen, einen neuen holen, dann in HA bei der Integration Jarvis *⋮ → Neu konfigurieren* und den neuen Schlüssel eintragen. Den Schlüssel speichert HA in seinen eigenen Konfigurationsdaten (`.storage/core.config_entries`), nicht in einer YAML-Datei.

**Umzug auf einen Heimserver:** Läuft Jarvis später auf eigener Hardware, ändert sich in Home Assistant nur die Adresse der Integration (*⋮ → Neu konfigurieren*). Ohne Grafikkarte dort lieber `small-int8` oder größer nehmen und vorher nachmessen.
