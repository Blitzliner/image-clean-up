# fotosort – Fotos sortieren, aufräumen & Jahresrückblick

Sortiert Fotos von mehreren Handys + WhatsApp **strikt nach Aufnahmezeit**,
erkennt **Duplikate**, **Serien** und schwache Bilder, markiert **Favoriten** (5 Sterne),
legt alles in **Ereignis-, Alltags- und Themen-Ordnern** auf dem NAS ab und erzeugt
einen **Jahresrückblick** und einen **Haus-Rückblick** – jeweils als **PowerPoint**
(zum Bearbeiten) und **PDF** (für die Großeltern).

## Installation (einmalig)

1. **uv** installieren (falls noch nicht vorhanden), in PowerShell:
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
2. Im Projektordner:
   ```powershell
   cd C:\Users\manue\Documents\git\analysis\image-clean-up
   uv sync
   ```
   Beim ersten Lauf werden die Modelle nach `models/` geladen:
   Gesichter (YuNet 0,2 MB + SFace 37 MB) und Bildinhalte (CLIP, ca. 0,6 GB, einmalig).

### Verwendete Bibliotheken

| Bibliothek | Wofür |
|---|---|
| **Pillow** (+ pillow-heif) | Bilder öffnen (auch HEIC), EXIF lesen, verkleinern |
| **opencv-python-headless** | Schärfe, Gesichter finden (YuNet) und wiedererkennen (SFace) |
| **fastembed** | Bildinhalte erkennen (CLIP): Haus/Renovierung, Kinder, Dokumente |
| **imagehash** | „Fingerabdruck“ für ähnliche Bilder / WhatsApp-Kopien |
| **numpy** | schnelle Vergleiche |
| **python-pptx** | PowerPoint-Rückblick |
| **fpdf2** | PDF-Rückblick |
| **piexif** | Sterne ins EXIF schreiben (ohne Neukomprimieren) |
| **reverse_geocoder** | Ortsnamen aus GPS – offline |
| **pyyaml**, **tzdata** | Konfiguration, Zeitzonen (PXL-Dateien sind in UTC benannt) |

## Oberfläche (empfohlen)

```powershell
uv run fotosort ui
```
Öffnet die Oberfläche im Browser (nur auf deinem PC, `http://127.0.0.1:8765`). Beenden: **Strg+C**.

1. **Einstellungen** – Quellordner, NAS-Ziel, **wie streng aussortiert wird**
   (mild / normal / streng / sehr streng), Alltag als **ein Ordner pro Jahr** oder pro Monat,
   Themen-Ordner, Rückblick-Optionen → **Speichern & analysieren**.
2. **Ereignisse** – erkannte Reisen und Vorschläge (auch Zeiträume mit vielen Fotos ohne GPS)
   übernehmen und benennen → **Speichern & neu einteilen**.
   - **im Rückblick** abhaken: eigener Ordner bleibt, das Ereignis erscheint aber nicht im Jahresrückblick.
   - **kein Ordner**: kein eigener Ordner – die Fotos werden trotzdem behalten und im Alltag sortiert.
3. **Personen** – gleiche Gesichter sind gruppiert. Rolle vergeben:
   **Kind** → Ordner „Kinder“, **Freund** → „Mit Freunden“, **Familie**. Ein Name ist freiwillig
   (nur Rolle → automatisch „Freund 1“). Gruppen ohne Rolle werden einfach nicht beachtet,
   **✕ ausblenden** blendet eine Gruppe dauerhaft aus (rückgängig über „ausgeblendete anzeigen“).
   Gleicher Name führt Gruppen zusammen. Wird beim nächsten Lauf wiedererkannt (`personen.json`).
4. **Bilder prüfen** – alle Bilder chronologisch je Ordner, vollständig (auch Hochkant).
   ★ Favorit, 🗑/↩ aussortieren/zurückholen, Themen-Häkchen *Kinder / Freunde / Haus* per Klick,
   Filter je Thema. Großansicht: ← → blättern, **F** Favorit, **A** aussortieren.
   - **☐ auswählen** (Umschalt-Klick = Bereich) → unten **verschieben nach** einem anderen
     Ereignis, in den Alltag oder in ein **neues Ereignis**; außerdem „★ alle“ / „🗑 alle“.
   - Ansicht **Rückblick**: alle Seiten so, wie sie im PDF/PowerPoint erscheinen
     (Ereignisse, Kinder, Mit Freunden, Haus, Sonstiges) – jedes Bild trägt außerdem ein
     Abzeichen wie „📖 S. 8 · Mit Freunden“.
   Deine Klicks (`korrekturen.json`) haben immer Vorrang vor der Automatik.
5. **Ablegen & Rückblick** – auf das NAS kopieren/verschieben, dann Rückblick erstellen
   (Links zu PowerPoint und PDF).

## Ergebnis auf dem NAS

