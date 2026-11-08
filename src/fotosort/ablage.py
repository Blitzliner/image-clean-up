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
import time
from pathlib import Path

from .bewertung import schreibe_sterne
from .fortschritt import Fortschritt


def _kopiere(quelle: Path, ziel: Path, versuche: int = 3) -> None:
    """Kopieren mit Wiederholung (Netzlaufwerke haben manchmal kurze Aussetzer)."""
    for versuch in range(1, versuche + 1):
        try:
            shutil.copy2(quelle, ziel)
            if ziel.stat().st_size != quelle.stat().st_size:
                raise OSError("Kopie unvollständig")
            return
        except OSError:
            if versuch == versuche:
                raise
            time.sleep(2 * versuch)


VERKLEINERBAR = {".jpg", ".jpeg", ".png", ".webp"}


def verkleinert_ablegen(quelle: Path, ziel: Path, cfg: dict) -> bool:
    """
    Großes Foto verkleinert/komprimiert ablegen. Gibt False zurück, wenn das
    Foto klein genug ist (dann normal kopieren). EXIF + Farbprofil bleiben erhalten.
    Hinweis: Bei Pixel-"Motion Photos" geht das eingebettete Kurzvideo verloren.
    """
    ac = cfg["ablage"]
    if not ac.get("verkleinern") or quelle.suffix.lower() not in VERKLEINERBAR:
        return False
    from PIL import Image

    grenze = ac["max_megapixel"] * 1_000_000
    with Image.open(quelle) as im:
        breite, hoehe = im.size
        if breite * hoehe <= grenze:
            return False
        f = (grenze / (breite * hoehe)) ** 0.5
        neu = (max(1, int(breite * f)), max(1, int(hoehe * f)))
        exif = im.info.get("exif")
        icc = im.info.get("icc_profile")
        im.draft("RGB", neu)  # JPEG direkt verkleinert dekodieren (schnell, wenig Speicher)
        if im.mode not in ("RGB", "L", "RGBA"):
            im = im.convert("RGB")
        klein = im.resize(neu, Image.LANCZOS)
    tmp = ziel.with_name(ziel.name + ".tmp")
    optionen = {"optimize": True}
    if exif:
        optionen["exif"] = exif
    if icc:
        optionen["icc_profile"] = icc
    endung = quelle.suffix.lower()
    if endung in (".jpg", ".jpeg"):
        klein.save(tmp, "JPEG", quality=ac["jpeg_qualitaet"], **optionen)
    elif endung == ".webp":
        klein.save(tmp, "WEBP", quality=ac["jpeg_qualitaet"], **optionen)
    else:
        klein.save(tmp, "PNG", **optionen)
    os.replace(tmp, ziel)
    return True


