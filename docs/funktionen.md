[← Zurück zum README](../README.md)

# Funktionen

Alles, was Jarvis kann: die Highlights, die lange Liste der kleinen Haken und der Stand der Funktionsliste.

## Highlights

| | |
|---|---|
| **Freihändig sprechen** | Live-Mikrofon mit Pausen- und Satzende-Erkennung, Push-to-Talk, Unterbrechen durch Reinreden: er hält an, hört zu und macht weiter, wenn nichts kommt. Optional mit Aktivierungswort („Jarvis …“ oder lokal per openWakeWord); ohne „Jarvis“ wird nichts bearbeitet oder protokolliert. |
| ⏰ **Timer, Wecker, Erinnerungen** | *„Timer 10 Minuten für die Nudeln“*, *„Weck mich morgen um halb sieben“*, *„Erinnere mich in 20 Minuten an die Wäsche“*, *„Mach das Licht in einer Stunde aus“*. Lokal, ohne Tokens, übersteht Neustarts. |
| **Meldet sich selbst** | Waschmaschine fertig, Termin in 15 Minuten bzw. „Zeit loszufahren“ (mit Fahrzeit), Paket kommt heute, Speicher voll, NAS-Problem, neues Gerät im WLAN. Mit Ruhezeiten. |
| **Unterwegs und Post** | *„Wann muss ich los?“* (Fahrzeit zum nächsten Termin über OpenStreetMap), *„Kommt heute ein Paket?“* (aus den Versandmails). Lokal, ohne Tokens. |
| **PC-Steuerung** | *„Lautstärke auf 30 Prozent“*, *„Ton aus“*, *„Öffne Spotify“*, *„Sperr den Bildschirm“*. Spotify wird leiser, solange Jarvis zuhört oder spricht, auf jedem Gerät des Kontos; nachts spricht er leiser. |
| **Wird sparsamer** | Merkt sich Formulierungen, die Claude einmal auflösen musste („Leselampe aus“), und schaltet sie danach selbst. Gleiche Fragen innerhalb von 5 Minuten kommen aus dem Zwischenspeicher. *„Verbrauchsbericht“* sagt, wie viel lokal lief. |
| **Aussprache** | NAS, Pi, kWh & Co. werden richtig gesprochen; eigene Wörter per *„Sprich Tuya als Tuja aus“*. |
| **Listen und Routinen** | Einkaufs- und To-do-Liste per Sprache (Home Assistant oder Vault-Notiz). Routinen wie „Gute Nacht“: ein Satz, mehrere Aktionen, per Sprache angelegt. |
| **Kontrolliert und ehrlich** | Feste Regeln im Code statt im Prompt, Schutz vor fremden Anweisungen in Mails und Webseiten, Audit-Log, Not-Aus, prüfbare Herkunft jeder Erinnerung, ehrliche Rückmeldung beim Schalten. |
| **Persönlichkeit** | Standard, Profi, Technisch, Locker per Zuruf; getippte Fragen ausführlich, gesprochene kurz. |
| **Windows** | Fenster anordnen, Arbeitsbereiche, Dateien und Zwischenablage, Bildschirmtext per lokaler OCR, freigegebene Skripte. |
| **Lernt dazu** | Bewertet seine Gespräche selbst, schlägt sonntags feste Regeln vor, macht einen Wochenrückblick mit Mustern aus Schlaf, Schritten, Stress und Golf, meldet Homelab-Ausfälle nur bei Relevanz und schlägt zweimal pro Woche eine neue Fähigkeit vor. Ohne Arbeitsthemen, Änderungen nur per Karte. |
| **Notbetrieb** | Fällt Claude aus oder ist das Limit erreicht, übernimmt eine kostenlose Kette: Groq → Gemini → Ollama, mit denselben Werkzeugen und Bestätigungskarten. |
| **Echtes Gedächtnis** | Notizen nach Themen, automatisches Gesprächsprotokoll, selbstständiges Merken und Vergessen (Ablaufdaten, Papierkorb). |
| **Lokaler Intent-Router** | Licht, Steckdosen, Musik, Uhrzeit, Wetter, Energie- und Systemstatus ohne LLM, mit Antwort in Millisekunden. |
| **Schlanke LLM-Aufrufe** | Eigener Prompt statt Claude-Code-Standard, Werkzeuge nach Bedarf, automatische Modellwahl (Haiku/Sonnet). In Messungen **über 25× weniger Tokens** als ein Standard-Aufruf. |
| **Ausfallsichere Stimme** | Kette aus Cloud-, lokalen GPU- und Gratis-Stimmen. Erschöpfte Anbieter werden bis zum Reset übersprungen, ohne hörbaren Abbruch. |
| **Smart Home** | Home Assistant: schalten, Zustände abfragen, Energie (Solar/Speicher), Raumklima, Luftqualität. Optional der volle HA-MCP-Server für Automationen und Verlauf. |
| **Heimnetz** | Status von Raspberry Pi und Synology-NAS, Geräteliste, Wächter für neue Geräte im WLAN. *„Lagebericht“* fasst Uptime Kuma, die Sicherheitszentrale auf dem Pi und den Energie-Kreislauf zusammen und meldet Ausfälle, dringende Sicherheitsbefunde und eine hängende Nulleinspeisung von selbst. |
| **Alltag** | Kalender (CalDAV/iCloud), Postfach (IMAP, nur lesen), Garmin-Gesundheitsdaten, Spotify, Websuche. |
| **HUD** | Dashboard mit Wetter- und Aktivitäts-Ampel, Haus, Gesundheit, Systeme, Post, 3D-Wissensgraph und Verbrauchsanzeige. |
| **Kontrolliert** | Nur `localhost`, Bestätigungskarten für Systemeingriffe, geschützte Geräte, kein Selbstumbau zur Laufzeit. |

