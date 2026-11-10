[← Zurück zum README](../README.md)

# Gedächtnis und Lernen

Ressourcen, Kontext und Aufgaben, die lernfähigen Module und das dauerhafte Gedächtnis im Obsidian-Vault.

## Ressourcen, Kontext, Aufgaben und Gedächtnis

Weitere Ausbaustufen auf der bestehenden Architektur. Alle laufen ohne LLM, außer wo vermerkt, und lassen sich einzeln abschalten.

| Modul | Datei | Was es tut | Befehle |
|---|---|---|---|
| **Resource Manager** | `resources.py` | Misst VRAM, RAM, Ollama, GPU-Stimme und Whisper mit echten Werten. Hält eine Reserve frei (`JARVIS_VRAM_RESERVE_MB`). Wird es knapp, gibt er nur im Leerlauf Speicher ab: erst Ollama entladen, dann die GPU-Stimme pausieren (Piper spricht), dann Whisper auf die CPU. Später holt er alles wieder zurück. Ein Gespräch unterbricht er nie. | *„Wie voll ist die Grafikkarte?“* · HUD: Zeile „GPU-Budget“ |
| **Skill Factory** | `skill_factory.py` | Gelernte Formulierungen sind zuerst ein **Entwurf**. Ab der 2. Wiederholung kommt ein Vorschlag, aktiviert wird erst nach Prüfung (Gerät vorhanden und schaltbar, keine Kollision mit Routinen) und Bestätigungskarte. Jede Version wird gespeichert und lässt sich zurücknehmen. | *„Was hast du gelernt?“*, *„Nimm die Regel Leselampe zurück“* |
| **Context Engine** | `context.py` | Fragt Zeit, Kalender, Wetter, Timer, fällige Pläne, Warnungen, PC und Haus parallel ab, mit Zeitlimit und Cache. Veraltete oder fehlende Quellen werden markiert. Jede LLM-Anfrage bekommt eine kurze „Lage“. | *„Wie sieht mein Tag aus?“* (lokal) |
| **Task Engine** | `tasks.py` | Prüfungen in Schritten (NAS, Pi, Netzwerk, PC, Smart Home, alles). Jeder Schritt hat Zeitlimit, Wiederholung und eigene Ergebnisprüfung, Abbruch ist jederzeit möglich. Am Ende gibt es einen Bericht mit Wiederherstellungsplan. Repariert wird **nie** automatisch. | *„Prüf mein NAS“*, *„Systemcheck“*, *„Brich die Prüfung ab“* |
| **Memory Intelligence** | `memory_intel.py` | Doppeltes wird nicht gespeichert. Ein Widerspruch (z. B. ein anderer Wohnort, ein anderes Datum) wird **nie** überschrieben, Jarvis fragt nach. Namen von Personen, Projekten und Geräten werden zu Links im Graphen. Herkunft „vermutet“ und ein Ablaufdatum pro Eintrag sind möglich. | *„Prüf mein Gedächtnis“* |
| **Monitoring** | `monitoring.py` | Speicherprognose für NAS, Systemplatte und Pi-Karte mit Datenbasis und Sicherheit. Wiederkehrende Ausfälle werden benannt. Gleiche Meldungen aus mehreren Quellen werden zusammengefasst, höchstens 6 gesprochene Hinweise pro Stunde. | *„Wann ist das NAS voll?“* |
| **Dokumentensuche** | `docsearch.py` | Lokaler Volltext-Index (SQLite FTS5) über die freigegebenen Ordner und `JARVIS_DOC_ROOTS`, mit Fundstelle und Position (bei PDF die Seite). | *„Was steht im Handbuch zum Flusensieb?“* |
| **Vision** | `vision.py` | Beschreibt den Bildschirm mit Claude, wenn die lokale Texterkennung nicht reicht (Aufbau, wo ein Knopf ist). Nur auf Nachfrage, mit **Karte je Bild**. Gesperrt bei Passwort-, Bank- und Chat-Fenstern. Das Bild geht ohne Werkzeuge an Claude und wird danach gelöscht. Ohne Claude bleibt Vision aus. | *„Wo finde ich in diesem Fenster die Einstellung?“* |