```
Fotos/2026/
├── 2026-05_Urlaub_Frankreich_Frühjahr/     ← Ereignisse
│   └── Aussortiert/
├── Alltag/                                 ← alles ohne Ereignis (ein Ordner pro Jahr)
│   └── Aussortiert/
├── Kinder/                                 ← KOPIEN aller Kinderfotos
├── Mit_Freunden/                           ← KOPIEN aller Fotos mit Freunden
├── Haus_und_Renovierung/                   ← Haus-Fotos, nach Bereich
│   ├── Bad/  Küche/  Wohnräume/  Außen_und_Garten/  Baustelle_und_Material/
├── Screenshots/
├── _fotosort_protokoll.csv                 ← was kam woher, warum aussortiert, Themen
├── Jahresrueckblick_2026.pptx / .pdf
└── Hausrenovierung_2026.pptx / .pdf
```

Dateien bekommen den Aufnahmezeitpunkt vorangestellt (`JJJJMMTT_HHMMSS_`), damit
„Sortieren nach Name“ chronologisch ist – egal ob von dir, deinem Mann oder per WhatsApp.

## Rückblick

**Jahresrückblick:** Titelseite → Ereignisse chronologisch (je 1 Seite, bei vielen Favoriten
bis zu 2) → Kinder → Mit Freunden → Haus & Renovierung (ca. 2 Seiten) → Sonstiges
(übrige Favoriten, bis 2 Seiten). Max. 6 Bilder pro Seite, Bilder werden **vollständig**
gezeigt (Blocksatz-Layout wie im Fotobuch; höchstens `pdf.max_beschnitt` = 15 % wird
abgeschnitten, nie ein Gesicht). Datum groß unter dem Titel, ohne Jahr; Seitenzahl unten.

**Haus-Rückblick:** alle behaltenen Haus-Fotos nach Bereich (Bad, Küche, …), innerhalb
chronologisch.

**PowerPoint bearbeiten:** Jedes Foto ist vollständig eingefügt und nur zugeschnitten –
Bild anklicken → *Zuschneiden*, um den Ausschnitt zu verschieben; *Bild ändern* zum
Austauschen; Folien duplizieren/hinzufügen. In den Notizen jeder Folie stehen die
Original-Dateipfade. Für die E-Mail: *Datei → Exportieren → PDF*.

**Auswahl ändern:** Im Windows-Explorer Sterne vergeben/entfernen und den Rückblick neu
erstellen. Ins PDF kommen Bilder mit mind. 4 Sternen.

## Kommandozeile (Alternative)

```powershell
uv run fotosort analysieren --interaktiv   # nichts verändern, Vorschau + Ereignisse benennen
uv run fotosort sortieren                  # ablegen
uv run fotosort pdf --jahr 2026            # Jahres- und Haus-Rückblick (PPTX + PDF)
uv run fotosort alles                      # sortieren + Rückblick
```

## So arbeitet das Skript

| Schritt | Was passiert | Datei |
|---|---|---|
| Zeitpunkt | EXIF → Dateiname (PXL: UTC → Ortszeit) → WhatsApp-Datum (Uhrzeit geschätzt) | `metadaten.py` |
| Analyse | Schärfe, Belichtung, Gesichter + Gesichts-Fingerabdruck, Note 0–100 | `analyse.py`, `gesichter.py` |
| Inhalte | CLIP: Haus/Renovierung (+ Bereich), Kinder, Dokumente/Schilder | `inhalt.py` |
| Duplikate & Serien | WhatsApp-Kopien (kleinere Auflösung) → Aussortiert, übernehmen Originalzeit; Serien → bestes bleibt | `duplikate.py` |
| Filterstärke | *streng*: längere Serien, Dokumente raus, nur beste 70 % je Tag; *sehr streng*: 50 % | `duplikate.py` → `STAERKEN` |
| Ereignisse | GPS-Reisen + Vorschläge aus Fotodichte + `ereignisse.yaml`; Frühjahr/Herbst nie vermischt | `ereignisse.py` |
| Personen | Gesichter gruppieren, benannte Personen wiedererkennen | `personen.py` |
| Themen | Kinder / Mit Freunden (Kopien), Haus (eigener Ordner) | `themen.py` |
| Favoriten | beste ~15 % je Ereignis bzw. je Monat im Alltag | `planung.py` |
| Ablage | kopieren/verschieben, nie überschreiben, Sterne, Themen-Kopien, Protokoll | `ablage.py`, `bewertung.py` |
| Rückblick | Seitenplanung, Blocksatz-Layout, PPTX + PDF | `jahresrueckblick.py` |
| Oberfläche | lokaler Webserver + Browser-Seite | `ui.py`, `ui.html` |

Alle Schwellwerte stehen mit Erklärung in `src/fotosort/config.py` und lassen sich in
`config.yaml` überschreiben. Die CLIP-Beschreibungen (z.B. was als „Haus“ zählt) stehen
in `inhalt.py` und können ergänzt werden.

