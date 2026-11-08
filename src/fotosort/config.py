"""
Konfiguration laden.

Alle Einstellungen stehen in einer YAML-Datei (Standard: config.yaml).
Hier sind die Standardwerte hinterlegt – in der config.yaml musst du nur
das eintragen, was du abweichend haben möchtest.
"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Standardwerte. Jeder Wert kann in config.yaml überschrieben werden.
# ---------------------------------------------------------------------------
STANDARD: dict = {
    # Ordner, in denen die unsortierten Fotos liegen (werden rekursiv durchsucht)
    "quellen": [],
    # Zielordner, z.B. das NAS: "//NAS/Fotos" oder "Z:/Fotos"
    "ziel": "",
    # "kopieren" (sicher, Originale bleiben liegen) oder "verschieben"
    "modus": "kopieren",
    # Zeitzone, in der die Fotos aufgenommen wurden (für PXL_-Dateinamen,
    # die in UTC benannt sind)
    "zeitzone": "Europe/Berlin",
    # Quellen, deren Dateinamen-Zeitstempel in UTC sind (Google Pixel = "mann")
    "dateiname_utc": ["mann"],
    # Dateien beim Ablegen mit Zeitstempel davor umbenennen, damit der
    # Explorer sie auch "nach Name" chronologisch sortiert
    "umbenennen": True,
    # Änderungsdatum der Zieldatei auf den Aufnahmezeitpunkt setzen
    "dateizeit_setzen": True,
    # Zusätzliche Erkennungsmuster für Dateinamen (werden VOR den Standardmustern
    # geprüft). Beispiel: [{quelle: "kamera", muster: "^DSC\\d+"}]
    "quellen_muster": [],
    # Ordner-/Unterordnernamen
    "ordner": {
        "aussortiert": "Aussortiert",
        "screenshots": "Screenshots",
        # Fotos ohne Ereignis: "jahr" = ein Ordner "Alltag" pro Jahr,
        # "monat" = ein Ordner pro Monat (2026-03_März ...)
        "alltag": "jahr",
        "alltag_name": "Alltag",
    },
    # Wie streng aussortiert wird: "mild" | "normal" | "streng" | "sehr streng"
    # | "eigene" (dann gelten die Werte unter serien/qualitaet unten)
    "aussortieren": {"staerke": "normal"},
    # Themen-Ordner (zusätzlich zu Ereignis/Alltag)
    "themen": {
        "inhalte_erkennen": True,  # Bildinhalte mit CLIP erkennen (~0,6 GB Download)
        "kinder": {"aktiv": True, "name": "Kinder", "sicherheit": 0.55, "seiten": 2},
        "freunde": {"aktiv": True, "name": "Mit Freunden", "seiten": 2},
        "haus": {"aktiv": True, "name": "Haus & Renovierung", "sicherheit": 0.55,
                 "seiten": 2,  # Seiten im Jahresrückblick
                 "eigenes_pdf": True, "max_bilder_pdf": 150},
        "sonstiges_seiten": 2,  # übrige Favoriten am Ende des Jahresrückblicks
    },
    # Beim Ablegen auf das NAS sehr große Fotos verkleinern/komprimieren.
    # Nur Fotos über max_megapixel werden verändert, kleinere 1:1 kopiert.
    # EXIF (Datum, GPS, Kamera) bleibt erhalten. Die Originale in den
    # Quellordnern bleiben bei "kopieren" unverändert.
    "ablage": {
        "verkleinern": False,
        "max_megapixel": 24,  # z.B. 24 MP = ca. 6000 x 4000 Pixel
        "jpeg_qualitaet": 90,  # 85-92 = kaum sichtbarer Unterschied
    },
    # Wohnort: "auto" (häufigster GPS-Ort) oder {lat: 50.1, lon: 7.5}
    "zuhause": "auto",
    # Automatische Reise-/Ausflugserkennung über GPS
    "reise": {
        "min_entfernung_km": 60,  # ab dieser Entfernung von zu Hause = unterwegs
        "max_luecke_tage": 2,  # Tage ohne GPS-Foto innerhalb einer Reise erlaubt
        "min_fotos": 15,  # ab so vielen Dateien wird eine GPS-Reise automatisch ein Ordner
        "min_vorschlag": 8,  # ab so vielen Dateien wird etwas als Ereignis VORGESCHLAGEN
        "dichte_min_pro_tag": 5,  # "fotoreicher Tag" = mind. so viele Fotos (und 2x Median)
    },
    # Datei mit deinen benannten Ereignissen (wird bei --interaktiv ergänzt)
    "ereignisse_datei": "ereignisse.yaml",
    # Duplikate (gleiches Bild, z.B. Original + WhatsApp-Kopie)
    "duplikate": {
        "phash_max": 6,  # max. Unterschied im Bild-Fingerabdruck (0 = identisch)
        "dhash_max": 8,
        # WhatsApp-Bilder haben keine genaue Uhrzeit: so viele Tage vor/nach
        # dem WhatsApp-Datum wird nach dem Original gesucht
        "fenster_tage": 60,
    },
    # Serienaufnahmen (mehrere Fotos derselben Szene innerhalb kurzer Zeit)
    "serien": {
        "max_sekunden": 30,  # max. Abstand zwischen zwei Fotos einer Serie
        "phash_max": 20,  # wie ähnlich die Fotos sein müssen (verschiedene Motive: >22)
        "aktion": "aussortieren",  # "aussortieren" oder "behalten"
        "behalten": 1,  # so viele der besten Fotos einer Serie bleiben
    },
    # Qualitätsgrenzen – darunter wird aussortiert
    "qualitaet": {
        "min_schaerfe": 40.0,  # Laplace-Varianz der schärfsten Bildbereiche
        "max_dunkel_anteil": 0.75,  # Anteil fast schwarzer Pixel
        "max_hell_anteil": 0.60,  # Anteil ausgebrannter Pixel
        "min_score": 15,  # Gesamtnote 0..100
    },
    # Favoriten (bekommen 5 Sterne und landen im Jahresrückblick)
    "favoriten": {
        "anteil": 0.15,  # Anteil der Fotos je Ereignis/Monat
        "min": 2,
        "min_ereignis": 4,  # Ereignisse bekommen mindestens so viele Favoriten
        "max": 12,
        "min_score": 60,
        "min_abstand_minuten": 5,  # Favoriten nicht alle aus derselben Minute
        "phash_min_abstand": 14,  # und nicht zu ähnlich zueinander
    },
    # Jahresrückblick-PDF
    "pdf": {
        "titel": "Jahresrückblick {jahr}",
        "untertitel": "Unser Jahr in Bildern",
        "min_sterne": 4,  # Bilder mit mindestens so vielen Sternen kommen rein
        "max_bilder_pro_seite": 6,
        "max_seiten_pro_ereignis": 2,  # Ereignis mit vielen Favoriten: bis zu so viele Seiten
        "max_seiten_pro_monat": 1,  # Alltag/Monat: Seiten je Monat
        "seitenzahlen": True,  # Seitenzahl unten
        "dpi": 150,  # Auflösung der Bilder im PDF (150 = gut am Bildschirm)
        "jpeg_qualitaet": 80,  # kleiner = kleinere Datei für E-Mail
        "zuschneiden": True,  # Gesichter-Zuschnitt aktivieren
        "zoom_staerke": 0.3,  # 0 = kaum zoomen, 1 = eng auf Personen
        "max_beschnitt": 0.15,  # höchstens so viel vom Bild abschneiden (0.15 = 15 %);
        # Hochkant-Bilder in breiten Feldern werden dann fast vollständig gezeigt
        "pptx": True,  # zusätzlich PowerPoint erzeugen (zum Nachbearbeiten)
        "pptx_kante": 1600,  # Auflösung der Bilder in der PowerPoint (Pixel)
        "schriftart": "",  # Pfad zu einer .ttf (leer = automatisch suchen)
        "bild_datum": True,  # kleines Datum unter jedem Bild
    },
    # Technisches
    "analyse": {
        "prozesse": 0,  # 0 = automatisch (Anzahl CPU-Kerne - 1)
        # Größte erlaubte Bildgröße in Megapixeln (Panoramas haben oft 100-200 MP;
        # Pillow warnt sonst schon ab 89 MP vor "Decompression Bombs")
        "max_megapixel": 600,
        "cache_datei": ".fotosort_cache.json",
        "modell_ordner": "models",
    },
}


def _tief_mischen(basis: dict, neu: dict) -> dict:
    """Verschachtelte Dictionaries zusammenführen (neu überschreibt basis)."""
    ergebnis = copy.deepcopy(basis)
    for schluessel, wert in (neu or {}).items():
        if isinstance(wert, dict) and isinstance(ergebnis.get(schluessel), dict):
            ergebnis[schluessel] = _tief_mischen(ergebnis[schluessel], wert)
        else:
            ergebnis[schluessel] = wert
    return ergebnis


def lade_config(pfad: str | Path) -> dict:
    """config.yaml lesen, mit Standardwerten auffüllen und Pfade auflösen."""
    pfad = Path(pfad)
    daten = {}
    if pfad.exists():
        with open(pfad, encoding="utf-8") as f:
            daten = yaml.safe_load(f) or {}
    cfg = _tief_mischen(STANDARD, daten)

    # Relative Pfade (Cache, Modelle, Ereignisse) beziehen sich auf den
    # Ordner der config.yaml
    basis = pfad.resolve().parent
    cfg["_basis"] = str(basis)
    for teil, schluessel in (
        ("analyse", "cache_datei"),
        ("analyse", "modell_ordner"),
    ):
        p = Path(cfg[teil][schluessel])
        cfg[teil][schluessel] = str(p if p.is_absolute() else basis / p)
    p = Path(cfg["ereignisse_datei"])
    cfg["ereignisse_datei"] = str(p if p.is_absolute() else basis / p)
    if isinstance(cfg["quellen"], str):
        cfg["quellen"] = [cfg["quellen"]]
    return cfg
