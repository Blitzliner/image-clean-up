"""
Duplikate, Serienaufnahmen und schlechte Bilder erkennen.

Jedes Bild bekommt dabei:
  status  : "behalten" oder "aussortiert"
  grund   : warum aussortiert (für die Vorschau / das Protokoll)
  serie   : Nummer der Serie (Bilder derselben Szene) – für die Vorschau

Wichtig für WhatsApp: Wird eine WhatsApp-Kopie als Duplikat eines Originals
erkannt, übernimmt sie dessen genaue Aufnahmezeit. So landet sie zeitlich
direkt neben dem Original (und im selben Ereignis), auch wenn sie erst
Wochen später verschickt wurde.
"""
from __future__ import annotations

import bisect
import datetime as dt

import numpy as np

# Je kleiner, desto lieber behalten wir das Bild bei exakten Duplikaten
QUELLEN_RANG = {"eigene": 0, "mann": 1, "sonstige": 2, "whatsapp": 3, "screenshot": 4}


def _zeit(i: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(i["zeit"])


def _hash_int(text: str | None) -> int:
    return int(text, 16) if text else 0


def _abstand(a: int, b: int) -> int:
    """Anzahl unterschiedlicher Bits zweier 64-bit-Hashes."""
    return (a ^ b).bit_count()


class _UnionFind:
    """Kleine Hilfsklasse, um zusammengehörige Bilder zu Gruppen zu verbinden."""

    def __init__(self, n: int):
        self.eltern = list(range(n))

    def finde(self, x: int) -> int:
        while self.eltern[x] != x:
            self.eltern[x] = self.eltern[self.eltern[x]]
            x = self.eltern[x]
        return x

    def verbinde(self, a: int, b: int) -> None:
        self.eltern[self.finde(a)] = self.finde(b)

    def gruppen(self) -> dict[int, list[int]]:
        g: dict[int, list[int]] = {}
        for i in range(len(self.eltern)):
            g.setdefault(self.finde(i), []).append(i)
        return g


def _aussortieren(item: dict, grund: str) -> None:
    item["status"] = "aussortiert"
    item["grund"] = grund


# ---------------------------------------------------------------------------
def exakte_duplikate(items: list[dict], videos: bool = False) -> int:
    """Identische Dateien (gleicher SHA1) – nur eine bleibt. Videos nur, wenn videos=True."""
    nach_hash: dict[str, list[dict]] = {}
    for i in items:
        if i["art"] == "video" and not videos:
            continue  # Videos werden (noch) nicht automatisch aussortiert
        if i.get("sha1"):
            nach_hash.setdefault(i["sha1"], []).append(i)
    anzahl = 0
    for gruppe in nach_hash.values():
        if len(gruppe) < 2:
            continue
        gruppe.sort(key=lambda i: (QUELLEN_RANG.get(i["quelle"], 9), i["zeit_unsicher"], i["name"]))
        for doppelt in gruppe[1:]:
            _aussortieren(doppelt, f"Exaktes Duplikat von {gruppe[0]['name']}")
            doppelt["duplikat_von"] = gruppe[0]["name"]
            anzahl += 1
    return anzahl


def aehnliche_duplikate(items: list[dict], cfg: dict) -> int:
    """
    Gleiches Motiv, aber andere Datei (z.B. WhatsApp-Kopie in kleinerer Auflösung).
    Das Bild mit der höchsten Auflösung bleibt, die anderen werden aussortiert.
    """
    p_max = cfg["duplikate"]["phash_max"]
    d_max = cfg["duplikate"]["dhash_max"]
    fenster = dt.timedelta(days=cfg["duplikate"]["fenster_tage"])
    kurz = dt.timedelta(minutes=2)

    fotos = [i for i in items if i.get("phash") and i["status"] == "behalten"]
    fotos.sort(key=_zeit)
    zeiten = [_zeit(i) for i in fotos]
    ph = np.array([_hash_int(i["phash"]) for i in fotos], dtype=np.uint64)
    dh = np.array([_hash_int(i["dhash"]) for i in fotos], dtype=np.uint64)
    uf = _UnionFind(len(fotos))

    for idx, foto in enumerate(fotos):
        # Bilder mit unsicherer Zeit (WhatsApp) werden in einem großen
        # Zeitfenster verglichen, alle anderen nur mit direkten Nachbarn.
        weite = fenster if foto["zeit_unsicher"] else kurz
        von = bisect.bisect_left(zeiten, zeiten[idx] - weite)
        bis = bisect.bisect_right(zeiten, zeiten[idx] + weite)
        if bis - von <= 1:
            continue
        p_abst = np.bitwise_count(ph[von:bis] ^ ph[idx])
        d_abst = np.bitwise_count(dh[von:bis] ^ dh[idx])
        for j in np.nonzero((p_abst <= p_max) & (d_abst <= d_max))[0]:
            j = int(j) + von
            if j == idx:
                continue
            # Seitenverhältnis muss passen (sonst evtl. nur Ausschnitt)
            a, b = foto, fotos[j]
            if a["breite"] and b["breite"]:
                va = a["breite"] / max(a["hoehe"], 1)
                vb = b["breite"] / max(b["hoehe"], 1)
                if abs(va - vb) > 0.15 and abs(va - 1 / vb) > 0.15:
                    continue
            uf.verbinde(idx, j)

    anzahl = 0
    for mitglieder in uf.gruppen().values():
        if len(mitglieder) < 2:
            continue
        gruppe = [fotos[m] for m in mitglieder]
        # Behalten: höchste Auflösung, dann beste Note, dann eigene Quelle
        gruppe.sort(key=lambda i: (-(i["breite"] * i["hoehe"]), -(i.get("score") or 0),
                                   QUELLEN_RANG.get(i["quelle"], 9)))
        sieger = gruppe[0]
        # Genaue Zeit an die Kopien mit unsicherer Zeit weitergeben
        sichere = sorted((g for g in gruppe if not g["zeit_unsicher"]), key=_zeit)
        for g in gruppe:
            if g["zeit_unsicher"] and sichere:
                g["zeit"] = sichere[0]["zeit"]
                g["zeit_herkunft"] = f"von {sichere[0]['name']}"
                g["zeit_unsicher"] = False
        for g in gruppe[1:]:
            _aussortieren(g, f"Duplikat (geringere Auflösung) von {sieger['name']}")
            g["duplikat_von"] = sieger["name"]
            anzahl += 1
    return anzahl


def serien(items: list[dict], cfg: dict) -> int:
    """
    Serienaufnahmen: aufeinanderfolgende, ähnliche Fotos innerhalb weniger
    Sekunden. Die besten "behalten" Bilder bleiben, der Rest wird aussortiert
    (wenn serien.aktion = "aussortieren").
    """
    sc = cfg["serien"]
    max_s = dt.timedelta(seconds=sc["max_sekunden"])
    fotos = sorted(
        (i for i in items if i.get("phash") and i["art"] == "foto"
         and i["status"] == "behalten" and not i["zeit_unsicher"]),
        key=_zeit,
    )
    alle_serien: list[list[dict]] = []
    aktuell: list[dict] = []
    for f in fotos:
        if aktuell and (
            _zeit(f) - _zeit(aktuell[-1]) <= max_s
            and _abstand(_hash_int(f["phash"]), _hash_int(aktuell[-1]["phash"])) <= sc["phash_max"]
        ):
            aktuell.append(f)
        else:
            if len(aktuell) > 1:
                alle_serien.append(aktuell)
            aktuell = [f]
    if len(aktuell) > 1:
        alle_serien.append(aktuell)

    anzahl = 0
    for nr, serie in enumerate(alle_serien, start=1):
        rangfolge = sorted(serie, key=lambda i: -(i.get("score") or 0))
        for i in serie:
            i["serie"] = nr
        if sc["aktion"] != "aussortieren":
            continue
        for schwaecher in rangfolge[sc["behalten"]:]:
            _aussortieren(schwaecher, f"Serie: schwächer als {rangfolge[0]['name']}")
            anzahl += 1
    return anzahl


def qualitaet_pruefen(items: list[dict], cfg: dict) -> int:
    """Unscharfe, zu dunkle oder überbelichtete Fotos aussortieren."""
    q = cfg["qualitaet"]
    anzahl = 0
    for i in items:
        if i["art"] != "foto" or i["status"] != "behalten" or "schaerfe" not in i:
            continue
        if i["schaerfe"] < q["min_schaerfe"]:
            _aussortieren(i, f"unscharf (Schärfe {i['schaerfe']:.0f})")
        elif i["dunkel_anteil"] > q["max_dunkel_anteil"]:
            _aussortieren(i, "zu dunkel")
        elif i["hell_anteil"] > q["max_hell_anteil"]:
            _aussortieren(i, "überbelichtet")
        elif (i.get("score") or 0) < q["min_score"]:
            _aussortieren(i, f"geringe Qualität (Note {i.get('score')})")
        else:
            continue
        anzahl += 1
    return anzahl


# Voreinstellungen für "Wie streng soll aussortiert werden?"
#   serien   : Fotos innerhalb max_sekunden mit Bild-Abstand <= phash_max = eine Serie
#   qualitaet: Mindestschärfe / Mindestnote
#   anteil   : pro Tag und Ordner nur die besten x % behalten (None = alle)
#   aussagekraft: Dokumente/Schilder/Karten (Inhaltserkennung) aussortieren
STAERKEN = {
    "mild": {"serien": {"max_sekunden": 15, "phash_max": 16},
             "qualitaet": {"min_schaerfe": 25, "min_score": 10}, "anteil": None, "aussagekraft": False},
    "normal": {"serien": {"max_sekunden": 30, "phash_max": 20},
               "qualitaet": {"min_schaerfe": 40, "min_score": 15}, "anteil": None, "aussagekraft": False},
    "streng": {"serien": {"max_sekunden": 120, "phash_max": 22},
               "qualitaet": {"min_schaerfe": 60, "min_score": 35}, "anteil": 0.7, "aussagekraft": True},
    "sehr streng": {"serien": {"max_sekunden": 600, "phash_max": 24},
                    "qualitaet": {"min_schaerfe": 90, "min_score": 50}, "anteil": 0.5, "aussagekraft": True},
}


def wende_staerke_an(cfg: dict) -> dict:
    """Werte der gewählten Filterstärke in eine Kopie der Konfiguration übernehmen."""
    import copy

    staerke = cfg["aussortieren"]["staerke"]
    if staerke not in STAERKEN:
        return {**cfg, "_anteil": None, "_aussagekraft": False}
    neu = copy.deepcopy({k: v for k, v in cfg.items() if not k.startswith("_")})
    neu.update({k: v for k, v in cfg.items() if k.startswith("_")})
    voreinstellung = STAERKEN[staerke]
    neu["serien"].update(voreinstellung["serien"])
    neu["qualitaet"].update(voreinstellung["qualitaet"])
    neu["_anteil"] = voreinstellung["anteil"]
    neu["_aussagekraft"] = voreinstellung["aussagekraft"]
    return neu


def wenig_aussagekraeftig(items: list[dict]) -> int:
    """Dokumente, Schilder, Karten usw. (laut Inhaltserkennung) aussortieren."""
    anzahl = 0
    for i in items:
        if i["art"] == "foto" and i["status"] == "behalten" and \
                (i.get("inhalt") or {}).get("dokument", 0) >= 0.6 and not i.get("gesichter"):
            _aussortieren(i, "wenig aussagekräftig (Dokument/Schild/Karte)")
            anzahl += 1
    return anzahl


def nur_die_besten(items: list[dict], anteil: float) -> int:
    """
    Pro Tag nur die besten x % der Fotos behalten (mind. 1 pro Tag).
    Wird bei "streng" / "sehr streng" verwendet.
    """
    import math

    pro_tag: dict[str, list[dict]] = {}
    for i in items:
        if i["art"] == "foto" and i["status"] == "behalten":
            pro_tag.setdefault(i["zeit"][:10], []).append(i)
    anzahl = 0
    for fotos in pro_tag.values():
        behalten = max(1, math.ceil(len(fotos) * anteil))
        for i in sorted(fotos, key=lambda f: -(f.get("score") or 0))[behalten:]:
            _aussortieren(i, f"schwächer als die besten {int(anteil * 100)} % des Tages")
            anzahl += 1
    return anzahl


def bereinigen(items: list[dict], cfg: dict) -> dict:
    """Alle Prüfungen nacheinander ausführen. Gibt eine kleine Statistik zurück."""
    cfg = wende_staerke_an(cfg)
    for i in items:
        i["status"] = "behalten"
        i["grund"] = ""
        i.pop("serie", None)
    statistik = {
        "exakte_duplikate": exakte_duplikate(items, cfg.get("videos_aussortieren", False)),
        "aehnliche_duplikate": aehnliche_duplikate(items, cfg),
        "serien_aussortiert": serien(items, cfg),
        "qualitaet_aussortiert": qualitaet_pruefen(items, cfg),
    }
    if cfg["_aussagekraft"]:
        statistik["wenig_aussagekraeftig"] = wenig_aussagekraeftig(items)
    if cfg["_anteil"]:
        statistik["nur_die_besten"] = nur_die_besten(items, cfg["_anteil"])
    items.sort(key=lambda i: (i["zeit"], i["name"]))
    return statistik
