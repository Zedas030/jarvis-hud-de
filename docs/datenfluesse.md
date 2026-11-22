# Datenflüsse: was das Haus verlässt

Erzeugt aus `jarvis/safety/data_flows.py` (`scripts/data_flows.py --write`); nicht von Hand ändern.
Welche Zeilen in Ihrer Installation aktiv sind, zeigt `scripts/data_flows.py` oder die Frage
„Welche Daten gehen in die Cloud?“.

## Gespräche

| Ziel | Was dorthin geht | Abschalten |
|---|---|---|
| Claude (Anthropic) | Jede Anfrage, die der lokale Router nicht selbst beantwortet: Ihr Satz, die letzten Wortwechsel, passende Notizen und die Ergebnisse der Werkzeuge, die dafür gebraucht werden (Mail, Kalender, Dateien nur auf Nachfrage). Ein Bildschirmfoto nur nach Ihrer Bestätigung auf der Karte. | Nicht abschaltbar, das ist das Gehirn. Bildbeschreibung aus: JARVIS_VISION=0. |
| Groq (Notbetrieb) | Nur wenn Claude gesperrt ist oder ausfällt: dieselbe Anfrage wie an Claude, auch Mail, Kalender und Gesundheit. | GROQ_API_KEY leeren oder JARVIS_FALLBACK=ollama (bzw. off). |
| Google Gemini (Notbetrieb) | Nur im Notbetrieb nach Groq, mit geschwärztem Kontext; nie Anfragen zu Mail, Kalender oder Gesundheit (die kostenlose Stufe darf mit Eingaben trainieren). | GEMINI_API_KEY leeren oder gemini aus JARVIS_FALLBACK nehmen. |

## Stimme

| Ziel | Was dorthin geht | Abschalten |
|---|---|---|
| Fish Audio (Sprachausgabe) | Der Text, den Jarvis spricht. | fish aus TTS_CHAIN nehmen; `local` spricht ohne Cloud. |
| ElevenLabs (Sprachausgabe) | Der Text, den Jarvis spricht. | elevenlabs aus TTS_CHAIN nehmen. |
| Microsoft Azure Speech (Sprachausgabe) | Der Text, den Jarvis spricht. | azure aus TTS_CHAIN nehmen. |
| Google Text-to-Speech (Sprachausgabe) | Der Text, den Jarvis spricht. | google aus TTS_CHAIN nehmen. |
| Microsoft Edge-Stimmen (Sprachausgabe) | Der Text, den Jarvis spricht (kostenloser Dienst, ohne Konto). | edge aus TTS_CHAIN nehmen. |
| Fish Audio / ElevenLabs (Spracherkennung) | Die Tonaufnahme Ihres Satzes — nur wenn die Erkennung ausdrücklich auf einen Cloud-Dienst gestellt ist. Standard ist Whisper auf dem eigenen PC. | JARVIS_STT=local (oder auto mit installiertem faster-whisper). |
| Amazon Alexa (Echo als Lautsprecher) | Der Text der Antwort, wenn ein Echo sie vorliest: über Home Assistant an Amazon. | Den Echo in config/home.json als Lautsprecher austragen. |

## Dienste

| Ziel | Was dorthin geht | Abschalten |
|---|---|---|
| Ihr Mail-Anbieter (IMAP) | Jarvis meldet sich an Ihrem Postfach an, liest und sucht; Entwürfe landen im Ordner Entwürfe. Gesendet wird nichts. | IMAP_HOST/MAIL_USER leeren. |
| Apple iCloud (Kalender) | Kalender lesen; neue Termine nach Bestätigung eintragen. | ICLOUD_USER leeren. |
| Garmin Connect | Anmeldung mit Ihrem Konto; Schlaf- und Aktivitätswerte kommen von dort auf den PC. | GARMIN_EMAIL leeren. |
| Spotify | Wiedergabe steuern und lesen, was läuft. | JARVIS_SPOTIFY_CLIENT_ID leeren. |
| Open-Meteo (Wetter) | Die Wetter-Koordinaten aus .env (JARVIS_WEATHER_LAT/LON) und Ortsnamen, nach deren Wetter Sie fragen. | Nicht abschaltbar; gröbere Koordinaten eintragen, wenn der genaue Standort nicht hinaus soll. |
| Bright Sky (Warnungen des Deutschen Wetterdienstes) | Die Wetter-Koordinaten. | JARVIS_WEATHER_ALERTS=0. |
| OpenStreetMap (Nominatim, OSRM) | Bei Fragen zur Fahrzeit: die Zieladresse und die Koordinaten von zu Hause. | Fahrzeit nicht abfragen; JARVIS_HOME_LAT/LON gröber eintragen. |
| DHL (Sendungsverfolgung) | Sendungsnummern aus Ihren Versandmails. | DHL_API_KEY leeren. |
| 17TRACK (Sendungsverfolgung) | Sendungsnummern aus Ihren Versandmails. | TRACK17_API_KEY leeren. |
| Push aufs Handy (Home-Assistant-App, über Apple/Google) | Der Text eines Vorschlags oder einer Meldung. | JARVIS_PHONE_NOTIFY leeren. |
| Webseiten und Websuche | Nur auf Nachfrage: Suchbegriffe und die aufgerufene Adresse. | Nicht danach fragen. |
| MakerWorld (Druckideen) | Nur auf Nachfrage: der Suchbegriff. | Nicht danach fragen. |

## ohne persönliche Daten

| Ziel | Was dorthin geht | Abschalten |
|---|---|---|
| Anthropic Cost API | Nur der Abrechnungsschlüssel; zurück kommen Kosten. | JARVIS_COST_KEY leeren. |
| Versions- und Verbindungsprüfungen (AVM, home-assistant.io, google.com) | Nur der Abruf selbst (Ihre IP-Adresse), keine Inhalte. | Nicht abschaltbar. |

## Bleibt im Haus

Spracherkennung (Whisper auf dem PC), Weckwort, Gedächtnis und Gesprächsprotokoll, Home Assistant, Sonos, NAS, Pi, Paperless, Jellyfin, UniFi, Bildschirm-Texterkennung, die lokale Stimme und Ollama.
