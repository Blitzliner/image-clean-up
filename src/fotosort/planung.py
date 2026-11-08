"""
Plan erstellen: Was kommt wohin? Welche Bilder werden Favoriten?

Ergebnis pro Datei:
  favorit : True/False
  ziel    : vollständiger Zielpfad, z.B.
            //NAS/Fotos/2026/2026-05_Urlaub_Frankreich/20260524_143212_PXL_....jpg
            //NAS/Fotos/2026/2026-05_Urlaub_Frankreich/Aussortiert/IMG-....jpg
            //NAS/Fotos/2026/2026-03_März/20260312_142616.jpg
            //NAS/Fotos/2026/Screenshots/Screenshot_....jpg
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import PurePath

from .duplikate import _abstand, _hash_int

KORREKTUREN_DATEI = "korrekturen.json"


def _zeit(i: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(i["zeit"])


def waehle_favoriten(items: list[dict], cfg: dict) -> int:
    """
    Pro Ereignis/Monat die besten Fotos als Favoriten markieren.
    Damit nicht 5x dieselbe Szene gewählt wird, müssen Favoriten zeitlich
    und inhaltlich etwas Abstand zueinander haben.
    """
    fc = cfg["favoriten"]
    gruppen: dict[str, list[dict]] = {}
    for i in items:
        i["favorit"] = False
        if i["art"] == "foto" and i["status"] == "behalten":
            # Alltag ist ein Jahresordner -> Favoriten trotzdem je Monat wählen,
            # damit jeder Monat vertreten ist
            schluessel = i["gruppe"]
            if i.get("gruppen_art") in ("alltag", "haus"):
                schluessel = f"{i['gruppe']}|{i['zeit'][:7]}"
            gruppen.setdefault(schluessel, []).append(i)

    anzahl = 0
    for fotos in gruppen.values():
        # Ereignisse (Urlaube ...) bekommen etwas mehr Favoriten als Alltagsmonate
        minimum = fc["min_ereignis"] if any(f.get("ereignis") for f in fotos) else fc["min"]
        ziel_anzahl = max(minimum, min(fc["max"], round(len(fotos) * fc["anteil"])))
        kandidaten = sorted(
            (f for f in fotos if (f.get("score") or 0) >= fc["min_score"]),
            key=lambda f: -(f.get("score") or 0),
        )
        gewaehlt: list[dict] = []
        for f in kandidaten:
            if len(gewaehlt) >= ziel_anzahl:
                break
            zu_nah = any(
                abs(_zeit(f) - _zeit(g)) < dt.timedelta(minutes=fc["min_abstand_minuten"])
                and _abstand(_hash_int(f["phash"]), _hash_int(g["phash"])) < fc["phash_min_abstand"]
                for g in gewaehlt
            )
            if not zu_nah:
                gewaehlt.append(f)
        for f in gewaehlt:
            f["favorit"] = True
            anzahl += 1
    return anzahl


def neuer_dateiname(i: dict, umbenennen: bool) -> str:
    """Zeitstempel voranstellen, damit "Sortieren nach Name" chronologisch ist."""
    # Eigene Fotos (20260524_091837.jpg) heißen schon richtig, Screenshots
    # liegen in einem eigenen Ordner – beide bleiben unverändert.
    if not umbenennen or i["art"] == "screenshot" or re.match(r"\d{8}_\d{6}", i["name"]):
        return i["name"]
    praefix = _zeit(i).strftime("%Y%m%d_%H%M%S")
    return f"{praefix}_{i['name']}"


def zielpfade(items: list[dict], cfg: dict) -> None:
    """Zielpfad für jede Datei festlegen (Namenskollisionen werden nummeriert)."""
    ziel = cfg["ziel"].rstrip("/\\")
    vergeben: set[str] = set()
    for i in items:
        teile = [str(i["jahr"]), i["gruppe"]]
        if i["status"] == "aussortiert":
            teile.append(cfg["ordner"]["aussortiert"])
        name = neuer_dateiname(i, cfg["umbenennen"])
        stamm, endung = PurePath(name).stem, PurePath(name).suffix
        kandidat = "/".join([ziel, *teile, name])
        n = 1
        while kandidat.lower() in vergeben:
            kandidat = "/".join([ziel, *teile, f"{stamm}_{n}{endung}"])
            n += 1
        vergeben.add(kandidat.lower())
        i["ziel"] = kandidat
        # Zusätzliche Kopien in Themen-Ordner (Kinder, Mit Freunden, Haus)
        i["kopien"] = []
        for g in i.get("kopie_gruppen", []):
            kopie = "/".join([ziel, str(i["jahr"]), g, PurePath(kandidat).name])
            if kopie.lower() not in vergeben:
                vergeben.add(kopie.lower())
                i["kopien"].append(kopie)


def lade_korrekturen(cfg: dict) -> dict:
    """Manuelle Entscheidungen (aus der Oberfläche) laden: {quellpfad: {...}}"""
    import json
    from pathlib import Path

    pfad = Path(cfg["_basis"]) / KORREKTUREN_DATEI
    if pfad.exists():
        try:
            return json.loads(pfad.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def speichere_korrekturen(cfg: dict, korrekturen: dict) -> None:
    import json
    from pathlib import Path

    pfad = Path(cfg["_basis"]) / KORREKTUREN_DATEI
    pfad.write_text(json.dumps(korrekturen, indent=1, ensure_ascii=False), encoding="utf-8")


def wende_ordner_korrekturen_an(items: list[dict], korrekturen: dict, cfg: dict,
                                gruppen: list[dict]) -> int:
    """
    Manuell verschobene Bilder in ihr neues Ereignis (oder in den Alltag) setzen.
    korrekturen[pfad]["ordner"] = {"ereignis": "Urlaub Frankreich"}  oder  {"ereignis": None}
    Ein neuer Ereignisname legt einen neuen Ordner an (Startmonat = frühestes Bild).
    """
    from .ereignisse import MONATE, sicherer_name

    verschoben = [i for i in items if "ordner" in korrekturen.get(i["pfad"], {})
                  and i["art"] != "screenshot"]
    if not verschoben:
        return 0
    # vorhandene Ereignis-Ordner: Name -> (Ordner, Jahr, im_rueckblick)
    vorhanden = {}
    for i in items:
        if i.get("ereignis") and i not in verschoben:
            vorhanden.setdefault(i["ereignis"], (i["gruppe"], i["jahr"], i.get("im_rueckblick", True)))
    neue_start: dict[str, dt.datetime] = {}
    for i in verschoben:
        name = korrekturen[i["pfad"]]["ordner"].get("ereignis")
        if name and name not in vorhanden:
            t = _zeit(i)
            neue_start[name] = min(neue_start.get(name, t), t)
    for name, start in neue_start.items():
        vorhanden[name] = (f"{start:%Y-%m}_{sicherer_name(name)}", start.year, True)

    bekannt = {g["gruppe"] for g in gruppen}
    for i in verschoben:
        name = korrekturen[i["pfad"]]["ordner"].get("ereignis")
        t = _zeit(i)
        if name:
            i["gruppe"], i["jahr"], i["im_rueckblick"] = vorhanden[name]
            i["ereignis"], i["gruppen_art"] = name, "ereignis"
            art_name = {"name": name, "art": "ereignis"}
        else:
            i["ereignis"], i["gruppen_art"], i["jahr"], i["im_rueckblick"] = None, "alltag", t.year, True
            if cfg["ordner"]["alltag"] == "monat":
                i["gruppe"] = f"{t:%Y-%m}_{MONATE[t.month - 1]}"
            else:
                i["gruppe"] = sicherer_name(cfg["ordner"]["alltag_name"])
            art_name = {"name": f"{cfg['ordner']['alltag_name']} {t.year}", "art": "alltag"}
        i["gruppe_basis"], i["gruppen_art_basis"] = i["gruppe"], i["gruppen_art"]
        if i["gruppe"] not in bekannt:
            bekannt.add(i["gruppe"])
            gruppen.append({"gruppe": i["gruppe"], **art_name})
    return len(verschoben)


def wende_korrekturen_an(items: list[dict], korrekturen: dict) -> int:
    """
    Deine manuellen Entscheidungen haben immer Vorrang vor der Automatik.
    korrekturen = {quellpfad: {"status": "behalten"|"aussortiert", "favorit": True|False}}
    """
    anzahl = 0
    for i in items:
        k = korrekturen.get(i["pfad"])
        if not k:
            continue
        if "status" in k and k["status"] != i["status"]:
            i["status"] = k["status"]
            i["grund"] = "manuell aussortiert" if k["status"] == "aussortiert" else "manuell behalten"
        if "favorit" in k:
            i["favorit"] = bool(k["favorit"])
        if i["status"] == "aussortiert":
            i["favorit"] = False
        i["manuell"] = True
        anzahl += 1
    return anzahl