## Was alles möglich ist

Die Architektur trennt **Router** (Regeln), **Werkzeuge** (MCP-Server) und **Gedächtnis** (Markdown). Neue Fähigkeiten lassen sich deshalb meist mit wenigen Zeilen ergänzen.

**Im Alltag**
- *„Guten Morgen“* liefert ein Tagesbriefing mit Wetter, Terminen, Energie, fast leeren Batterien und neuen Mails.
- *„Kann ich heute noch eine Runde laufen / golfen?“* ergibt eine Ampel aus Wetter, Wind, Tageslicht und Arbeitszeit.
- *„Was steht morgen an?“* bzw. *„Trag mir Freitag um zehn den Zahnarzt ein“* (mit Bestätigung).
- *„Hab ich wichtige Mails?“* bringt eine Zusammenfassung ohne Newsletter.
- *„Merk dir, dass Anna am 3. Mai Geburtstag hat“* legt eine Notiz unter `Personen/Anna` an.
- *„Timer 8 Minuten für die Pizza“*, *„Wie lange noch?“*, *„Weck mich um 6:45“*, *„Schlummern“*, *„Wecker aus“*
- *„Erinnere mich morgen um 9 daran, Anna anzurufen“*, *„Welche Erinnerungen habe ich?“*
- *„Setz Milch und Eier auf die Einkaufsliste“*, *„Was steht auf der Einkaufsliste?“*, *„Streich die Milch“ … „von der Einkaufsliste“*
- *„Wenn ich Gute Nacht sage, mach alle Lichter aus und sag mir, wann der Wecker klingelt“* legt eine Routine an. Danach läuft *„Gute Nacht“* ohne LLM.

