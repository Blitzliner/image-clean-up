"""
Themen: Kinder, Mit Freunden, Haus & Renovierung.

Ein Foto kann mehrere Themen haben. Regeln (in dieser Reihenfolge):
  1. Deine manuellen Entscheidungen aus der Oberfläche (immer Vorrang)
  2. Kinder       : eine Person mit Rolle "Kind" ist drauf
                    ODER die Inhaltserkennung sieht sicher ein Kind
  3. Mit Freunden : eine Person mit Rolle "Freund" ist drauf.
                    Solange du noch niemanden als Freund benannt hast:
                    mind. 3 Gesichter, davon mind. 2 nicht Familie/Kind
  4. Haus         : die Inhaltserkennung sieht Haus/Renovierung/Garten
                    und kein Kind im Mittelpunkt

Wohin die Fotos kommen:
  - Kinder / Mit Freunden: zusätzlich als KOPIE in einen eigenen Ordner
  - Haus & Renovierung   : Alltagsfotos kommen direkt in den Haus-Ordner
                           (Unterordner je Bereich: Bad, Küche, ...);
                           Fotos aus einem Ereignis werden dorthin kopiert.
"""
from __future__ import annotations

from .ereignisse import sicherer_name

THEMEN = ("kinder", "freunde", "haus")


def bestimme_themen(items: list[dict], cfg: dict, korrekturen: dict) -> dict:
    tc = cfg["themen"]
    freunde_benannt = any("Freund" in (i.get("rollen") or []) for i in items)
    statistik = {t: 0 for t in THEMEN}
    for i in items:
        i["themen"] = []
        if i["art"] != "foto":
            continue
        inhalt = i.get("inhalt") or {}
        rollen = i.get("rollen") or []
        themen = set()

        if tc["kinder"]["aktiv"]:
            if "Kind" in rollen or inhalt.get("kinder", 0) >= tc["kinder"]["sicherheit"]:
                themen.add("kinder")

        if tc["freunde"]["aktiv"]:
            if "Freund" in rollen:
                themen.add("freunde")
            elif not freunde_benannt:
                ids = i.get("person_ids") or []
                fremd = sum(1 for pid in ids if not pid.startswith("p"))  # nicht benannt
                if len(i.get("gesichter", [])) >= 3 and fremd >= 2:
                    themen.add("freunde")

        if tc["haus"]["aktiv"]:
            if (inhalt.get("haus", 0) >= tc["haus"]["sicherheit"]
                    and inhalt.get("kinder", 0) < 0.3 and "Kind" not in rollen):
                themen.add("haus")

        # Manuelle Korrekturen (Häkchen in der Oberfläche)
        for t, an in (korrekturen.get(i["pfad"], {}).get("themen") or {}).items():
            (themen.add if an else themen.discard)(t)

        i["themen"] = sorted(themen)
        for t in themen:
            statistik[t] += 1
    return statistik


def ordner_name(cfg: dict, thema: str) -> str:
    return sicherer_name(cfg["themen"][thema]["name"]).replace("&", "und")


def themen_ordner(items: list[dict], cfg: dict) -> None:
    """
    Haus-Fotos aus dem Alltag bekommen den Haus-Ordner als Hauptort.
    Für alle anderen Themen werden Kopien vorgemerkt (i["kopie_gruppen"]).
    """
    for i in items:
        # Ursprünglichen Ordner merken, damit Themen-Änderungen umkehrbar sind
        i["gruppe"] = i.setdefault("gruppe_basis", i["gruppe"])
        i["gruppen_art"] = i.setdefault("gruppen_art_basis", i.get("gruppen_art", ""))
        i["kopie_gruppen"] = []
        if i["art"] != "foto":
            continue
        for t in i.get("themen", []):
            ordner = ordner_name(cfg, t)
            if t == "haus":
                bereich = sicherer_name(i.get("haus_bereich") or "Allgemein").replace("&", "und")
                ordner = f"{ordner}/{bereich}"
                if not i.get("ereignis"):
                    i["gruppe"] = ordner  # Hauptort
                    i["gruppen_art"] = "haus"
                    continue
            if i["status"] == "behalten":
                i["kopie_gruppen"].append(ordner)