## Wie es mit vielen Bildern skaliert (3.000 – 10.000+ Fotos)

Gemessen mit 3.135 und 10.032 Bildern (11.808 Gesichter) – Planung, Duplikate,
Ereignisse, Personen und Themen brauchen auch bei 10.000 Bildern zusammen nur ca. 5 s:

| Schritt | Dauer |
|---|---|
| Erstes Einlesen (Hash, Schärfe, Gesichter) | ca. 0,3 s pro Bild und CPU-Kern → bei 8 Kernen ca. 2–3 min, bei 4 Kernen ca. 5 min |
| Inhaltserkennung (CLIP), erstes Mal | ca. 2–4 min (+ einmalig 0,6 GB Download) |
| Jeder weitere Lauf (Cache) | wenige Sekunden – nur neue Fotos werden eingelesen |
| Duplikate, Serien, Ereignisse, Personen, Themen, Favoriten | zusammen < 1 s |
| Oberfläche: Plan laden / Klick auf ★ | ca. 0,1 s / 0,2 s (große Ordner sind anfangs zugeklappt) |
| Ablegen aufs NAS (3000 × 4 MB = 12 GB) | Netzwerk: LAN 1 Gbit ca. 2–3 min, WLAN deutlich länger |
| Rückblick (PowerPoint + PDF) | ca. 1 min, unabhängig von der Gesamtzahl (nur Favoriten) |

Tipps: Fotos lokal einlesen (nicht vom NAS als Quelle), Ziel = NAS. Mehrere Jahre in
einem Lauf sind kein Problem – jedes Jahr bekommt seinen Ordner und Rückblick.

**Robustheit bei vielen Dateien**
- Fortschritt mit Restzeit in der Konsole und als Balken oben in der Oberfläche
  (Einlesen, Bildinhalte, Ablegen, Rückblick).
- Abbrechen ist ungefährlich: Der Cache wird jede Minute gesichert (mit Sicherungskopie
  `.bak`), beim nächsten Start geht es dort weiter. Beim Ablegen werden bereits kopierte
  Dateien übersprungen.
- Sehr große Bilder (Panoramas bis 600 Megapixel, `analyse.max_megapixel`) werden normal
  verarbeitet; die Pillow-Warnung „DecompressionBombWarning“ erscheint nicht mehr, stattdessen
  ein Hinweis beim Bild.
- Defekte Dateien stoppen nichts: Sie stehen in der Oberfläche unter **Bilder prüfen → Probleme**
  und am Ende des Einlesens im Protokoll.
- Stürzt ein Hilfsprozess ab (z.B. zu wenig Arbeitsspeicher), wird der Rest nacheinander eingelesen.
- Vor dem Ablegen wird der freie Speicherplatz im Ziel geprüft; Netzwerk-Aussetzer werden
  bis zu 3× wiederholt, Fehler landen in `ablage_fehler.txt`.
- In der Oberfläche sind große Ordner anfangs zugeklappt und zeigen je 300 Bilder
  („weitere anzeigen“).
- Die Analyse läuft in 7 Schritten, die im Kopf der Oberfläche und in der Konsole angezeigt
  werden. Gibt ein Schritt länger als 1 Minute keine Meldung, schreibt der „Wachhund“ ins
  Protokoll, an welcher Stelle im Code das Programm gerade arbeitet (hilfreich bei Hängern).
- Dateien, die beim letzten Lauf nicht lesbar waren, werden beim nächsten Lauf neu versucht.

**Große Fotos beim Ablegen verkleinern** (Einstellungen → Ordner): Fotos über z.B. 24 MP
(≈ 6000 × 4000) werden beim Kopieren aufs NAS verkleinert und mit JPEG-Qualität 90 gespeichert;
Datum, GPS und Kameradaten bleiben erhalten, kleinere Fotos werden 1:1 kopiert. Die Originale
in den Quellordnern bleiben (Modus „kopieren“) unverändert. Hinweis: Bei Pixel-„Motion Photos“
geht beim Verkleinern das eingebettete Kurzvideo verloren.

## Hinweise

- Ein zweiter Lauf ist schnell (Cache: `.fotosort_cache.json`, `.fotosort_inhalt.npz`).
  Bereits abgelegte Dateien werden übersprungen.
- Es wird nie etwas gelöscht. „Aussortiert“ ist nur ein Unterordner.
- Ohne Internet beim ersten Start: Die Inhaltserkennung wird übersprungen (Haus-Fotos
  dann per Häkchen markieren). Neuer Versuch nach einem Tag oder nach Löschen von
  `models/clip/.download_fehlgeschlagen`.
- Kleinkinder sehen sich für die Gesichtserkennung oft ähnlich – für den Kinder-Ordner
  reicht eine gemeinsame Gruppe mit Rolle *Kind*.
