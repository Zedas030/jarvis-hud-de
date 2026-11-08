# Roadmap: Ausbau zum vollständigen Assistenten

Status je Phase der Vorgabe vom 02.11.2026. Deutsch, weil es der Arbeitsplan des Nutzers ist. Die Analyse
steht in `BESTANDSAUFNAHME.md` (Phase 0), die Sprachpipeline in `VOICE-ANALYSE.md`, der Server-Umzug in
`UMBAU-SERVER.md`. Die Liste der kleinen Haken in `docs/funktionen.md` bleibt die Funktionsliste; hier steht der Plan.

Legende: ✅ fertig · 🔄 in Arbeit · ⏸ wartet auf Freigabe · ⬜ offen

| Phase | Status | Stand |
|---|---|---|
| 0 Bestandsaufnahme | ✅ | Bericht `BESTANDSAUFNAHME.md`, freigegeben 02.11.2026. Die sieben Entscheidungen (Abschnitt 11) sind noch offen |
| P0 Fundament | ✅ bis auf Linux-Lauf | 02.11.2026: Tests grün (637), Abhängigkeiten aufgeteilt, `jarvis_log.py` (JSON-Log), `metrics.py` (`/metrics` für alle Module), nicht-UTF-8-Anfragen → 400. Offen: Testlauf unter Linux (kein WSL auf dem PC), Schlüssel rotieren (Nutzer) |
| 1 Sprachpipeline | ✅ Kern · ⬜ Rest | Engine auf dem Server seit 02.11. (Commit `b85eb0d`). Offen: iPad/iPhone an die Engine, Raum-Satellit, Sonos-AEC-Erprobung, RNNoise, Tuning mit echtem Mikrofon |
| 2 Agenten-Kern | ⬜ | Vorhanden: Router, Haiku/Sonnet, Notbetrieb, Aktionsklassen, Audit, Persönlichkeit, Vault, Episoden. Fehlt: Plugin-Registry, YAML-Konfiguration, SQLite-Gedächtnis, Kurzzeit-Zusammenfassung, Sprecher-Erkennung, Trennung privat/beruflich |
| 3 Server & Betrieb | ⬜ | Plan und Hardware in `UMBAU-SERVER.md`; `hostos.py` begonnen. Fehlt: Linux-Testlauf, PC-Agent, systemd, Secrets, Backups auf NAS, Watchdog, Selbstüberwachung |
| 4 HA & Energie | ⬜ | Vorhanden: Räume/Aliase, Klima-Gruppen, eigene Routinen, Energie-Coach, Stromkosten, Budget. Fehlt: HA-Automationen per Sprache, PV-Prognose, Noah-SOC, PromQL aus Sprache mit Grafik |
| 5 Proaktivität | 🔄 | 05.11.2026: Proaktiver Modus (`feat/proaktiv`, docs/proaktiv.md): Auslöser Heimkommen/Morgen/Sofort/Gute Nacht, Stufen, Ausgabe nach Präsenz, Schweige-Regeln, Feedback per Sprache, Verlauf. Fehlt: Ereignis-Engine (HA-WebSocket statt 30-s-Abfrage, Alertmanager) |
| 6 Präsenz & Multiroom | ⬜ | Vorhanden: Lautsprecher je Raum, Sonos-Ansagen, Musik umziehen. Fehlt: mmWave/CSI-Fusion, Arbitration mehrerer Mikros, Lautstärke je Raum und Tageszeit |
| 7 Arbeitsassistent | ⬜ | Vorhanden: IMAP lesen, Entwürfe, CalDAV, Recherche, Pläne. Fehlt: M365 Graph, Fristen-Wächter, Diktat → Dokument, berufliches Gedächtnis (Entscheidung 1 nötig) |
| 8 HUD | ⬜ | Vorhanden: 13 Layouts, iPad-Pairing, Zustandsanzeige, Graph. Fehlt: Kamera-Streams, Prometheus-Grafiken, Aufteilung von `app.js` |
| 9 Netz & IT | ⬜ | Vorhanden: Pi/NAS/UniFi/Pi-hole, neue Geräte, Lagebericht, Updates mit Karte, `pi_run`. Fehlt: Alerts sprechen, Ursachen-Rückfrage, Neustart nach Freigabe |

## Querschnitt

| Thema | Status | Stand |
|---|---|---|
| Tests | 🔄 | 637 Tests grün; `coverage` in `requirements-dev.txt`, noch keine Messung; nie auf Linux gelaufen |
| Observability | 🔄 | `/metrics` für Anfragen, Dauer, Tokens, Karten, Jobs, Notbetrieb, Werkzeugaufrufe, Log-Fehler und Sprach-Engine; `logs/jarvis.log` als JSON. Prometheus auf dem Pi erreicht den PC nicht; kein Grafana-Dashboard „Jarvis“ |
| Kostenkontrolle | ⬜ | Tokens je Route im Log, Strombudget vorhanden; kein API-Budget mit Warnung |
| Datenschutz | 🔄 | Audio lokal, Rohaudio nur im Debug-Modus; Übersicht „welche Daten an welche Cloud“ fehlt als Dokument |
| Konfiguration | 🔄 | `voice.yaml` als Muster; Rest in `.env` (333 Variablen) und 7 JSON-Dateien. Neu: `JARVIS_LOG_LEVEL`, `JARVIS_LOG_DEBUG`, `JARVIS_LOG_FILE` |
| Dokumentation | 🔄 | README, ARCHITECTURE, SECURITY, PORTING, UMBAU-SERVER, VOICE-ANALYSE; README je Modul und Anleitung „neue Fähigkeit“ fehlen |

## Nächste Schritte (nach Freigabe von Phase 0)

1. Entscheidungen 1–7 aus `BESTANDSAUFNAHME.md`; Schlüssel aus Commit `f9f794f` rotieren (Nutzer); Prometheus auf dem Pi an `/metrics` anbinden, sobald der Jarvis-Host erreichbar ist.
2. Phase 2 Agenten-Kern mit Registry, YAML-Konfiguration, SQLite-Gedächtnis.
3. Phase 3 Server-Umzug nach `UMBAU-SERVER.md`.

## Verlauf

- 05.11.2026 Proaktiver Modus gebaut und abgenommen (`docs/abnahme-proaktiv.md`), Branch `feat/proaktiv`.

- 02.11.2026 Phase 0 freigegeben, P0 Fundament gebaut (Logging, Metriken, Abhängigkeiten, Tests).
- 02.11.2026 Phase 0 Bericht erstellt; Phase 1 Kern fertig (Sprach-Engine auf dem Server).
- 29.10.2026 Server-Umbauplan (`UMBAU-SERVER.md`).
- 24.10.2026 Portabilitätsprüfung (`PORTING.md`), `hostos.py`.
- 14.10.2026 Erster Commit.
