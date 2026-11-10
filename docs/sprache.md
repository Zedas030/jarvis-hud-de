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
| `barge` | `duck_ms`, `duck_gain`, `confirm_ms`, `fade_ms`, `clip_silence_ms`, `resume_after_ms`, `backchannel`, `stop_words`, `echo_text_ratio`, `sonos_duck_via_ha` |
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
