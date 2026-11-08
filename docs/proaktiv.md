# Proaktiver Modus

Jarvis meldet sich von selbst – zur richtigen Zeit, im richtigen Raum, und schweigt nach klaren Regeln.
Code: `jarvis/watch/proactive.py` (Steuerung), `quiet_rules.py` (Schweige-Regeln), `proactive_sources.py`
(Quellen), `proactive_config.py` (Einstellungen), `jarvis/core/mic_use.py` (Mikrofon in Benutzung).

## Einschalten

```powershell
copy config\proactive.example.yaml config\proactive.yaml   # Entitäten anpassen, enabled: true
.venv\Scripts\python.exe scripts\proactive_ha_setup.py     # legt die drei HA-Schalter an (einmalig)
```

`enabled: false` (oder `JARVIS_PROACTIVE_MODE=0`) schaltet alles ab: Willkommen, Morgen-Briefing und
DWD-Warnungen laufen dann wieder genau wie vorher über `away_check.py`, `morning_echo.py` und `weather_alerts.py`.
Die Datei wird bei jeder Änderung neu gelesen, ein Neustart ist nicht nötig.

## Wann Jarvis spricht

| Auslöser | Wann | Was |
|---|---|---|
| Heimkommen | `person.*` ist nach mindestens 20 min Abwesenheit wieder `home` | Briefing, sobald ein Präsenzmelder den Raum kennt (spätestens nach 3 min am Standardlautsprecher) |
| Morgens | erstes Betreten des Bads nach der Nacht (5–11 Uhr), einmal am Tag | Wetter, Warnungen, Termine, Pakete, Sonne, was nachts war. Zählt als Tagesbriefing |
| Sofort | jede Runde (30 s) | Kritisches und Wichtiges aus HA: Wasser/Rauch, NOAH-Differenz, Steckdose offline, DWD-Warnungen |
| Gute Nacht | „Jarvis, gute Nacht“ | hängt an deine Routine „Gute Nacht“ an: Geräte noch an, offene Fenster, früher Termin morgen |

Das Briefing dauert höchstens 25 Sekunden (etwa 60 Wörter); was nicht mehr passt, steht nur auf dem iPad.
Claude (Haiku, ohne Werkzeuge) formuliert es; antwortet das Modell nicht innerhalb von 25 s, spricht Jarvis
einen festen Text aus den Meldungen. Gesagte Meldungen merkt er sich mit einem Fingerabdruck: dieselbe Sache
kommt erst wieder, wenn sich etwas ändert (z. B. eine neue Akku-Differenz, ein anderer Termin).

Jede Meldung hat eine Stufe: **kritisch** (Wasser, Rauch, Unwetter), **wichtig** (Akku, Gerät offline, Termin
gleich), **nett zu wissen** (Wetter, Pakete, Sonnen-Tipp). Die Stufen stehen in
`config/proactive.yaml → priorities`.

## Wann Jarvis schweigt

In jedem dieser Fälle spricht er **nur Kritisches**:

| Grund | Erkannt an |
|---|---|
| Ruhezeit | `quiet.hours` (Standard 22–7). Das Morgen-Briefing beendet die Nacht |
| Ruhe | „Jarvis, Ruhe für eine Stunde“, „… für 20 Minuten“, „heute keine Ansagen“ (bis 5 Uhr) oder Schalter `input_boolean.jarvis_ruhe` |
| Gäste | Schalter `input_boolean.jarvis_gaste_modus` oder „Gäste-Modus an“ |
| Telefonat / Videocall | ein Programm nutzt das PC-Mikrofon (außer den Programmen in `calls.mic_ignore`) oder eine Entität aus `calls.entities` ist an (iPhone-Fokus) |
| Nicht zu Hause | `person.*` ist nicht `home`: Wichtiges wartet aufs Heimkehr-Briefing, Kritisches geht zusätzlich aufs Handy |
| Proaktiv aus | Schalter `input_boolean.jarvis_proaktiv` aus, oder der Not-Aus ist aktiv |

Wichtiges, das wegen Ruhe, Gästen, Telefonat oder Abwesenheit zurückgehalten wurde, sagt Jarvis danach, solange
es noch gilt. Was in der Ruhezeit zurückgehalten wurde, kommt im Morgen-Briefing, nicht um 7:00 Uhr schlagartig.
„Nett zu wissen“ unterbricht nie, es wartet auf das nächste Briefing.

Hinweis zum Telefonat: Der Browser mit dem HUD hält das Mikrofon für „Jarvis“ dauernd offen und steht deshalb
in `mic_ignore`. Ein Videocall **im Browser** fällt so nicht auf; Teams, Zoom, Discord und die Windows-Telefon-App
schon. Für Anrufe am iPhone den Fokus-Sensor der HA-App einschalten (iPhone: HA-App → Einstellungen → Companion
App → Sensoren → Fokus), dann ist `binary_sensor.<handy>_focus` an, solange ein Fokus läuft.

## Sprachbefehle

