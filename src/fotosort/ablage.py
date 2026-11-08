"""
Den Plan ausführen: Dateien auf das NAS kopieren bzw. verschieben.

Sicherheitsregeln:
  - Es wird NIE etwas gelöscht oder überschrieben.
  - "verschieben" = erst kopieren, Größe prüfen, dann Original entfernen.
  - Liegt die Zieldatei schon da (gleiche Größe), wird sie übersprungen.
    Du kannst das Skript also gefahrlos mehrmals laufen lassen.
  - Pro Jahr wird ein Protokoll (_fotosort_protokoll.csv) geschrieben:
    woher kam jede Datei, warum wurde sie aussortiert usw.
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import shutil
from pathlib import Path

from .bewertung import schreibe_sterne

PROTOKOLL_NAME = "_fotosort_protokoll.csv"
SPALTEN = ["ziel", "original", "zeit", "zeit_herkunft", "quelle", "art", "status",
           "grund", "favorit", "score", "gruppe", "gruppen_art", "themen", "haus_bereich",
           "personen", "kopien", "im_rueckblick", "breite", "hoehe", "gesichter"]


def protokoll_zeile(i: dict) -> dict:
    return {
        "ziel": i["ziel"], "original": i["pfad"], "zeit": i["zeit"],
        "zeit_herkunft": i.get("zeit_herkunft", ""), "quelle": i["quelle"], "art": i["art"],
        "status": i["status"], "grund": i.get("grund", ""),
        "favorit": "ja" if i.get("favorit") else "", "score": i.get("score") or "",
        "gruppe": i["gruppe"], "gruppen_art": i.get("gruppen_art", ""),
        "themen": "|".join(i.get("themen", [])), "haus_bereich": i.get("haus_bereich", ""),
        "personen": "|".join(i.get("personen", [])), "kopien": "|".join(i.get("kopien", [])),
        "im_rueckblick": "" if i.get("im_rueckblick", True) else "nein",
        "breite": i.get("breite", ""), "hoehe": i.get("hoehe", ""),
        "gesichter": len(i.get("gesichter", [])),
    }


def schreibe_csv(pfad: Path, zeilen: list[dict]) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig + Semikolon: öffnet sich in deutschem Excel direkt richtig
    with open(pfad, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=SPALTEN, delimiter=";")
        w.writeheader()
        w.writerows(zeilen)


def lese_protokoll(pfad: Path) -> dict[str, dict]:
    if not pfad.exists():
        return {}
    with open(pfad, newline="", encoding="utf-8-sig") as f:
        return {z["ziel"]: z for z in csv.DictReader(f, delimiter=";")}


def _gleiche_datei(a: Path, b: Path) -> bool:
    return b.exists() and a.stat().st_size == b.stat().st_size


def ablegen(items: list[dict], cfg: dict) -> dict:
    """Alle Dateien laut Plan ablegen. Gibt eine Statistik zurück."""
    verschieben = cfg["modus"] == "verschieben"
    statistik = {"kopiert": 0, "verschoben": 0, "uebersprungen": 0, "fehler": 0, "sterne": 0,
                 "themen_kopien": 0}
    pro_jahr: dict[Path, list[dict]] = {}

    for nr, i in enumerate(items, start=1):
        quelle, ziel = Path(i["pfad"]), Path(i["ziel"])
        try:
            if not quelle.exists():
                # schon bei einem früheren Lauf verschoben
                statistik["uebersprungen"] += 1
            elif _gleiche_datei(quelle, ziel) or (
                i.get("favorit") and ziel.exists()
                and abs(ziel.stat().st_size - quelle.stat().st_size) < 4096
            ):
                # schon da (Favoriten sind durch die Sterne ein paar Bytes größer)
                statistik["uebersprungen"] += 1
                if verschieben:
                    quelle.unlink()
            else:
                ziel.parent.mkdir(parents=True, exist_ok=True)
                if ziel.exists():  # anderer Inhalt unter gleichem Namen -> nummerieren
                    n = 1
                    while (k := ziel.with_name(f"{ziel.stem}_{n}{ziel.suffix}")).exists():
                        n += 1
                    ziel = k
                    i["ziel"] = str(ziel)
                shutil.copy2(quelle, ziel)
                if ziel.stat().st_size != quelle.stat().st_size:
                    raise OSError("Kopie unvollständig")
                if i.get("favorit") and schreibe_sterne(ziel, 5):
                    statistik["sterne"] += 1
                if cfg["dateizeit_setzen"]:
                    t = dt.datetime.fromisoformat(i["zeit"]).timestamp()
                    os.utime(ziel, (t, t))
                if verschieben:
                    quelle.unlink()
                    statistik["verschoben"] += 1
                else:
                    statistik["kopiert"] += 1
        except OSError as fehler:
            print(f"  FEHLER bei {quelle.name}: {fehler}")
            statistik["fehler"] += 1
            continue

        # Kopien in die Themen-Ordner (vom bereits abgelegten Hauptbild)
        for kopie in i.get("kopien", []):
            kopie = Path(kopie)
            try:
                if not kopie.exists() and ziel.exists():
                    kopie.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ziel, kopie)
                    statistik["themen_kopien"] += 1
            except OSError as fehler:
                print(f"  FEHLER bei Kopie {kopie.name}: {fehler}")
                statistik["fehler"] += 1

        jahr_ordner = Path(cfg["ziel"]) / str(i["jahr"])
        pro_jahr.setdefault(jahr_ordner, []).append(protokoll_zeile(i))
        if nr % 100 == 0:
            print(f"  {nr}/{len(items)}")

    # Protokolle zusammenführen (frühere Läufe bleiben erhalten)
    for jahr_ordner, zeilen in pro_jahr.items():
        pfad = jahr_ordner / PROTOKOLL_NAME
        alt = lese_protokoll(pfad)
        alt.update({z["ziel"]: z for z in zeilen})
        schreibe_csv(pfad, sorted(alt.values(), key=lambda z: z["zeit"]))
    return statistik