def pruefe_speicherplatz(items: list[dict], cfg: dict) -> bool:
    """Reicht der Platz im Ziel? Gibt False (mit Meldung) zurück, wenn nicht."""
    ziel = Path(cfg["ziel"])
    pruefen = ziel
    while not pruefen.exists() and pruefen.parent != pruefen:
        pruefen = pruefen.parent
    try:
        frei = shutil.disk_usage(pruefen).free
    except OSError:
        print(f"Hinweis: Speicherplatz von {ziel} konnte nicht geprüft werden.")
        return True
    benoetigt = 0
    ac = cfg["ablage"]
    for i in items:
        if not Path(i["ziel"]).exists():
            groesse = i.get("groesse", 0)
            if ac.get("verkleinern") and i.get("breite", 0) * i.get("hoehe", 0) > ac["max_megapixel"] * 1e6:
                groesse = min(groesse, int(ac["max_megapixel"] * 1e6 * 0.5))  # grobe Schätzung
            benoetigt += groesse * (1 + len(i.get("kopien", [])))
    gb = lambda b: f"{b / 1e9:.1f} GB".replace(".", ",")
    print(f"Speicherplatz: benötigt ca. {gb(benoetigt)}, frei {gb(frei)}.")
    if benoetigt > frei * 0.98:
        print("FEHLER: Nicht genug Speicherplatz im Ziel – Ablage abgebrochen, nichts verändert.")
        return False
    return True

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
                 "themen_kopien": 0, "verkleinert": 0}
    pro_jahr: dict[Path, list[dict]] = {}
    # Was wurde früher schon abgelegt? (wichtig für verkleinerte Dateien, deren
    # Größe nicht mehr zum Original passt)
    frueher: dict[str, str] = {}
    for jahr in {i["jahr"] for i in items}:
        for zeile in lese_protokoll(Path(cfg["ziel"]) / str(jahr) / PROTOKOLL_NAME).values():
            frueher[zeile["ziel"]] = zeile.get("original", "")
    gespart = 0
    if not pruefe_speicherplatz(items, cfg):
        statistik["fehler"] = len(items)
        return statistik
    fortschritt = Fortschritt("Ablegen", len(items), "Dateien")
    fehlerliste: list[str] = []

    for nr, i in enumerate(items, start=1):
        quelle, ziel = Path(i["pfad"]), Path(i["ziel"])
        try:
            if not quelle.exists():
                # schon bei einem früheren Lauf verschoben
                statistik["uebersprungen"] += 1
            elif _gleiche_datei(quelle, ziel) or (
                ziel.exists() and frueher.get(str(ziel)) == str(quelle)
            ) or (
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
                if verkleinert_ablegen(quelle, ziel, cfg):
                    statistik["verkleinert"] += 1
                    gespart += quelle.stat().st_size - ziel.stat().st_size
                else:
                    _kopiere(quelle, ziel)
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
            fehlerliste.append(f"{quelle}: {fehler}")
            statistik["fehler"] += 1
            fortschritt.schritt()
            continue

        # Kopien in die Themen-Ordner (vom bereits abgelegten Hauptbild)
        for kopie in i.get("kopien", []):
            kopie = Path(kopie)
            try:
                if not kopie.exists() and ziel.exists():
                    kopie.parent.mkdir(parents=True, exist_ok=True)
                    _kopiere(ziel, kopie)
                    statistik["themen_kopien"] += 1
            except OSError as fehler:
                print(f"  FEHLER bei Kopie {kopie.name}: {fehler}")
                statistik["fehler"] += 1

        jahr_ordner = Path(cfg["ziel"]) / str(i["jahr"])
        pro_jahr.setdefault(jahr_ordner, []).append(protokoll_zeile(i))
        fortschritt.schritt()
        # Protokoll zwischendurch sichern (bei Abbruch weiß man, was schon da ist)
        if nr % 1000 == 0:
            _schreibe_protokolle(pro_jahr)
    fortschritt.fertig()
    if statistik["verkleinert"]:
        menge = f"{gespart / 1e9:.1f} GB" if gespart >= 1e9 else f"{gespart / 1e6:.0f} MB"
        print(f"  {statistik['verkleinert']} große Fotos verkleinert – {menge.replace('.', ',')} gespart.")
    _schreibe_protokolle(pro_jahr)
    if fehlerliste:
        datei = Path(cfg["_basis"]) / "ablage_fehler.txt"
        datei.write_text("\n".join(fehlerliste), encoding="utf-8")
        print(f"{len(fehlerliste)} Fehler – Liste in {datei}. Einfach erneut ablegen: "
              f"bereits kopierte Dateien werden übersprungen.")
    return statistik


def _schreibe_protokolle(pro_jahr: dict) -> None:

    # Protokolle zusammenführen (frühere Läufe bleiben erhalten)
    for jahr_ordner, zeilen in pro_jahr.items():
        pfad = jahr_ordner / PROTOKOLL_NAME
        alt = lese_protokoll(pfad)
        alt.update({z["ziel"]: z for z in zeilen})
        schreibe_csv(pfad, sorted(alt.values(), key=lambda z: z["zeit"]))
