# Abnahme: Proaktiver Modus (Branch `feat/proaktiv`)

Stand 05.11.2026, ca. 15:45–16:00 Uhr. Basis: `feat/raum-befehle` (Raum-Bewusstsein), also erst diesen Branch
mergen, dann `feat/proaktiv`.

**Was „gestellt“ heißt:** Nutzer war während der Abnahme unterwegs (`person.nutzer = not_home`). Ein Heimkommen
oder den ersten Bad-Besuch am Morgen kann man nicht auf Befehl auslösen. Deshalb hat das Abnahme-Skript
(`scripts/proactive_acceptance.py`) die Anwesenheit auf `home` gesetzt und die Auslöser selbst gestartet.
**Alle Daten waren echt:** HA-Zustände, iCloud-Kalender, open-meteo, DWD, die Meldungen der letzten Nacht, das
Claude-Modell, der Echo im Wohnzimmer. Gehört hat die Durchsagen niemand, weil niemand zu Hause war.
Das Skript hatte eigenen Zustand, Verlauf und eigene Meldungsdatei, der laufende Jarvis wurde nicht verändert.

## Nachgewiesen

| # | Anforderung | Ergebnis |
|---|---|---|
| 1 | Regressionstests grün | Windows: **791 Tests OK**, darunter 11 neue Regressionstests (`test_regression_notices.py`), die vor der ersten Änderung entstanden sind, plus die vorhandenen für Willkommen, Morgen-Briefing, Router und Routinen. Linux (`python:3.12-slim` im Docker auf dem Pi): alle 76 Proaktiv- und Regressionstests OK. In der Linux-Gesamtsuite schlägt nur `test_desktop.test_local_voice_commands` fehl (ein Windows-Befehl); das tut er auf dem Basis-Branch genauso |
| 2 | Heimkehr-Briefing mit echten Daten | Gesprochen über den Echo im Wohnzimmer, 32 Wörter, formuliert von Claude: „Willkommen zurück, Sir. Der Luftreiniger ist offline. Heute kommt ein Paket von Amazon. Das Wetter: Nebel, 0 bis 10 Grad, kein Regen, Wind bis 8 Kilometer pro Stunde. Sonnenuntergang um 16:29.“ Reihenfolge nach Stufe: der wichtige Punkt zuerst, die netten danach |
| 3 | Morgen-Briefing | „Guten Morgen, Sir. Der Luftreiniger ist offline. Heute kommt ein Paket von Amazon. Das Wetter: Nebel, zwischen null und zehn Grad, Regenrisiko null Prozent, … Im Energie-Kreislauf ist wieder alles in Ordnung. Außerdem liegen sechs weitere Meldungen vor.“ Das sind 49 Wörter, also innerhalb der 25 s; der Rest der Nacht steht auf dem iPad |
| 4 | Sofortwarnung | Der Luftreiniger ist in HA wirklich seit 9:05 offline. Gesprochen wurde „Luftreiniger ist offline.“ (Stufe wichtig) |
| 5 | Schweigen in der Ruhezeit, außer Kritischem | Ruhezeit auf 15–16 Uhr gestellt, dazu ein Test-Wassermelder. „Luftreiniger ist offline.“ wurde **zurückgehalten (Ruhezeit)**, „Achtung: Abnahme-Wassermelder meldet Wasser!“ wurde **gesprochen (kritisch)**. Die Ausgabe dieses Szenarios wurde abgefangen und ging nicht auf einen Lautsprecher |
| 6 | „Ruhe für eine Stunde“ | Antwort: „Verstanden, bis 16:46 Uhr sage ich nur noch Kritisches.“ Der echte HA-Schalter `input_boolean.jarvis_ruhe` ging **an**, die Offline-Meldung wurde **zurückgehalten (Ruhe)**, gesprochen wurde nichts. „Ruhe aufheben“ ergab „Gut, ich melde mich wieder.“, und der Schalter ging wieder **aus** |
| 7 | Keine doppelte Meldung | Zwei Sofort-Durchläufe direkt hintereinander: „Luftreiniger ist offline.“ wurde genau **einmal** gesprochen. Auch im Heimkehr-Test (End-to-End) kommt dieselbe Meldung nach dem Briefing nicht noch einmal |
| 8 | Abgeschaltete Quelle wird übersprungen | Mit `sources.weather.enabled: false` wurde `weather` gar nicht abgefragt (abgefragt wurden safety, dwd, noah, devices_offline, calendar, bsr, parcels, missed, energy). Das Briefing lief ohne Wetter |
| 8b | Ausgefallene Quelle wird übersprungen | `bsr` (Müllabfuhr; am 05.11. abends auf Wunsch wieder entfernt) war eingeschaltet, aber noch ohne Schlüssel. Das Log meldete „Quelle bsr fällt aus und wird übersprungen (weder BSR_SCHEDULE_ID noch BSR_ICS_URL in .env)“, einmal pro Durchlauf und nicht pro Runde; jedes Briefing lief trotzdem |
| 9 | Verlauf auf dem iPad | Der HTTP-Teil von Jarvis lief vom Branch auf Port 8740, Ansicht `tablet`. Die Glocke zeigt **ANSAGEN · PROAKTIV** mit Zeit, Stufe, Raum und gesprochen / zurückgehalten samt Grund. Der Knopf „unwichtig“ antwortete mit „notiert“, `/api/proactive` meldete danach `Pakete: 1× unwichtig` |
| 10 | HA-Schalter | Mit `scripts/proactive_ha_setup.py` angelegt: `input_boolean.jarvis_proaktiv` (an), `input_boolean.jarvis_gaste_modus` (aus), `input_boolean.jarvis_ruhe` (aus) |