| Sagen | Wirkung |
|---|---|
| „Jarvis, mehr dazu“ | Details zur letzten Meldung (beim Briefing: zu allen Punkten) |
| „unwichtig“ / „Wetter unwichtig“ | Feedback; nach 3× in 30 Tagen sinkt diese Meldungsart eine Stufe (kritisch nie) |
| „Was hast du über Ansagen gelernt?“ | zählt das Feedback auf |
| „Ansage-Feedback zurücksetzen“ / „Wetter Ansage-Feedback zurücksetzen“ | alles bzw. eine Art zurück auf die normale Stufe |
| „Ruhe für eine Stunde“ · „heute keine Ansagen“ · „Ruhe aufheben“ | Ruhe an/aus (schaltet den HA-Schalter mit) |
| „Gäste-Modus an/aus“ · „Proaktiv-Modus an/aus“ | die HA-Schalter |

Meldungsarten für Feedback: Wetter, Unwetter/Warnung, Paket, Sonne/Energie, Termin, Akku/NOAH,
Offline/Steckdose, Geräte, Fenster.

## iPad und PC: Verlauf

Die Glocke zeigt unter **ANSAGEN · PROAKTIV** die letzten Ansagen mit Zeit, Stufe, Raum und ob sie gesprochen,
nur angezeigt oder zurückgehalten wurden – mit Grund. Daneben der Knopf **unwichtig**, darüber das Gelernte mit
**zurücksetzen**, eine laufende Ruhe und ausgefallene Quellen. Daten: `GET /api/proactive`,
Feedback: `POST /api/proactive/feedback`. Der Verlauf liegt in `logs/proactive_history.jsonl` (letzte 500);
Arbeitstermine werden dort nicht gespeichert.

## Quellen

Jede Quelle lässt sich in `sources.<name>.enabled` einzeln abschalten. Fällt eine aus (Fehler oder keine Antwort
in 12 s), wird sie übersprungen, steht einmal im Log (`logs/jarvis.log`: „Quelle … fällt aus“, später „… ist
wieder da“) und das Briefing läuft mit dem Rest.

| Quelle | Woher |
|---|---|
| `calendar`, `parcels` | iCloud-Kalender (`ICLOUD_*` in `.env`); Pakete sind die „📦“-Einträge von `shipments.py` |
| `weather` | open-meteo (`JARVIS_WEATHER_LAT/LON`) |
| `dwd` | Warnungen des Deutschen Wetterdienstes über Bright Sky |
| `energy` | Energie-Coach (Sonnenüberschuss) |
| `noah` | HA-Schalter `input_boolean.noah_diff_warnung` (schaltet die Automation „StromAutomatisierung“ ab 6 % Differenz), dazu Master- und Slave-SoC |
| `devices_offline` | `binary_sensor.*_cloud_verbindung` länger als 15 min aus |
| `safety` | `binary_sensor` mit `device_class` moisture/smoke/gas/carbon_monoxide – sobald solche Melder in HA sind |
| `devices_on`, `windows` | abends: was noch läuft (wie beim Verlassen), offene Fenster/Türen |
| `missed` | beim Heimkommen/morgens: was Jarvis in der Zwischenzeit gemeldet hat |

## Secrets

Keine Zugangsdaten in `config/proactive.yaml`. Alles Geheime steht in `.env`:

| Variable | Wofür | Ändern / rotieren |
|---|---|---|
| `HA_TOKEN`, `HA_URL` | Home Assistant lesen, Schalter setzen, Durchsagen | HA → Profil → Sicherheit → langlebiges Token neu, in `.env` eintragen, Jarvis neu starten, altes Token löschen |
| `ICLOUD_USER`, `ICLOUD_APP_PASSWORD` | Termine | account.apple.com → App-spezifische Passwörter: neues anlegen, eintragen, altes widerrufen |

## Linux-Server und Docker

Der Modus enthält nichts, was nur unter Windows läuft: Pfade kommen aus Umgebungsvariablen
(`JARVIS_PROACTIVE_CONFIG`, `JARVIS_PROACTIVE_HISTORY`), der Schalter aus `JARVIS_PROACTIVE_MODE`.
Unter Linux findet `mic_use.py` kein PC-Mikrofon und meldet nichts; dort trägt man in `calls.entities` eine
HA-Entität ein, die der PC liefert (z. B. HASS.Agent „Mikrofon in Benutzung“). Die Tests laufen unter Linux in
`python:3.12-slim` (nachgewiesen auf dem Pi, siehe `docs/abnahme-proaktiv.md`).

## Diagnose

```powershell
# einen Auslöser jetzt mit echten Daten ausführen (nur am PC)
curl -X POST http://127.0.0.1:8720/api/proactive/trigger -H "X-Jarvis-Token: <aus dem HUD>" `
     -H "Origin: http://127.0.0.1:8720" -H "Content-Type: application/json" -d '{\"mode\":\"arrival\"}'
.venv\Scripts\python.exe -m unittest tests.test_quiet_rules tests.test_proactive tests.test_proactive_e2e -v
```