| Variable | Standard | Zweck |
|---|---|---|
| `JARVIS_RESOURCES`, `JARVIS_VRAM_RESERVE_MB`, `JARVIS_VRAM_HYSTERESIS_MB` | `1`, `1500`, `1000` | Resource Manager, freie Reserve, Abstand fürs Zurückholen |
| `JARVIS_TTS_VRAM_MB`, `JARVIS_WHISPER_VRAM_MB` | `4000`, `1800` | Bedarf der GPU-Stimme und von Whisper (Einstellung, keine Messung) |
| `JARVIS_OLLAMA_IDLE_S`, `JARVIS_OLLAMA_SMALL_MODEL` | `180`, leer | Ollama nach Leerlauf entladen; kleineres Modell bei knappem Speicher |
| `JARVIS_SKILL_MIN_SEEN` | `2` | Ab so vielen Wiederholungen wird ein Entwurf vorgeschlagen |
| `JARVIS_CONTEXT_TIMEOUT` | `4` | Zeitlimit je Kontextquelle in Sekunden |
| `JARVIS_TASK_STEP_TIMEOUT`, `JARVIS_TASK_MAX_RUNNING` | `45`, `2` | Zeitlimit je Prüfschritt, gleichzeitige Prüfungen |
| `JARVIS_FORECAST_WINDOW`, `JARVIS_FORECAST_WARN_DAYS` | `30`, `60` | Prognosezeitraum, Warnschwelle in Tagen |
| `JARVIS_DEDUP_SECONDS`, `JARVIS_SAY_PER_HOUR` | `1800`, `6` | Gleiche Meldung zusammenfassen, gesprochene Hinweise pro Stunde |
| `JARVIS_HOME_IGNORE` | Wechselrichter, Internet-Zugriffsschalter | Entities, die der Smart-Home-Check übergeht (Regex) |
| `JARVIS_DOCSEARCH`, `JARVIS_DOC_ROOTS`, `JARVIS_DOC_MAX_FILES`, `JARVIS_DOC_MAX_MB` | `1`, leer, `5000`, `20` | Dokumentensuche, weitere Ordner, Grenzen |
| `JARVIS_VISION`, `JARVIS_VISION_MODEL`, `JARVIS_VISION_MAX_WIDTH` | `1`, `sonnet`, `1600` | Bildbeschreibung an/aus, Modell, maximale Bildbreite |

Lokale Daten (in `.gitignore`): `logs/tasks.json`, `logs/metrics_daily.json`, `logs/docindex.sqlite`, `config/learned.history.jsonl`.

## Lernfähige Module

Vier Module laufen im Hintergrund, jedes in einer eigenen Datei. Für alle gilt:
- **Keine Arbeitsthemen:** Was nach Arbeit aussieht, wird weder protokolliert noch ausgewertet oder gespeichert (`privacy.py`, eigene Begriffe in `JARVIS_WORK_TERMS`). Das gilt auch für das Gesprächsprotokoll und für `memory_save`.
- **Änderungen nur mit Karte:** Hintergrundjobs ändern nichts selbst. Sie legen einen Vorschlag ab (`proposals.py`), Jarvis erwähnt ihn im nächsten Gespräch, erklärt auf Deutsch, was passiert und warum, und erst die Bestätigungskarte (`proposal_accept`) führt ihn aus. Die Erklärung steht auch auf der Karte.
- **Erst der Kalender, dann der Vorschlag:** Bevor Jarvis einen Termin zum Eintragen vorschlägt oder selbst einträgt (`calendar_add`, Termine aus Mails, Lieferungen), liest der Code den Kalender für diesen Tag (`icloud_calendar.check`). Steht dort schon ein Termin mit ähnlichem Titel, kommt kein Vorschlag und keine Karte; überschneidet er sich mit einem anderen, steht das auf der Karte und in der Ansage.