Durch Tests abgedeckt, nicht live vorgeführt:
- Schweige-Regeln als Tabelle aller Kombinationen aus Stufe, Grund und Modus (`test_quiet_rules.py`)
- Gäste-Modus und Telefonat (iPhone-Fokus, PC-Mikrofon mit Ausnahmeliste)
- unterwegs: Wichtiges wartet, Kritisches geht zusätzlich aufs Handy
- Lernen per „unwichtig“: nach 3× eine Stufe tiefer, „kritisch“ bleibt immer kritisch, Zurücksetzen
- „mehr dazu“, „Gute Nacht“ als Ergänzung der Routine
- Ruhe endet, wenn der HA-Schalter von Hand ausgeschaltet wird
- Ausfall von Home Assistant: das Briefing läuft trotzdem
- Feature-Flag aus: alles wie vorher
- End-to-End-Test mit nachgebautem HA und dem echten Jarvis-HTTP-Server (`test_proactive_e2e.py`)

## Offen (braucht Nutzer zu Hause)

- **Echtes Heimkommen:** `person.nutzer` wechselt auf `home`, dann ein Präsenzmelder; das Briefing kommt im
  richtigen Raum. Der Ablauf ist im End-to-End-Test mit HA-Nachbau geprüft, mit dem echten Haus noch nicht.
- **Echter Bad-Besuch am Morgen** (5–11 Uhr). Sobald `feat/proaktiv` live ist, übernimmt er das Morgen-Briefing
  von `morning_echo`.
- **iPhone-Fokus:** `binary_sensor.nutzer_focus` ist in HA „nicht verfügbar“. Der Sensor muss in der HA-App am
  iPhone eingeschaltet werden, sonst zählt nur das PC-Mikrofon.

## Bewusst so gebaut

- **Dauer des Heimkehr-Briefings:** Claude braucht 20–25 s fürs Formulieren. Das Briefing kommt also etwa 25 s
  nach dem Erkennen des Raums. Wer es schneller will, setzt `output.formulate_with_llm: false` und bekommt den
  festen Text sofort. Bei „Gute Nacht“ wartet Jarvis höchstens 8 s auf das Modell.
- **Videocall im Browser:** Der Browser mit dem HUD hält das Mikrofon dauernd offen und steht deshalb in der
  Ausnahmeliste. Ein Call im Browser fällt so nicht auf; Teams, Zoom, Discord und andere eigene Programme schon.
- **Kein Küchen-Melder:** Morgens löst nur das Bad aus (deine Wahl). Ein weiterer Melder ist eine Zeile in
  `triggers.morning.sensors`.
- **Kalender nicht sofort:** Termine kommen nicht als Sofortmeldung. „In 15 Minuten: …“ sagt schon
  `watchers.calendar`; der Modus hätte das sonst doppelt gemacht.