**Zuhause**
- *„Mach das Licht im Wohnzimmer auf 40 Prozent“*, *„Ist die Waschmaschine noch an?“*, *„Szene Kino“*
- *„Wie viel liefert die Solaranlage, und wie voll ist der Speicher?“*
- *„Was kostet mich der PC im Monat?“*, *„Wer zieht Standby-Strom?“*: Stromkosten aus den Zählern der Steckdosen (Preis `JARVIS_POWER_PRICE`); Standby misst der Energie-Coach über mehrere Tage.
- Verlässt du die Wohnung und es läuft noch Licht, Musik oder ein Gerät aus `config/home.json` → `away_check`, kommt eine Nachricht aufs Handy und ein Vorschlag „Ausschalten“, den du in Jarvis auf einer Karte bestätigst. Von selbst schaltet er nichts.
- Sonntags ab 18 Uhr: Wochenbericht fürs Heimnetz (Ausfälle, Sicherheitsbewertung, Internet-Abbrüche, Verbrauch und Sonnenstrom), gesprochen und als Notiz unter `Erinnerungen/Homelab`.
- *„Welche Sicherheitsbefunde sind offen?“*, *„Nimm den Echo-Befund für 30 Tage als Ausnahme“* (Karte), *„Starte eine Sicherheitsprüfung“*.
- Kommst du nach mindestens 20 Minuten heim: *„Willkommen zurück. Während du weg warst: …“* mit dem, was Jarvis in der Zeit gemeldet hat.
- Amtliche Warnungen des Deutschen Wetterdienstes (über Bright Sky) meldet er von selbst, ab „markant“; schwere auch nachts und aufs Handy. *„Gibt es Unwetterwarnungen?“*
- Gewohnheiten im Haus: Schaltest du ein Licht an mindestens 4 von 10 Tagen zur gleichen Zeit, schlägt Jarvis samstags eine Routine vor (*„Gute Nacht“*), angelegt erst nach der Karte. Ist ein Licht viel länger an als sonst, sagt er es einmal.
- *„Sag im Wohnzimmer: Essen ist fertig“* spricht über den Echo Dot, im Schlafzimmer über den Sonos (`config/home.json` → `speakers`).
- *„Mach die Musik vom Schlafzimmer im Wohnzimmer an“*, *„Musik ins Schlafzimmer“* schiebt die Spotify-Wiedergabe auf den Lautsprecher des Raums (Spotify Connect, läuft dort weiter; braucht das Spotify-Login). Welcher Spotify-Name zum Raum gehört, steht in `config/home.json` → `speakers` → `spotify`; fehlt er, gilt der Raumname (Sonos und Alexa-Gruppen heißen in Spotify meist wie der Raum). Ohne Raum geht auch ein Gerätename, etwa *„Musik auf den PC“*.
- *„Öffne Netflix auf dem Fernseher“*, *„Fernseher pause/lauter/leiser“* über Alexa am Fire TV — nur feste Befehle und App-Namen (`config/home.json` → `tv`).
- Antworten aus dem Echo: am HUD-Knopf 🔈 „Echo Wohnzimmer“ wählen — was du am PC tippst oder sagst, spricht dann Alexa (mit Alexas Stimme, Satz für Satz; kostet keine Sprach-Guthaben).
- Morgen-Briefing: geht morgens im Wohnzimmer das erste Mal etwas an (LED, Fernseher, oder du sprichst mit dem Echo), sagt der Echo einmal Wetter, Warnungen, Termine, Sonnen-Tipp, Meldungen der Nacht und ungelesene Mails (`config/home.json` → `morning_briefing`).
- Rückfragen: nach *„Licht im Wohnzimmer an“* reichen zwei Minuten lang *„heller“*, *„dunkler“*, *„auf 30 Prozent“*, *„wieder aus“*; nach *„TV an“* auch *„und im Schlafzimmer auch“*.
- *„Energie-Tipp“*, *„Wann soll ich die Spülmaschine anmachen?“*: Der Energie-Coach sagt aus Akkustand, Hausverbrauch und Sonnenvorhersage, wann heute oder morgen Strom übrig ist und was dann laufen sollte. Ist der Akku voll und Überschuss da, meldet er sich einmal am Tag von selbst. Er schaltet nie selbst. Steckdosen, die beim ersten Blick aus waren (z. B. der Boiler), gelten als selten genutzt und werden nie vorgeschlagen; *„Den Boiler nutze ich oft“* ändert das.
- *„Welche Batterien muss ich tauschen?“*
- Mit dem HA-MCP-Server: *„Warum ist es im Schlafzimmer so kalt? Zeig mir den Verlauf.“*

**Technik und Netzwerk**
- *„Wie geht es dem Pi / dem NAS?“* liefert Temperatur, Speicher und Laufzeit, außerdem den Plattenstatus mit Warnung ab 85 %.
- *„Ist ein unbekanntes Gerät im WLAN?“* Neue Geräte meldet der Wächter von selbst.
- *„Starte die Container auf dem Pi neu“* (Werkzeug `pi_run`: ein Befehl per SSH, immer mit Bestätigungskarte; offensichtlich zerstörerische Befehle lehnt es ab).
- *„Lagebericht“*, *„Läuft alles?“*, *„Wie ist die Sicherheitslage?“*, *„Läuft die Nulleinspeisung?“* liefern lokal und ohne Tokens den Stand aus Uptime Kuma (Statusseite `jarvis`), der ORBIT-Sicherheitszentrale und Home Assistant (`config/homelab.json` → `energy`).

