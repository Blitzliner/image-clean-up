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
    korrekturen[pfad]["ordner"] = {"id": "a1b2c3d4", "ereignis": "Urlaub"}  oder  {"ereignis": None}
    (ältere Dateien nur mit Name). Eine neue Gruppe legt einen neuen Ordner an
    (Startmonat = frühestes Bild). Gleiche Namen sind erlaubt – entscheidend ist die id.
    """
    from .ereignisse import MONATE, eindeutiger_ordner, lade_ereignisse, sicherer_name

    verschoben = [i for i in items if "ordner" in korrekturen.get(i["pfad"], {})
                  and i["art"] != "screenshot"]
    if not verschoben:
        return 0
    # Ziel einer Korrektur: Ereignis-Kennung (neu) oder nur Name (ältere korrekturen.json)
    eigene = lade_ereignisse(cfg["ereignisse_datei"])
    name_zu_id = {}
    for e in eigene:
        name_zu_id.setdefault(e["name"], e["id"])
    id_zu_name = {e["id"]: e["name"] for e in eigene}
    nr_verschoben = {id(i) for i in verschoben}
    for j in items:  # Name -> Kennung eines Ereignisses, das gerade Bilder hat
        if j.get("ereignis_id") and id(j) not in nr_verschoben:
            name_zu_id.setdefault("§" + j["ereignis"], j["ereignis_id"])

    def ziel_von(i):
        o = korrekturen[i["pfad"]]["ordner"]
        if o.get("id"):
            return o["id"], id_zu_name.get(o["id"], o.get("ereignis") or "Gruppe")
        name = o.get("ereignis")
        if not name:
            return None, None
        return name_zu_id.get("§" + name) or name_zu_id.get(name) or f"name-{name}", name

    # vorhandene Ereignis-Ordner: Kennung -> (Ordner, Jahr, im_rueckblick)
    vorhanden = {}
    belegt_von = {}
    for i in items:
        if i.get("ereignis_id") and id(i) not in nr_verschoben:
            vorhanden.setdefault(i["ereignis_id"], (i["gruppe"], i["jahr"], i.get("im_rueckblick", True)))
            belegt_von.setdefault((i["jahr"], i["gruppe"]), ("vorhanden", i["ereignis_id"]))
    neue_start: dict[str, tuple] = {}
    for i in verschoben:
        eid, name = ziel_von(i)
        if eid and eid not in vorhanden:
            t = _zeit(i)
            alt = neue_start.get(eid)
            neue_start[eid] = (min(alt[0], t) if alt else t, name)
    for eid, (start, name) in neue_start.items():
        ordner = eindeutiger_ordner(belegt_von, start.year, f"{start:%Y-%m}_{sicherer_name(name)}", eid)
        vorhanden[eid] = (ordner, start.year, True)

    bekannt = {g["gruppe"] for g in gruppen}
    for i in verschoben:
        eid, name = ziel_von(i)
        t = _zeit(i)
        if eid:
            i["gruppe"], i["jahr"], i["im_rueckblick"] = vorhanden[eid]
            i["ereignis"], i["ereignis_id"], i["gruppen_art"] = name, eid, "ereignis"
            art_name = {"name": name, "art": "ereignis"}
        else:
            i["ereignis"], i["ereignis_id"] = None, None
            i["gruppen_art"], i["jahr"], i["im_rueckblick"] = "alltag", t.year, True
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
