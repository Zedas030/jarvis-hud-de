# Abnahme: Raum-Bewusstsein (Branch `feat/raum-befehle`)

Stand 05.11.2026, 14:45 Uhr. Nutzer war zu der Zeit nicht zu Hause (`person.nutzer` = `not_home`, Helfer „Unbekannt“).

## Nachgewiesen

| Punkt | Nachweis | Ergebnis |
|---|---|---|
| Regressionstests vorher | `unittest discover -s tests` auf `5e3380d`, vor der ersten Änderung | 683 Tests, OK |
| Regressionstests nachher | gleiche Suite auf `feat/raum-befehle` | 714 Tests (683 + 31 neu), OK |
| Feature-Flag aus = altes Verhalten | `test_room_scope.Fallbacks.test_switched_off_behaves_as_before` | OK |
| HA nicht erreichbar = altes Verhalten | `Fallbacks.test_home_assistant_down_behaves_as_before`, `Presence.test_hiccup_then_stale` | OK |
| Sonos nicht verfügbar → Gerät antwortet, sagt einmal warum | `Output.test_unavailable_sonos`, `test_failed_announcement`, `test_presence_down_told_once` | OK |
| Geräte je Raum aus den echten HA-Bereichen | `scripts/room_probe.py` gegen das echte HA | Wohnzimmer: LED, TV, 2 Thermostate · Schlafzimmer: 2 Thermostate, Luftreiniger, TV Schlafzimmer, Sonos, iPad-Steckdose |
| Befehle gegen die echten Geräte (Raum vorgegeben, Schalten abgefangen) | Probelauf mit echtem HA, `jarvis_mcp._home` aufgezeichnet | Wohnzimmer: „Licht an“ → `light.licht`, „TV an“ → `switch.tv`, „Heizung an“ → Wohnzimmer-Thermostate · Schlafzimmer: „Licht an“ → Rückfrage, „Ja“ → `light.licht`; „TV an“ → `switch.tv_schlafzimmer`; „wärmer“ → Schlafzimmer-Thermostate · unsicher: „TV an“ → „Wohnzimmer oder Schlafzimmer?“, „Schlafzimmer“ → `switch.tv_schlafzimmer` |

## Live-Teilabnahme 05.11.2026, 16:37–16:45 Uhr

Nutzer zu Hause, Branch im laufenden Jarvis (`C:\Users\nutzer\jarvis-hud` auf `feat/raum-befehle`, Neustart 16:39), `scripts\room_probe.py --watch` lief mit.

| Punkt | Nachweis | Ergebnis |
|---|---|---|
| Live-Suite | `unittest discover -s tests` im Live-Ordner, zusammen mit den offenen Änderungen einer anderen Session | OK |
| Raum live erkannt | `room_probe.py`: Wohnzimmer, sicher, Ausgabe Echo Wohnzimmer | OK |
| iPad-Daten | `/api/dashboard` → `house.where` = Wohnzimmer, sicher, online | OK (Kachel selbst nicht gesehen) |
| „Licht aus“ / „Licht an“ im Wohnzimmer | `logs/audit.jsonl` 16:41:26 / 16:41:30, lokal (0 Token) | OK |
| Radar-Fehlimpuls | Schlafzimmer-Melder kurz belegt (16:40:10, 16:42:24), Helfer blieb Wohnzimmer, kein Raumwechsel | OK: kein falscher Wechsel |

Gefunden und behoben (16:47): Musik per Spotify Connect auf der Wohnzimmer-Soundbar wurde nicht leiser, wenn der Echo antwortete. Das Ereignis `room_audio` (Antwort über Echo oder Sonos) rief im Browser kein `duckMusic` auf, nur die Ausgabe am Gerät selbst. Fix in `ui/voice-client.js`, Commit `8742500`. Live bestätigt 16:56: `logs/spotify.log` „leiser 17 % → 5 %“, nach der Antwort wieder 17 %; Nutzer: „Musik wird leiser, passt“.

Aufgefallen: Jarvis lief vor dem Test seit etwa 12:09 nicht (kein Absturz im Log), neu gestartet per `start-jarvis.bat`.

Dabei gefunden und behoben: Ein gelernter Wortlaut („Licht an“ → `light.licht`) übersprang die Rückfrage im Schlafzimmer, und der Sonos-Schalter „Schlafzimmer TV-Autoplay“ galt als Gerät. Für beides gibt es jetzt einen Test.

## Offen (braucht Nutzer zu Hause und den Branch live)

- [ ] Echter Raumwechsel: `scripts\room_probe.py --watch` laufen lassen, Wohnzimmer → Schlafzimmer → Bad → Wohnzimmer gehen
- [x] „Licht an“ im Wohnzimmer schaltet die LED (live 05.11., 16:41)
- [ ] Im Schlafzimmer kommt bei „Licht an“ die Rückfrage. Ein zweites schaltbares Licht gibt es im Haus nicht
- [ ] Antwort über den Sonos im Schlafzimmer, während dort Musik läuft: Musik wird leiser und spielt danach weiter (Lautstärke, Titel, Gruppierung)
- [ ] Nachfrage beim Durchlaufen (Helfer noch alter Raum, neuer Melder belegt)
- [ ] Fallback: Sonos stromlos → „Der Lautsprecher … ist gerade nicht erreichbar“ am Gerät; HA gestoppt → „Ich sehe gerade nicht, in welchem Raum Sie sind“
- [ ] iPad: Kachel „Raum“ sichtbar (Daten kommen an, Anzeige auf dem iPad nicht gesehen)

Merge erst, wenn die offenen Punkte live geprüft sind.

## Bewusst nicht gebaut

- Ein eigener Zuversichtswert aus vielen Signalen: Die Hold-Logik gegen Radar-Fehlimpulse steckt schon in der HA-Automation „Jarvis: Wo ist Nutzer“. Jarvis prüft nur noch, ob die Melder dem Helfer gerade widersprechen.
- „Keine Ausgabe in Räumen, in denen sonst jemand schläft“: In der Wohnung schläft sonst niemand. Antworten gehen ohnehin nur in den eigenen Raum, nachts mit Nachtlautstärke.
- YAML-Konfiguration: Jarvis nutzt durchgehend `config/home.json` und `.env`, ein zweites Format wäre ein Bruch.