**Smarter im Alltag** (seit Oktober 2026)
- *„Was habe ich dich letzte Woche zum Drucker gefragt?“*, *„Worüber haben wir gestern gesprochen?“* beantwortet der Router aus dem Gesprächsprotokoll (0 Tokens). *„Wie war mein Schlaf, als ich das letzte Mal krank war?“* bekommt das Werkzeug `memory_timeline`: je Tag Gespräche, Garmin, Meldungen, Ausfälle und Speicherstände, nach Datum verknüpft (`episodes.py`).
- **Deine Gewohnheiten:** Fragst du an mindestens vier Tagen zur gleichen Zeit dasselbe (*„Wann muss ich los?“* dienstags um 7:40), schlägt Jarvis samstags vor, es dir von selbst zu sagen, oder, wenn das Modell antworten muss, die Antwort drei Minuten vorher in den Zwischenspeicher zu holen, damit sie sofort und ohne Tokens kommt. Erst die Karte richtet es ein. *„Welche Gewohnheiten kennst du?“*, *„Vergiss die Gewohnheit …“* (`user_habits.py`).
- **Garmin als Auslöser:** Schlaf-Score unter 60, Stress ab 50 oder Body Battery unter 30 am Morgen machen den Vormittag „schonend“: kurzes, ruhiges Briefing, kein Sarkasmus, Vorschläge und Energie-Tipps nur gezeigt statt gesprochen, Stimme etwas leiser. Abends ab 21 Uhr bei Body Battery unter 25 einmal der Hinweis auf einen frühen Abend, mit dem Auslöser der Gute-Nacht-Routine. *„Wie geht es mir heute?“* (`wellbeing.py`).
- **Vor dem Losfahren:** Hat ein Termin einen Ort, meldet Jarvis 30 Minuten vor der Losfahrzeit, was zu Hause noch läuft (wie beim Verlassen) und welche Thermostate er auf die Abwesenheitstemperatur stellen würde (`config/home.json` → `departure`; `climate_away: 0` schaltet sie stattdessen aus). Der Vorschlag liegt dann schon bereit: *„ausschalten“* genügt, dazu die Karte, oder der Ja-Knopf auf dem Handy (`departure.py`).
- **Strombudget:** `JARVIS_POWER_BUDGET_EUR` (oder `budget_eur` in `config/energy_coach.json`): *„Liege ich im Budget?“*, und ab dem fünften Tag des Monats einmal am Tag ab 18 Uhr die Meldung, wenn die Hochrechnung drüber liegt, mit dem größten Verursacher.
- **Wartungskalender:** Zertifikate, die bald ablaufen, Docker-Images auf dem Pi, die zu alt sind, das Alter des letzten NAS-Backups, Platten, die nicht „normal“ sind (`config/homelab.json` → `maintenance`). *„Was steht im Wartungskalender?“*, *„Wann läuft das Zertifikat ab?“*, täglich eine Meldung je fälligem Punkt (`maintenance.py`).
- **Paperless-ngx:** *„Was zahle ich für die Haftpflicht?“*, *„Wann läuft der Handyvertrag?“*: Volltextsuche in der gescannten Post, Dokumente lesen (`PAPERLESS_URL`, `PAPERLESS_TOKEN`). Fremde Inhalte, nur lesend.
- **UniFi:** *„Wer ist gerade im WLAN?“*, *„Laufen alle Access Points?“* (`UNIFI_URL` mit API-Schlüssel oder Benutzer; `unifi.py`).
- **Jellyfin:** *„Was habe ich zuletzt geschaut?“*, *„Serie auf dem Fernseher weiterschauen“*, *„Such den Film Heat“*; Abspielen auf jedem Gerät mit Jellyfin-App (`JELLYFIN_URL`, `JELLYFIN_API_KEY`, `JELLYFIN_USER`, `JELLYFIN_TV`).
- **Stimme nach Tageszeit:** Ab 21 Uhr spricht er leiser und etwas langsamer, in den Ruhezeiten flüstert er (Stimmen mit Regie-Tags bekommen `[whispers]`), und das Modell antwortet dann in höchstens zwei Sätzen. *„Flüstermodus an“* / *„Sprich wieder normal“* schalten von Hand bis zum Morgen (`daytime.py`).
- **Rückfrage mit Gedächtnis:** *„Mach Wohnzimmer an“* bei einer Lampe und einer Steckdose: Jarvis fragt einmal *„Meinen Sie die Lampe oder die Steckdose?“*, führt die Antwort aus und merkt sie sich für diese Formulierung; danach fragt er nie wieder und braucht kein Modell (`learning.py` → `choices`, zu sehen unter *„Was hast du gelernt?“*).
- **Handy als Karte:** Vorschläge beim Verlassen und vor dem Losfahren kommen mit Ja/Nein-Knöpfen aufs Handy (Home-Assistant-App). Die Antwort läuft über einen Helfer in Home Assistant zurück und zählt wie die Karte; siehe [Handy](bedienung.md#handy-ja-und-nein-von-unterwegs) (`phone_actions.py`).
- **Fokus-Sitzungen:** *„Fokus 45 Minuten“*: Meldungen werden gesammelt statt gesprochen (Wecker klingeln), die Fokus-Playlist startet (`config/home.json` → `focus`), nach zehn Minuten in YouTube, Netflix oder Reddit kommt einmal ein Hinweis, am Ende die Zusammenfassung mit dem, was inzwischen anlag. *„Fokus beenden“*, *„Wie lange noch Fokus?“* (`focus.py`).
- **HA in Echtzeit:** Jarvis hält eine WebSocket-Verbindung zu Home Assistant (`ha_events.py`) und bekommt jede Zustandsänderung sofort. Tür, Fenster, Wasser, Rauch, Bewegung, Anwesenheit, der Druckerstatus und die Jarvis-Schalter wecken die Meldungs-Prüfungen sofort statt erst in der nächsten 30-Sekunden-Runde; alle Stellen, die bisher die komplette Zustandsliste per REST geholt haben, lesen die Live-Kopie. Fällt die Verbindung aus, fragt Jarvis wie früher ab. Abschalten: `JARVIS_HA_WS=0`.
- **„Warum ist das Licht angegangen?“** Jarvis liest das HA-Logbuch und verfolgt den Auslöser bis zu zwei Schritte zurück: *„Hue Schlafzimmer ging um 7:20 aus. Auslöser: die Automation ‚Licht folgt mir‘, ausgelöst von ‚Raum‘, das gesetzt hat: die Automation ‚Wo bin ich‘, ausgelöst von ‚Präsenz Schlafzimmer‘.“* Ohne Kontext im Logbuch: am Gerät selbst oder in dessen App geschaltet (`ha_why.py`).
- **„Wo ist …?“** *„Ich hab den Ersatzschlüssel in die blaue Kiste im Flur gelegt“*, *„Merk dir, der Reisepass liegt im Tresor“*, *„Die Winterreifen sind jetzt im Keller“* landen in der Notiz **Orte**; *„Wo ist der Ersatzschlüssel?“* findet es. Ein neuer Ort ersetzt den alten. Ohne „merk dir“, „jetzt“ oder „ich hab … gelegt“ wird nichts gespeichert, damit Fernsehton die Notiz nicht füllt (`places.py`).
- **Geburtstage:** Am Vortag um 18 Uhr: *„Morgen hat Anna Geburtstag, sie wird 34. Notiert habe ich: mag Tulpen.“* Aus den Personen-Notizen und Stichpunkten wie „Ben hat am 04.05.1990 Geburtstag“, auch selbst gemerkten; die Geschenkidee sind die Vorlieben aus der Notiz der Person, kein Modellaufruf. *„Wer hat bald Geburtstag?“*, *„Wann hat Anna Geburtstag?“* (`birthdays.py`).
- **„Was hast du heute gemacht?“** Anfragen und lokaler Anteil, was er von selbst gesagt, sich gemerkt und vorgeschlagen hat und was er mit oder ohne Karte geändert hat, aus seinen eigenen Logs (`activity.py`).
- **Nachtbericht der Geister:** Morgens ab 7:30, nur wenn ein Präsenz- oder Bewegungsmelder mindestens 5-mal angeschlagen hat, während du in einem anderen Raum warst, oder nachts ein Licht anging, samt Auslöser. *„Wie war die Nacht?“* fragt jederzeit (`night_report.py`).
- **Lüften und Schimmel:** Jarvis vergleicht die absolute Feuchte drinnen (Feuchte- und Temperatursensoren mit Raumnamen) mit der Außenluft. Ab 60 % und deutlich trockenerer Luft draußen kommt einmal pro Raum und Tag (8–21 Uhr) ein Lüften-Tipp; liegt ein Raum drei Tage lang meist über 65 %, eine Schimmel-Warnung pro Woche. *„Soll ich lüften?“* (`airing.py`).
- **Druckideen:** *„Merk dir als Druckidee: Kabelhalter für den Schreibtisch“*, *„Was wollte ich noch drucken?“*, *„Der Kabelhalter ist gedruckt“*, *„Such einen Kabelhalter auf MakerWorld“* öffnet die Suche im Browser (`print_ideas.py`, Liste im Vault).
- **Kuma-Alarme sofort, Neustart nach Freigabe:** Die HA-Automation „ORBIT: Uptime-Kuma-Alarm aufs Handy“ schickt zusätzlich das Ereignis `jarvis_alert`; Jarvis sagt einen Ausfall dann sofort an statt nach bis zu zehn Minuten (`kuma_alerts.py`). Gehört der Monitor zu einem Container auf dem Pi (`config/homelab.json` → `kuma_restart`), fragt er einmal *„Soll ich den Container … neu starten?“*; ein *„Ja“* geht über die Bestätigungskarte, erst dann läuft `docker restart`. Ist der Dienst von selbst wieder da, zieht er die Frage zurück.
- **Rauschfilter:** Schaltet ein Sensor mehr als 30-mal pro Stunde (Geisterimpulse der Präsenzmelder), weckt er die Wächter eine Stunde lang nicht mehr; sein Wert landet trotzdem in der Live-Kopie (`JARVIS_HA_WS_NOISY`).
- **Was steht morgen an?** Wecker, erster Termin und wie viele danach, ganztägige Einträge, erwartete Pakete und das Wetter in ein paar Sätzen, lokal (`tomorrow.py`). Läuft auch als vorletzter Schritt der Routine „Gute Nacht“.
- **Token-Wochenbudget:** `JARVIS_TOKEN_BUDGET_WEEK` (Standard 2 Millionen) zählt seit Montag alles, was an ein Modell ging. Bei 80 und 100 % sagt Jarvis es einmal pro Woche (`token_budget.py`); *„Wie viele Tokens?“* nennt den Stand, die Kachel „Tokens“ zeigt den Anteil.
- **Absturz-Wächter:** Jarvis stempelt jede Minute die Uhrzeit in den HA-Helfer `input_datetime.jarvis_lebenszeichen`, aber nur, wenn sein eigener Server noch antwortet (`heartbeat.py`). Läuft der PC (Steckdose über 80 W) 10 Minuten lang, ohne dass ein Stempel kommt, schickt die Automation „Jarvis: Lebenszeichen-Wächter“ eine Push-Meldung aufs Handy, einmal je Ausfall. Ein ausgeschalteter PC löst nichts aus. Einrichten mit `scripts/heartbeat_ha_setup.py`; Testinstanzen auf anderen Ports stempeln nicht.
- **Selbstkontrolle:** Stellst du eine Frage innerhalb von 25 Sekunden umformuliert noch einmal, zählt das als Fehlschlag der ersten Antwort, samt dem Handler oder Modell, das sie gab. Die Lernschleife sieht diese Stellen, *„Wie gut warst du diese Woche?“* nennt sie (`selfcheck.py`).
- **Token-Kachel:** Im Raum-Display zeigt die Kachel „Tokens“ die Woche, den Anteil lokal erledigter Anfragen und den Trend zur Vorwoche; das Blatt listet die teuersten wiederholten Fragen, also die Kandidaten für den Router.
- **Routinen-Vorschau:** *„Was passiert, wenn ich Gute Nacht sage?“*, *„Was macht die Routine Feierabend?“* zählt die Schritte auf, ohne etwas zu schalten.

**Erweiterungsideen** (siehe [Funktionsliste](#funktionsliste))
- Kamera- oder Bildschirm-Kontext auf Anfrage
- Raum-Mikrofone (ESP32/Satelliten), mehrere Nutzerprofile per Sprecher-Erkennung
- Weitere Werkzeuggruppen: Proxmox, Fahrzeug-APIs, Fitness-Plattformen
- Eigene Stimme per Sprachprobe (lokal), Mehrsprachigkeit

## Funktionsliste

Der Plan je Ausbauphase steht in `ROADMAP.md`, die Bestandsaufnahme vom 02.11.2026 in `BESTANDSAUFNAHME.md`. Hier die Funktionsliste:

- [x] Aktivierungswort („Jarvis …“) als Alternative zum Dauer-Mikrofon, Folgefragen ohne Aktivierungswort
- [x] Sprach-Engine auf dem Server: Silero VAD, adaptives Satzende mit Partials, Unterbrechen in 150 ms, Weitersprechen am Satzanfang, Metriken für Grafana
- [x] Sprach-Engine für iPad/iPhone (über den Remote-HUD, `/voice-ws`)
- [ ] Raum-Satellit mit openWakeWord
- [ ] Echo-Unterdrückung für den Sonos mit Referenzsignal (speexdsp) im Alltag erproben
- [x] Timer, Wecker, Erinnerungen und geplante Befehle
- [x] Meldungen von selbst (Geräte fertig, Termine, Speicher, NAS, neue Geräte) mit Ruhezeiten
- [x] Einkaufs- und To-do-Liste, Routinen per Sprache
- [x] Notbetrieb mit kostenloser Ersatz-Kette (Groq, Gemini, Ollama) inklusive Werkzeugen
- [x] Automatisierte Tests für den Router
- [x] Pendelzeiten, Paketstatus, PC-Steuerung, Ducking, Aussprache, lernender Router, Antwort-Cache
- [x] Autostart und Neustart nach Absturz, Absturz-Wächter in Home Assistant mit Push-Meldung
- [x] Lernschleife, Wochenrückblick, Homelab-Beobachter, Vorschlagsmodul
- [x] Aktionsklassen, Audit-Log, Not-Aus, Gedächtnis-Kontrolle, Schutz vor fremden Anweisungen
- [x] Persönlichkeitsprofile, Fenster/Dateien/Bildschirm, PC- und Netzwerk-Diagnose, Mail-Entwürfe, Recherche, Pläne, Meldungszentrale
- [x] Resource Manager, Skill Factory mit Freigabe, Context Engine, Task Engine, Memory Intelligence, Prognosen, Dokumentensuche
- [x] PDF in der Dokumentensuche (pypdf), Bildbeschreibung auf Nachfrage mit Karte je Bild
- [ ] Maus- und Tastatursteuerung (bewusst nicht gebaut; nur als eigene, streng kontrollierte Erweiterung)
- [ ] Größeres lokales Modell nur im Notbetrieb (VRAM teilen mit der GPU-Stimme)
- [ ] Zentrale Bedienung: Bildschirm, Raum-Mikrofone, Sprecher-Erkennung (bewusst ausgelassen: ein Nutzer)
- [x] Zugriff vom Handy: Vorschläge mit Ja/Nein-Knöpfen, Antwort über Home Assistant
- [x] Fokus-Sitzungen mit Ablenkungs-Hinweisen
- [ ] Bildschirm- und Kamera-Kontext auf Anfrage
- [ ] Mehrere Nutzerprofile und Raum-Satelliten (ESP32)
- [ ] Linux/macOS-Startskripte
- [x] Weitere Werkzeuggruppen: Paperless (Dokumente), UniFi (Netzwerk-Controller), Jellyfin (Medienserver)
- [ ] Hypervisor (Proxmox)
- [x] Episodisches Gedächtnis (Gespräche, Garmin, Meldungen und Ausfälle je Tag), Gewohnheiten des Users, Garmin als Auslöser, Vorbereitung vors Losfahren, Strombudget, Wartungskalender, Stimme nach Tageszeit, Rückfrage mit Gedächtnis, Selbstkontrolle, Token-Kachel, Routinen-Vorschau
- [x] Lagebericht aus Uptime Kuma, Sicherheitszentrale und Energie-Kreislauf, mit Meldungen von selbst
- [x] Energie-Coach: Sonnenüberschuss vorhersagen, verschiebbare Geräte vorschlagen, selten genutzte Steckdosen merken
- [x] Stromkosten je Gerät und Standby, Check beim Verlassen der Wohnung, Wochenbericht fürs Heimnetz, Sicherheitsbefunde per Sprache
- [x] Willkommen zurück, Unwetter-Warnungen, Gewohnheiten im Haus (Routinen-Vorschläge, zu lange an), Durchsagen in anderen Räumen, Fire TV per Sprache
- [x] Antworten über den Echo, Morgen-Briefing im Wohnzimmer, Rückfragen beim Schalten (heller, wieder aus, im Schlafzimmer auch)
- [x] Automatisierte Tests für die Stimmen-Kette

<sub>Ursprünglich inspiriert von <a href="https://github.com/Itsme23476/jarvis-claude-code">jarvis-claude-code</a>. Für eine eigenständige Weitergabe vorher die Lizenzlage der Vorlage klären.</sub>