| Modul | Datei | Was es tut | Wann |
|---|---|---|---|
| **Lernschleife** | `reflection.py` | Bewertet nach jedem Gespräch, was gut lief und was korrigiert wurde, und schreibt das in die Notiz **Lernprotokoll**. Verdichtet die Woche zu Regeln; bestätigte Regeln stehen in **Vorlieben** unter „Gelernte Regeln“ und gelten ab dann in jedem Gespräch. *„Streich die Regel …“* entfernt eine wieder. | 30 min nach dem letzten Wortwechsel; Regeln sonntags ab 19 Uhr |
| **Wochenrückblick** | `weekly_review.py` | Holt täglich Schlaf, Schritte, Stress, Body Battery und Golfrunden von Garmin, sonntags Rückblick mit Vergleich zur Vorwoche und Mustern (im Code berechnet, mit Anzahl der Tage). Essenszeiten, Alkohol und Kaffee notiert Jarvis, wenn man sie sagt (*„Hab um 22 Uhr gegessen“*). Notiz: **Gesundheit/Wochenrückblick JJJJ-KWnn** | Abruf ab 9 Uhr; Rückblick sonntags ab 18 Uhr |
| **Homelab-Beobachter** | `homelab_watch.py` | Home Assistant, Pi (Dienste, Container, Temperatur, Speicherkarte, Last), NAS und wichtige Geräte aus `config/homelab.json`. Meldet Ausfälle erst nach zwei Prüfungen, einmal pro Vorfall plus Entwarnung, nachts nur Kritisches, auffällige Werte nach festen Grenzen und nach einer Lernwoche auch gegen die eigenen Normalwerte. Neue WLAN-Geräte meldet weiterhin der bestehende Wächter. 0 Tokens. | alle 5 Minuten |
| **Vorschläge** | `suggestions.py` | Schlägt eine neue Fähigkeit vor, die zu den Gewohnheiten der letzten zwei Wochen passt, mit Begründung. Routinen und Erinnerungen richtet er nach der Karte selbst ein; was neuen Code bräuchte, wird nur notiert. Liste mit Status in der Notiz **Vorschläge**. | dienstags und freitags 18:30 |

Hintergrundjobs nutzen Claude Haiku (`JARVIS_BACKGROUND_MODEL`) als eigenen kurzen Aufruf, ohne Werkzeuge und ohne den Gesprächsablauf zu blockieren; ist Claude am Limit, nur Groq oder Ollama (nie Gemini). Verbrauch steht in `logs/usage.jsonl` als `background:…`, geschätzt 30–60k Tokens pro Woche.

| Variable | Standard | Zweck |
|---|---|---|
| `JARVIS_WORK_TERMS` | leer | Eigene Arbeitsbegriffe (Firma, Kollegen, Kunden, Projekte), kommagetrennt |
| `JARVIS_REFLECT`, `JARVIS_REVIEW`, `JARVIS_HOMELAB`, `JARVIS_SUGGEST` | `1` | Modul an/aus |
| `JARVIS_REFLECT_IDLE_MIN`, `JARVIS_REFLECT_WEEK_HOUR`, `JARVIS_MAX_RULES` | `30`, `19`, `20` | Lernschleife |
| `JARVIS_REVIEW_HOUR`, `JARVIS_LATE_MEAL`, `JARVIS_PATTERN_DAYS` | `18`, `21:00`, `42` | Wochenrückblick |
| `JARVIS_HOMELAB_SECONDS`, `JARVIS_HOMELAB_CONFIRM`, `JARVIS_HOMELAB_MAX_PER_DAY` | `300`, `2`, `2` | Beobachter; Geräte und Grenzen in `config/homelab.json` (Vorlage `config/homelab.example.json`) |
| `JARVIS_SUGGEST_DAYS`, `JARVIS_SUGGEST_TIME` | `1,4`, `18:30` | Vorschläge (Montag = 0) |
| `JARVIS_BACKGROUND_MODEL`, `JARVIS_PROPOSAL_DAYS` | `haiku`, `7` | Modell der Hintergrundjobs, Verfall offener Vorschläge |

Lokale Daten (alle in `.gitignore`): `logs/health_daily.json`, `logs/proposals.json`, `logs/homelab_baseline.json`, `config/homelab.json`.

## Gedächtnis

```
vault/
├─ Erinnerungen/     Profil · Befehle · Vorlieben · Websites · Wissen · Netzwerk · Smart Home
│  ├─ Personen/      eine Notiz pro Person
│  ├─ Projekte/      eine Notiz pro Vorhaben
│  └─ Netzwerk-Geräte/   automatisch vom Netzwerk-Wächter angelegt
├─ Gespraeche/       Tagesprotokolle (automatisch)
└─ Papierkorb/       abgelaufene Notizen, nach 30 Tagen gelöscht
```

- **Obsidian-kompatibel:** Frontmatter mit `type`, `wichtigkeit` und optional `ablauf`, Verknüpfungen per `[[Wikilink]]`. Daraus entsteht der 3D-Graph im HUD.
- **Gezielter Abruf statt Komplett-Kontext:** Im Prompt stehen nur Profil und Befehle. Alles andere holt Jarvis über `memory_search` (Volltext, gewichtet) und `memory_read`.
- **Selbstständiges Merken** über `memory_save`, ohne dass man „merk dir“ sagen muss.
- **Vergessen mit System:** Ablaufdaten, dann der Papierkorb, dann die Löschung.
