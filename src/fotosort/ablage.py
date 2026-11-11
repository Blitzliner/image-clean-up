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


def _gleiches_laufwerk(a: Path, b_ordner: Path) -> bool:
    """Liegen Datei und Zielordner auf demselben Laufwerk? (Dann geht Verschieben ohne Kopie.)"""
    try:
        while not b_ordner.exists() and b_ordner.parent != b_ordner:
            b_ordner = b_ordner.parent
        return os.stat(a).st_dev == os.stat(b_ordner).st_dev
    except OSError:
        return False


def _verschiebe(quelle: Path, ziel: Path) -> None:
    """
    Verschieben: auf demselben Laufwerk nur umbenennen (braucht KEINEN freien
    Speicher, dauert Millisekunden), sonst kopieren + prüfen + Original löschen.
    """
    if _gleiches_laufwerk(quelle, ziel.parent):
        try:
            os.replace(quelle, ziel)
            return
        except OSError:
            pass
    _kopiere(quelle, ziel)
    quelle.unlink()


def _themen_kopie(ziel: Path, kopie: Path, art: str) -> None:
    """Themen-Ordner: Hardlink (kein zusätzlicher Speicher) oder echte Kopie."""
    if art == "hardlink":
        try:
            os.link(ziel, kopie)
            return
        except OSError:
            pass  # z.B. NAS/Netzlaufwerk ohne Hardlinks -> normale Kopie
    _kopiere(ziel, kopie)


def pruefe_speicherplatz(items: list[dict], cfg: dict) -> bool:
    """Reicht der Platz im Ziel? Gibt False (mit Meldung) zurück, wenn nicht."""
    from .komprimieren import BILD_ENDUNGEN

    ziel = Path(cfg["ziel"])
    pruefen = ziel
    while not pruefen.exists() and pruefen.parent != pruefen:
        pruefen = pruefen.parent
    try:
        frei = shutil.disk_usage(pruefen).free
    except OSError:
        print(f"Hinweis: Speicherplatz von {ziel} konnte nicht geprüft werden.")
        return True
    ac = cfg["ablage"]
    verschieben = cfg["modus"] == "verschieben"
    themen_art = ac.get("themen_kopien", "hardlink")
    benoetigt = 0
    gleich_cache: dict = {}
    for i in items:
        if Path(i["ziel"]).exists() or not Path(i["pfad"]).exists():
            continue
        quelle = Path(i["pfad"])
        if quelle.parent not in gleich_cache:
            gleich_cache[quelle.parent] = _gleiches_laufwerk(quelle, pruefen)
        gleich = gleich_cache[quelle.parent]
        groesse = i.get("groesse", 0)
        if ac.get("verkleinern") and quelle.suffix.lower() in BILD_ENDUNGEN \
                and i.get("breite", 0) * i.get("hoehe", 0) > ac["max_megapixel"] * 1e6:
            groesse = min(groesse, int(ac["max_megapixel"] * 1e6 * 0.5))  # grobe Schätzung
        elif ac.get("videos_komprimieren") and i["art"] == "video":
            groesse = int(groesse * 0.5)
        if not (verschieben and gleich):  # Umbenennen auf gleichem Laufwerk kostet nichts
            benoetigt += groesse
        if themen_art == "kopie" or (themen_art == "hardlink" and not gleich):
            benoetigt += groesse * len(i.get("kopien", []))
    gb = lambda b: f"{b / 1e9:.1f} GB".replace(".", ",")
    print(f"Speicherplatz: benötigt ca. {gb(benoetigt)}, frei {gb(frei)}.")
    if benoetigt > frei * 0.98:
        print("FEHLER: Nicht genug Speicherplatz im Ziel – Ablage abgebrochen, nichts verändert.")
        print("  Tipps: Modus 'verschieben' (auf demselben Laufwerk braucht das keinen Platz), "
              "Themen-Ordner als 'Verknüpfung (Hardlink)' oder 'aus', große Fotos/Videos "
              "komprimieren – oder vorher 'Quellordner komprimieren'.")
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
    themen_art = cfg["ablage"].get("themen_kopien", "hardlink")
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
                from .komprimieren import bild_komprimieren, video_komprimieren, video_ziel_endung

                komprimiert = False
                if i["art"] == "video" and cfg["ablage"].get("videos_komprimieren"):
                    neues_ziel = ziel.with_suffix(video_ziel_endung(ziel))
                    komprimiert, hinweis = video_komprimieren(quelle, neues_ziel, cfg)
                    if komprimiert:
                        ziel = neues_ziel
                        i["ziel"] = str(ziel)
                        print(f"  Video {quelle.name}: {hinweis}")
                elif i["art"] in ("foto", "screenshot"):
                    komprimiert, _ = bild_komprimieren(quelle, ziel, cfg)
                if komprimiert:
                    statistik["verkleinert"] += 1
                    gespart += quelle.stat().st_size - ziel.stat().st_size
                elif verschieben:
                    _verschiebe(quelle, ziel)  # gleiches Laufwerk: nur umbenennen
                else:
                    _kopiere(quelle, ziel)
                if i.get("favorit") and schreibe_sterne(ziel, 5):
                    statistik["sterne"] += 1
                if cfg["dateizeit_setzen"]:
                    t = dt.datetime.fromisoformat(i["zeit"]).timestamp()
                    os.utime(ziel, (t, t))
                if verschieben:
                    if quelle.exists():
                        quelle.unlink()  # nach Komprimieren/Kopieren das Original entfernen
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
                if themen_art != "aus" and not kopie.exists() and ziel.exists():
                    kopie.parent.mkdir(parents=True, exist_ok=True)
                    _themen_kopie(ziel, kopie.with_suffix(ziel.suffix), themen_art)
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
        print(f"  {statistik['verkleinert']} Fotos/Videos komprimiert – {menge.replace('.', ',')} gespart.")
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
