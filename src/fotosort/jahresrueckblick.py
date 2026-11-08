"""
Jahresrückblick und Haus-Rückblick – als PowerPoint (.pptx) UND als PDF.

Die PowerPoint ist zum Nachbearbeiten gedacht: Bilder austauschen, den
Bildausschnitt verschieben (Bild anklicken -> "Zuschneiden"), Texte ändern,
Seiten hinzufügen. Danach in PowerPoint "Speichern unter -> PDF" für die
Großeltern. Zusätzlich erzeugt das Skript direkt ein fertiges PDF.

Aufbau des Jahresrückblicks:
  1. Titelseite (bestes Foto des Jahres)
  2. Ereignisse chronologisch – je 1 Seite, bei vielen Favoriten bis zu
     pdf.max_seiten_pro_ereignis Seiten
  3. Kinder, Mit Freunden   (je bis zu themen.<thema>.seiten Seiten)
  4. Haus & Renovierung     (ca. 2 Seiten ausgewählte Bilder)
  5. Sonstiges              (übrige Favoriten aus dem Alltag, bis zu 2 Seiten)

Haus-Rückblick (eigene Datei): alle behaltenen Haus-Fotos, sortiert nach
Bereich (Bad, Küche, ...) und innerhalb chronologisch.

Welche Bilder? Alle mit mind. pdf.min_sterne Sternen (Favoriten). Sterne
kannst du im Windows-Explorer ändern und den Rückblick neu erzeugen.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import itertools
import math
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from . import gesichter as gesichter_mod
from .ablage import PROTOKOLL_NAME
from .bewertung import lese_sterne
from .ereignisse import MONATE

# ---------------------------------------------------------------------------
# Gestaltung (alle Maße in mm, A4 quer)
# ---------------------------------------------------------------------------
SEITE_B, SEITE_H = 297, 210
RAND = 14
KOPF_H = 30  # Titel + Datum
FUSS_H = 12  # Seitenzahl
LUECKE = 5  # Abstand zwischen Bildern
PASSEPARTOUT = 1.6  # weißer Rahmen um jedes Bild

FARBEN = {
    "hintergrund": (250, 247, 242),  # warmes Cremeweiß
    "text": (43, 38, 34),
    "akzent": (181, 101, 29),  # Terrakotta
    "leise": (140, 128, 116),
    "schatten": (226, 219, 208),
    "rahmen": (255, 255, 255),
}

# ---------------------------------------------------------------------------
# Bilder aus dem Jahresordner einsammeln
# ---------------------------------------------------------------------------
def _zeit_aus_name(name: str) -> dt.datetime | None:
    m = re.match(r"(\d{8})_(\d{6})", name)
    if m:
        try:
            return dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            return None
    return None


def _titel_aus_ordner(ordnername: str) -> str:
    m = re.match(r"\d{4}-(\d{2})_(.*)", ordnername)
    if not m:
        return ordnername.replace("_", " ")
    monat, rest = m.groups()
    if rest == MONATE[int(monat) - 1]:
        return rest
    return rest.replace("_", " ")


def _zeitraum(von: dt.datetime, bis: dt.datetime) -> str:
    """Datum ohne Jahr, z.B. '24.05. – 28.05.' oder '26.07.'"""
    if von.date() == bis.date():
        return f"{von:%d.%m.}"
    return f"{von:%d.%m.} – {bis:%d.%m.}"


def sammle_bilder(jahr_ordner: Path, cfg: dict) -> list[dict]:
    """Alle behaltenen Fotos des Jahres laut Protokoll (+ Sterne aus der Datei)."""
    protokoll = jahr_ordner / PROTOKOLL_NAME
    bilder = []
    if protokoll.exists():
        with open(protokoll, newline="", encoding="utf-8-sig") as f:
            zeilen = list(csv.DictReader(f, delimiter=";"))
    else:
        zeilen = []
        for datei in jahr_ordner.rglob("*.jpg"):
            if cfg["ordner"]["aussortiert"] in datei.parts:
                continue
            zeilen.append({"ziel": str(datei), "art": "foto", "status": "behalten",
                           "gruppe": datei.parent.name, "gruppen_art": "ereignis"})
    for z in zeilen:
        pfad = Path(z["ziel"])
        if z.get("art") != "foto" or z.get("status") != "behalten" or not pfad.exists():
            continue
        if pfad.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
            continue
        zeit = (dt.datetime.fromisoformat(z["zeit"]) if z.get("zeit")
                else _zeit_aus_name(pfad.name) or dt.datetime.fromtimestamp(pfad.stat().st_mtime))
        bilder.append({
            "pfad": pfad, "zeit": zeit, "score": float(z.get("score") or 0),
            "gruppe": z.get("gruppe", ""), "gruppen_art": z.get("gruppen_art") or "alltag",
            "themen": set(filter(None, (z.get("themen") or "").split("|"))),
            "haus_bereich": z.get("haus_bereich") or "",
            "sterne": lese_sterne(pfad),
            "im_rueckblick": z.get("im_rueckblick") != "nein",
        })
    return bilder


def _verteilen(bilder: list[dict], max_seiten: int, pro_seite: int) -> list[list[dict]]:
    """Beste Bilder auswählen und gleichmäßig auf Seiten verteilen (chronologisch)."""
    if not bilder:
        return []
    n_seiten = max(1, min(max_seiten, math.ceil(len(bilder) / pro_seite)))
    auswahl = sorted(sorted(bilder, key=lambda b: -b["score"])[:n_seiten * pro_seite],
                     key=lambda b: b["zeit"])
    n_seiten = math.ceil(len(auswahl) / pro_seite)
    groessen = [len(auswahl) // n_seiten + (1 if k < len(auswahl) % n_seiten else 0)
                for k in range(n_seiten)]
    seiten, start = [], 0
    for g in groessen:
        seiten.append(auswahl[start:start + g])
        start += g
    return seiten


def _seiten(titel: str, untertitel: str, gruppen: list[list[dict]]) -> list[dict]:
    n = len(gruppen)
    return [{"titel": titel, "untertitel": untertitel,
             "teil": f"{k + 1}/{n}" if n > 1 else "", "bilder": g}
            for k, g in enumerate(gruppen)]


def plane_jahresrueckblick(bilder: list[dict], cfg: dict) -> list[dict]:
    """
    Seiten des Jahresrückblicks planen. Wird auch für die Vorschau in der
    Oberfläche verwendet (dort mit den Favoriten aus dem Plan statt der Sterne).
    Ereignisse mit "nicht im Rückblick" werden komplett weggelassen.
    """
    pc, tc = cfg["pdf"], cfg["themen"]
    bilder = [b for b in bilder if b.get("im_rueckblick", True)]
    pro_seite = pc["max_bilder_pro_seite"]
    favoriten = [b for b in bilder if b["sterne"] >= pc["min_sterne"]]
    benutzt: set = set()
    seiten: list[dict] = []

    # 1) Ereignisse chronologisch
    ereignisse: dict[str, list[dict]] = {}
    alle_je_gruppe: dict[str, list[dict]] = {}
    for b in bilder:
        alle_je_gruppe.setdefault(b["gruppe"], []).append(b)
    for b in favoriten:
        if b["gruppen_art"] == "ereignis":
            ereignisse.setdefault(b["gruppe"], []).append(b)
    for gruppe in sorted(ereignisse, key=lambda g: min(b["zeit"] for b in alle_je_gruppe[g])):
        alle = alle_je_gruppe[gruppe]
        verteilt = _verteilen(ereignisse[gruppe], pc["max_seiten_pro_ereignis"], pro_seite)
        seiten += _seiten(_titel_aus_ordner(gruppe),
                          _zeitraum(min(b["zeit"] for b in alle), max(b["zeit"] for b in alle)),
                          verteilt)
        benutzt.update(id(b) for g in verteilt for b in g)

    # 2) Themen – erst "Mit Freunden" (seltener), dann Kinder, dann Haus.
    #    Bevorzugt noch nicht verwendete Favoriten; reicht das nicht für eine
    #    Seite, werden die besten übrigen Bilder des Themas ergänzt.
    abschnitte = {}
    for thema in ("freunde", "kinder", "haus"):
        t = tc[thema]
        if not t["aktiv"] or not t["seiten"]:
            continue

        def passt(b, thema=thema):
            return id(b) not in benutzt and (
                thema in b["themen"] or (thema == "haus" and b["gruppen_art"] == "haus"))

        kandidaten = [b for b in favoriten if passt(b)]
        if len(kandidaten) < pro_seite:
            extra = sorted((b for b in bilder if passt(b) and b not in kandidaten),
                           key=lambda b: -b["score"])
            kandidaten += extra[:pro_seite - len(kandidaten)]
        if not kandidaten:
            # Thema soll immer vorkommen: dann dürfen sich Bilder wiederholen
            kandidaten = sorted((b for b in bilder if thema in b["themen"]
                                 or (thema == "haus" and b["gruppen_art"] == "haus")),
                                key=lambda b: -(b["score"] + 100 * (b["sterne"] >= 4)))[:pro_seite]
        verteilt = _verteilen(kandidaten, t["seiten"], pro_seite)
        abschnitte[thema] = verteilt
        benutzt.update(id(b) for g in verteilt for b in g)
    for thema in ("kinder", "freunde", "haus"):  # Reihenfolge im Rückblick
        verteilt = abschnitte.get(thema)
        t = tc[thema]
        if verteilt:
            alle = [b for g in verteilt for b in g]
            seiten += _seiten(t["name"], _zeitraum(min(b["zeit"] for b in alle),
                                                   max(b["zeit"] for b in alle)), verteilt)

    # 3) Sonstiges: übrige Alltags-Favoriten
    rest = [b for b in favoriten if id(b) not in benutzt and b["gruppen_art"] == "alltag"]
    verteilt = _verteilen(rest, tc["sonstiges_seiten"], pro_seite)
    if verteilt:
        seiten += _seiten("Sonstiges", "", verteilt)
    return seiten


def plane_haus_rueckblick(bilder: list[dict], cfg: dict) -> list[dict]:
    from .inhalt import HAUS_BEREICHE

    haus = [b for b in bilder if b["gruppen_art"] == "haus" or "haus" in b["themen"]]
    grenze = cfg["themen"]["haus"]["max_bilder_pdf"]
    haus = sorted(haus, key=lambda b: -(b["score"] + 100 * (b["sterne"] >= 4)))[:grenze]
    reihenfolge = list(HAUS_BEREICHE) + ["Allgemein", ""]
    seiten = []
    for bereich in sorted({b["haus_bereich"] for b in haus},
                          key=lambda x: reihenfolge.index(x) if x in reihenfolge else 99):
        teil = sorted((b for b in haus if b["haus_bereich"] == bereich), key=lambda b: b["zeit"])
        pro = cfg["pdf"]["max_bilder_pro_seite"]
        n = math.ceil(len(teil) / pro)  # gleichmäßig verteilen, z.B. 14 -> 5/5/4
        groessen = [len(teil) // n + (1 if k < len(teil) % n else 0) for k in range(n)]
        gruppen, start = [], 0
        for g in groessen:
            gruppen.append(teil[start:start + g])
            start += g
        seiten += _seiten(bereich or "Haus", _zeitraum(teil[0]["zeit"], teil[-1]["zeit"]), gruppen)
    return seiten


# ---------------------------------------------------------------------------
# Zuschnitt und Platzierung
# ---------------------------------------------------------------------------
def zuschnitt(breite: int, hoehe: int, gesichter: list, ziel_format: float,
              zoom: float) -> tuple[int, int, int, int]:
    """
    Ausschnitt (links, oben, rechts, unten) im Seitenverhältnis ziel_format.
    Ohne Gesichter mittig (Hochkant leicht nach oben), mit Gesichtern um die
    Personen herum; zoom (0..1) steuert, wie eng geschnitten wird.
    """
    if breite / hoehe > ziel_format:
        max_h, max_b = hoehe, hoehe * ziel_format
    else:
        max_b, max_h = breite, breite / ziel_format
    if gesichter:
        wichtig = _wichtige(gesichter)
        x0 = min(g[0] for g in wichtig) * breite
        y0 = min(g[1] for g in wichtig) * hoehe
        x1 = max(g[0] + g[2] for g in wichtig) * breite
        y1 = max(g[1] + g[3] for g in wichtig) * hoehe
        fb = np.mean([g[2] for g in wichtig]) * breite
        fh = np.mean([g[3] for g in wichtig]) * hoehe
        x0, x1 = max(0, x0 - 0.8 * fb), min(breite, x1 + 0.8 * fb)
        y0, y1 = max(0, y0 - 0.6 * fh), min(hoehe, y1 + 1.8 * fh)
        eng_b = min(max(x1 - x0, (y1 - y0) * ziel_format), max_b)
        eng_b = max(eng_b, max_b * 0.45)
        b = max_b - zoom * (max_b - eng_b)
        h = b / ziel_format
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    else:
        b, h = max_b, max_h
        mx, my = breite / 2, hoehe * (0.42 if hoehe > breite else 0.5)
    links = min(max(0, mx - b / 2), breite - b)
    oben = min(max(0, my - h / 2), hoehe - h)
    return int(links), int(oben), int(links + b), int(oben + h)


def _wichtige(gesichter: list) -> list:
    groesstes = max(g[2] for g in gesichter)
    return [g for g in gesichter if g[2] >= groesstes * 0.4]


def _schneidet_gesicht_ab(box, breite: int, hoehe: int, gesichter: list) -> bool:
    if not gesichter:
        return False
    l, o, r, u = box
    for g in _wichtige(gesichter):
        gx0, gy0 = g[0] * breite, g[1] * hoehe
        gx1, gy1 = gx0 + g[2] * breite, gy0 + g[3] * hoehe
        if gx0 < l - 2 or gx1 > r + 2 or gy0 < o - 2 or gy1 > u + 2:
            return True
    return False


def _gesichter_box(breite, hoehe, gesichter) -> tuple[float, float, float, float]:
    wichtig = _wichtige(gesichter)
    rx = 0.35 * np.mean([g[2] for g in wichtig]) * breite
    ry = 0.35 * np.mean([g[3] for g in wichtig]) * hoehe
    return (max(0, min(g[0] for g in wichtig) * breite - rx),
            max(0, min(g[1] for g in wichtig) * hoehe - ry),
            min(breite, max(g[0] + g[2] for g in wichtig) * breite + rx),
            min(hoehe, max(g[1] + g[3] for g in wichtig) * hoehe + ry * 3))


def _erweitere(box, breite, hoehe, min_b, min_h, schutz=None):
    """Box auf mind. min_b x min_h vergrößern (zentriert, im Bild, Schutzbereich drin)."""
    l, o, r, u = box
    b, h = max(r - l, min(breite, min_b)), max(u - o, min(hoehe, min_h))
    mx, my = (l + r) / 2, (o + u) / 2
    if schutz:
        sx0, sy0, sx1, sy1 = schutz
        b, h = max(b, min(breite, sx1 - sx0)), max(h, min(hoehe, sy1 - sy0))
        mx = min(max(mx, sx1 - b / 2), sx0 + b / 2)
        my = min(max(my, sy1 - h / 2), sy0 + h / 2)
    l = min(max(0, mx - b / 2), breite - b)
    o = min(max(0, my - h / 2), hoehe - h)
    return int(l), int(o), int(l + b), int(o + h)


def endgueltiger_ausschnitt(bild: dict, feld_format: float, cfg: dict) -> tuple:
    """
    Ausschnitt fürs Feld unter Beachtung aller Regeln:
      1. auf Gesichter zuschneiden (pdf.zoom_staerke)
      2. niemals ein Gesicht abschneiden
      3. höchstens pdf.max_beschnitt vom Bild wegnehmen (Hochkant bleibt fast ganz)
    Passt der Ausschnitt dann nicht mehr genau ins Feld, wird er eingepasst
    (das Feld wird etwas schmaler/niedriger).
    """
    pc = cfg["pdf"]
    im = bild["_bild"]
    W, H = im.width, im.height
    g = bild["gesichter"] if pc["zuschneiden"] else []
    box = zuschnitt(W, H, g, feld_format, pc["zoom_staerke"] if pc["zuschneiden"] else 0)
    schutz = _gesichter_box(W, H, g) if g else None
    if _schneidet_gesicht_ab(box, W, H, g):
        box = _erweitere(box, W, H, 0, 0, schutz)
    # Höchstens max_beschnitt wegschneiden
    behalten = 1 - pc["max_beschnitt"]
    l, o, r, u = box
    if (r - l) * (u - o) < behalten * W * H:
        if r - l < W and u - o < H:  # in beide Richtungen beschnitten (Zoom)
            f = math.sqrt(behalten * W * H / ((r - l) * (u - o)))
            box = _erweitere(box, W, H, (r - l) * f, (u - o) * f, schutz)
        elif r - l < W:
            box = _erweitere(box, W, H, behalten * W, 0, schutz)
        else:
            box = _erweitere(box, W, H, 0, behalten * H, schutz)
        l, o, r, u = box
        if (r - l) * (u - o) < behalten * W * H * 0.98:  # noch zu klein -> andere Richtung
            box = _erweitere(box, W, H, behalten * W * H / max(u - o, 1),
                             behalten * W * H / max(r - l, 1), schutz)
    return box


def platzieren(bild: dict, x, y, b, h, cfg: dict) -> dict:
    """Bild in ein Feld setzen. Rückgabe: Position/Größe (mm) + Ausschnitt (px)."""
    box = endgueltiger_ausschnitt(bild, b / h, cfg)
    bw, bh = box[2] - box[0], box[3] - box[1]
    faktor = min(b / bw, h / bh)
    nb, nh = bw * faktor, bh * faktor
    return {"bild": bild, "x": x + (b - nb) / 2, "y": y + (h - nh) / 2, "b": nb, "h": nh,
            "box": box, "feld": (x, y, b, h)}


def _vorbereiten(bild: dict, modell: str | None, kante: int) -> None:
    im = Image.open(bild["pfad"])
    im.draft("RGB", (kante, kante))
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((kante, kante), Image.LANCZOS)
    bild["_bild"] = im
    klein = im.copy()
    klein.thumbnail((1024, 1024))
    bild["gesichter"] = gesichter_mod.finde_gesichter(np.asarray(klein), modell)


def fokus_ausschnitt(bild: dict, cfg: dict) -> tuple[int, int, int, int]:
    """
    Ausschnitt im EIGENEN Seitenverhältnis des Bildes: Hochkant bleibt hochkant,
    nichts Wichtiges geht verloren. Bei Personen wird leicht auf sie gezoomt
    (höchstens pdf.max_beschnitt der Fläche, nie ein Gesicht abgeschnitten).
    """
    pc = cfg["pdf"]
    im = bild["_bild"]
    W, H = im.width, im.height
    g = bild["gesichter"]
    if not (pc["zuschneiden"] and g and pc["zoom_staerke"] > 0):
        return 0, 0, W, H
    behalten = 1 - pc["max_beschnitt"] * min(1.0, pc["zoom_staerke"] * 2)
    f = math.sqrt(behalten)
    b, h = W * f, H * f
    sx0, sy0, sx1, sy1 = _gesichter_box(W, H, g)
    mx, my = (sx0 + sx1) / 2, (sy0 + sy1) / 2
    box = (int(min(max(0, mx - b / 2), W - b)), int(min(max(0, my - h / 2), H - h)), 0, 0)
    box = (box[0], box[1], int(box[0] + b), int(box[1] + h))
    if _schneidet_gesicht_ab(box, W, H, g):
        box = _erweitere(box, W, H, 0, 0, (sx0, sy0, sx1, sy1))
    return box


def _guete(rechtecke: list) -> float:
    """
    Wie gut ist ein Layout? Summe der Wurzeln der Bildflächen: belohnt große
    Bilder, aber ausgewogen (ein Riesenbild + Briefmarken schneidet schlecht ab).
    Sehr kleine Bilder (< 35 mm) werden bestraft.
    """
    if not rechtecke or any(r is None for r in rechtecke):
        return 0.0
    wert = sum(math.sqrt(r[2] * r[3]) for r in rechtecke)
    wert -= sum(40 for r in rechtecke if min(r[2], r[3]) < 35)
    return wert


def _aufteilungen(n: int, k: int):
    """Alle Möglichkeiten, n Bilder (Reihenfolge bleibt) auf k Zeilen zu verteilen."""
    for schnitte in itertools.combinations(range(1, n), k - 1):
        grenzen = (0, *schnitte, n)
        yield [list(range(grenzen[j], grenzen[j + 1])) for j in range(k)]


def _blocksatz(formate: list[float], x, y, b, h, spalten: bool = False):
    """
    Blocksatz: Bilder in Zeilen (oder Spalten) so skalieren, dass jede Zeile
    genau die Breite füllt – Bilder bleiben vollständig, ohne Beschnitt.
    Rückgabe: (genutzte Fläche, [(x, y, b, h) je Bild]) – das Beste aller Aufteilungen.
    """
    n = len(formate)
    if spalten:  # Spalten = Zeilen eines um 90° gedrehten Problems
        flaeche, rechtecke = _blocksatz([1 / a for a in formate], y, x, h, b)
        return flaeche, [(rx, ry, rb, rh) for (ry, rx, rh, rb) in rechtecke]
    bestes = (0.0, [])
    for k in range(1, min(n, 3) + 1):
        for zeilen in _aufteilungen(n, k):
            hoehen = [(b - LUECKE * (len(z) - 1)) / sum(formate[i] for i in z) for z in zeilen]
            gesamt = sum(hoehen) + LUECKE * (k - 1)
            s = min(1.0, h / gesamt)
            if any(hh * s < 25 for hh in hoehen):  # keine Mini-Zeilen
                continue
            # zentrieren
            block_h = gesamt * s
            yy = y + (h - block_h) / 2
            rechtecke = [None] * n
            for j, z in enumerate(zeilen):
                hh = hoehen[j] * s
                zeilen_b = sum(formate[i] * hh for i in z) + LUECKE * (len(z) - 1)
                xx = x + (b - zeilen_b) / 2
                for i in z:
                    rechtecke[i] = (xx, yy, formate[i] * hh, hh)
                    xx += formate[i] * hh + LUECKE
                yy += hh + LUECKE
            wert = _guete(rechtecke)
            if wert > bestes[0]:
                bestes = (wert, rechtecke)
    return bestes


def _seiten_layout(formate: list[float], scores: list[float], x, y, b, h):
    """
    Bestes Layout aus: Zeilen-Blocksatz, Spalten-Blocksatz oder
    "ein großes Bild + Blocksatz daneben/darunter".
    """
    kandidaten = [_blocksatz(formate, x, y, b, h), _blocksatz(formate, x, y, b, h, spalten=True)]
    n = len(formate)
    if n >= 3:
        for held in range(n):
            rest = [i for i in range(n) if i != held]
            a = formate[held]
            # großes Bild links, Rest rechts
            hb = min(h * a, b * 0.62)
            hh = hb / a
            fl, rr = _blocksatz([formate[i] for i in rest], x + hb + LUECKE, y, b - hb - LUECKE, h)
            if rr:
                fl2, rr2 = _blocksatz([formate[i] for i in rest], x + hb + LUECKE, y,
                                      b - hb - LUECKE, h, spalten=True)
                if fl2 > fl:
                    fl, rr = fl2, rr2
                rechtecke = [None] * n
                rechtecke[held] = (x, y + (h - hh) / 2, hb, hh)
                for i, r in zip(rest, rr):
                    rechtecke[i] = r
                # kleiner Bonus, wenn das große Bild ein besonders gutes ist
                kandidaten.append((_guete(rechtecke) + 0.05 * scores[held], rechtecke))
    return max(kandidaten, key=lambda k: k[0])[1]


def layout_seite(seite: dict, cfg: dict, modell, kante: int) -> list[dict]:
    """Alle Bilder einer Inhaltsseite platzieren (vollständig, im Blocksatz)."""
    for b in seite["bilder"]:
        if "_bild" not in b:
            _vorbereiten(b, modell, kante)
    boxen = [fokus_ausschnitt(b, cfg) for b in seite["bilder"]]
    formate = [(r - l) / max(u - o, 1) for l, o, r, u in boxen]
    fx, fy = RAND + PASSEPARTOUT, RAND + KOPF_H
    fb, fh = SEITE_B - 2 * RAND - 2 * PASSEPARTOUT, SEITE_H - fy - FUSS_H - 3
    rechtecke = _seiten_layout(formate, [b["score"] for b in seite["bilder"]], fx, fy, fb, fh)
    return [{"bild": b, "x": r[0], "y": r[1], "b": r[2], "h": r[3], "box": box}
            for b, r, box in zip(seite["bilder"], rechtecke, boxen)]


# ---------------------------------------------------------------------------
# Ausgabe: PDF (fpdf2)
# ---------------------------------------------------------------------------
def _schriften() -> dict:
    """Schriftarten suchen (Windows zuerst, dann Linux/macOS)."""
    kandidaten = {
        "titel": ["C:/Windows/Fonts/georgiab.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
                  "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"],
        "text": ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/System/Library/Fonts/Supplemental/Arial.ttf"],
        "text_fett": ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                      "/System/Library/Fonts/Supplemental/Arial Bold.ttf"],
    }
    return {k: next((p for p in v if Path(p).exists()), None) for k, v in kandidaten.items()}


class _PdfAusgabe:
    def __init__(self, cfg: dict, titel: str):
        from fpdf import FPDF

        self.cfg = cfg
        self.pdf = FPDF(orientation="L", unit="mm", format="A4")
        self.pdf.set_auto_page_break(False)
        self.pdf.set_title(titel)
        s = _schriften()
        self.unicode = bool(s["text"])
        if s["text"]:
            self.pdf.add_font("Text", "", s["text"])
            self.pdf.add_font("Text", "B", s["text_fett"] or s["text"])
            self.pdf.add_font("Titel", "", s["titel"] or s["text_fett"] or s["text"])
            self.titel_schrift, self.text_schrift = "Titel", "Text"
        else:
            self.titel_schrift = self.text_schrift = "Helvetica"
        self.seitennr = 0

    def _t(self, text: str) -> str:
        if self.unicode:
            return text
        return text.replace("–", "-").encode("latin-1", "replace").decode("latin-1")

    def _farbe(self, name, art="text"):
        r, g, b = FARBEN[name]
        {"text": self.pdf.set_text_color, "fill": self.pdf.set_fill_color,
         "draw": self.pdf.set_draw_color}[art](r, g, b)

    def neue_seite(self):
        self.pdf.add_page()
        self._farbe("hintergrund", "fill")
        self.pdf.rect(0, 0, SEITE_B, SEITE_H, style="F")

    def text(self, x, y, b, h, text, schrift, groesse, farbe, ausrichtung="L", fett=False):
        self.pdf.set_font(schrift, "B" if fett and schrift == self.text_schrift else "", groesse)
        self._farbe(farbe)
        self.pdf.set_xy(x, y)
        self.pdf.cell(b, h, self._t(text), align=ausrichtung)

    def bild(self, p: dict):
        pc = self.cfg["pdf"]
        x, y, b, h = p["x"], p["y"], p["b"], p["h"]
        # dezenter Schatten + weißes Passepartout
        self._farbe("schatten", "fill")
        self.pdf.rect(x - PASSEPARTOUT + 0.8, y - PASSEPARTOUT + 1.0,
                      b + 2 * PASSEPARTOUT, h + 2 * PASSEPARTOUT, style="F")
        self._farbe("rahmen", "fill")
        self.pdf.rect(x - PASSEPARTOUT, y - PASSEPARTOUT, b + 2 * PASSEPARTOUT, h + 2 * PASSEPARTOUT, style="F")
        px_b, px_h = max(1, int(b / 25.4 * pc["dpi"])), max(1, int(h / 25.4 * pc["dpi"]))
        teil = p["bild"]["_bild"].crop(p["box"]).resize((px_b, px_h), Image.LANCZOS)
        puffer = io.BytesIO()
        teil.save(puffer, format="JPEG", quality=pc["jpeg_qualitaet"], optimize=True)
        puffer.seek(0)
        self.pdf.image(puffer, x=x, y=y, w=b, h=h)
        if pc["bild_datum"]:
            text = f"{p['bild']['zeit']:%d.%m.}"
            self.pdf.set_font(self.text_schrift, "", 9)
            tb = self.pdf.get_string_width(text) + 4
            with self.pdf.local_context(fill_opacity=0.75):
                self.pdf.set_fill_color(255, 255, 255)
                self.pdf.rect(x + 2, y + h - 7, tb, 5, style="F", round_corners=True, corner_radius=1.2)
            self._farbe("text")
            self.pdf.set_xy(x + 2, y + h - 7)
            self.pdf.cell(tb, 5, text, align="C")

    def kopf(self, titel, untertitel, teil):
        self.text(RAND, RAND - 2, SEITE_B * 0.7, 14, titel, self.titel_schrift, 28, "text")
        rechts = untertitel + (f"   ·   Teil {teil}" if teil else "")
        if rechts:
            self.text(RAND, RAND + 13, SEITE_B * 0.7, 10, rechts, self.text_schrift, 18, "akzent")
        self._balken(RAND, RAND + 25, 16)

    def _balken(self, x, y, b):
        self._farbe("akzent", "fill")
        self.pdf.rect(x, y, b, 1.1, style="F")

    def fuss(self):
        self.seitennr += 1
        if self.cfg["pdf"]["seitenzahlen"]:
            self.text(0, SEITE_H - FUSS_H, SEITE_B, 8, f"– {self.seitennr} –", self.text_schrift, 14,
                      "leise", "C")

    def speichern(self, pfad: Path):
        self.pdf.output(str(pfad))


# ---------------------------------------------------------------------------
# Ausgabe: PowerPoint (python-pptx) – gleiches Layout, alles editierbar
# ---------------------------------------------------------------------------
class _PptxAusgabe:
    def __init__(self, cfg: dict, titel: str):
        from pptx import Presentation
        from pptx.util import Mm

        self.cfg = cfg
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = Mm(SEITE_B), Mm(SEITE_H)
        self.prs.core_properties.title = titel
        self.folie = None
        self.seitennr = 0
        self.notizen: list[str] = []

    def _rgb(self, name):
        from pptx.dml.color import RGBColor

        return RGBColor(*FARBEN[name])

    def neue_seite(self):
        self._notizen_schreiben()
        self.folie = self.prs.slides.add_slide(self.prs.slide_layouts[6])  # leer
        hg = self.folie.background.fill
        hg.solid()
        hg.fore_color.rgb = self._rgb("hintergrund")

    def _notizen_schreiben(self):
        if self.folie is not None and self.notizen:
            self.folie.notes_slide.notes_text_frame.text = "Bilder:\n" + "\n".join(self.notizen)
        self.notizen = []

    def text(self, x, y, b, h, text, schrift, groesse, farbe, ausrichtung="L", fett=False):
        from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
        from pptx.util import Mm, Pt

        feld = self.folie.shapes.add_textbox(Mm(x), Mm(y), Mm(b), Mm(h))
        tf = feld.text_frame
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = {"L": PP_ALIGN.LEFT, "C": PP_ALIGN.CENTER, "R": PP_ALIGN.RIGHT}[ausrichtung]
        run = p.add_run()
        run.text = text
        run.font.size = Pt(groesse)
        run.font.name = "Georgia" if schrift == "Titel" else "Segoe UI"
        run.font.bold = schrift == "Titel" or fett
        run.font.color.rgb = self._rgb(farbe)
        return feld

    def _rechteck(self, x, y, b, h, farbe):
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.util import Mm

        form = self.folie.shapes.add_shape(MSO_SHAPE.RECTANGLE, Mm(x), Mm(y), Mm(b), Mm(h))
        form.fill.solid()
        form.fill.fore_color.rgb = self._rgb(farbe)
        form.line.fill.background()
        form.shadow.inherit = False
        return form

    def bild(self, p: dict):
        from pptx.util import Mm

        pc = self.cfg["pdf"]
        x, y, b, h = p["x"], p["y"], p["b"], p["h"]
        self._rechteck(x - PASSEPARTOUT + 0.8, y - PASSEPARTOUT + 1.0,
                       b + 2 * PASSEPARTOUT, h + 2 * PASSEPARTOUT, "schatten")
        self._rechteck(x - PASSEPARTOUT, y - PASSEPARTOUT, b + 2 * PASSEPARTOUT, h + 2 * PASSEPARTOUT, "rahmen")
        # GANZES Bild einfügen und nur per Zuschnitt-Eigenschaft beschneiden:
        # So kannst du in PowerPoint den Ausschnitt nachträglich verschieben.
        im = p["bild"]["_bild"]
        voll = im.copy()
        voll.thumbnail((pc["pptx_kante"], pc["pptx_kante"]))
        puffer = io.BytesIO()
        voll.save(puffer, format="JPEG", quality=80, optimize=True)
        puffer.seek(0)
        bild = self.folie.shapes.add_picture(puffer, Mm(x), Mm(y), Mm(b), Mm(h))
        l, o, r, u = p["box"]
        bild.crop_left, bild.crop_top = l / im.width, o / im.height
        bild.crop_right, bild.crop_bottom = 1 - r / im.width, 1 - u / im.height
        bild.name = p["bild"]["pfad"].name
        self.notizen.append(str(p["bild"]["pfad"]))
        if pc["bild_datum"]:
            chip = self.text(x + 2, y + h - 7, 11, 5, f"{p['bild']['zeit']:%d.%m.}", "Text", 9, "text", "C")
            chip.fill.solid()
            chip.fill.fore_color.rgb = self._rgb("rahmen")

    def kopf(self, titel, untertitel, teil):
        self.text(RAND, RAND - 2, SEITE_B * 0.7, 14, titel, "Titel", 28, "text")
        rechts = untertitel + (f"   ·   Teil {teil}" if teil else "")
        if rechts:
            self.text(RAND, RAND + 13, SEITE_B * 0.7, 10, rechts, "Text", 18, "akzent")
        self._balken(RAND, RAND + 25, 16)

    def _balken(self, x, y, b):
        self._rechteck(x, y, b, 1.1, "akzent")

    def fuss(self):
        self.seitennr += 1
        if self.cfg["pdf"]["seitenzahlen"]:
            self.text(0, SEITE_H - FUSS_H, SEITE_B, 8, f"– {self.seitennr} –", "Text", 14, "leise", "C")

    def speichern(self, pfad: Path):
        self._notizen_schreiben()
        self.prs.save(str(pfad))


# ---------------------------------------------------------------------------
# Gemeinsamer Aufbau
# ---------------------------------------------------------------------------
def _titelseite(ausgaben, titel: str, untertitel: str, jahr_text: str, held: list[dict], cfg):
    """Großes Foto links, rechts Titel und zwei weitere Fotos."""
    for a in ausgaben:
        a.neue_seite()
    plaetze = [platzieren(held[0], RAND, RAND, 160, SEITE_H - 2 * RAND, cfg)]
    rx = RAND + 160 + 12
    rb = SEITE_B - rx - RAND
    if len(held) > 1:
        boxen = [fokus_ausschnitt(b, cfg) for b in held[1:3]]
        formate = [(r - l) / max(u - o, 1) for l, o, r, u in boxen]
        _, rr = _blocksatz(formate, rx, 96, rb, SEITE_H - 96 - RAND, spalten=True)
        if not rr:
            _, rr = _blocksatz(formate, rx, 96, rb, SEITE_H - 96 - RAND)
        for b, r, box in zip(held[1:3], rr, boxen):
            plaetze.append({"bild": b, "x": r[0], "y": r[1], "b": r[2], "h": r[3], "box": box})
    for a in ausgaben:
        for p in plaetze:
            a.bild(p)
        ts = getattr(a, "titel_schrift", "Titel")
        a.text(rx, 26, rb, 14, titel, ts, 26, "text")
        a.text(rx, 42, rb, 24, jahr_text, ts, 50, "akzent")
        a._balken(rx, 70, 16)
        a.text(rx, 76, rb, 10, untertitel, getattr(a, "text_schrift", "Text"), 14, "leise")


def _erzeuge(seiten: list[dict], cfg: dict, ziel_stamm: Path, titel: str, untertitel: str,
             jahr_text: str) -> list[Path]:
    pc = cfg["pdf"]
    modell = gesichter_mod.modell_bereitstellen(cfg["analyse"]["modell_ordner"])
    modell = str(modell) if modell else None
    kante = max(int(SEITE_B / 25.4 * pc["dpi"]), pc["pptx_kante"]) + 100

    ausgaben = [_PdfAusgabe(cfg, titel)]
    if pc.get("pptx", True):
        try:
            ausgaben.append(_PptxAusgabe(cfg, titel))
        except ImportError:
            print("Hinweis: python-pptx fehlt – nur PDF ('uv sync' ausführen).")

    # Titelseite: bestes Bild + zwei weitere aus anderen Seiten
    alle = [b for s in seiten for b in s["bilder"]]
    held = sorted(alle, key=lambda b: -b["score"])
    auswahl = [held[0]]
    for b in held[1:]:
        if len(auswahl) == 3:
            break
        if all(abs((b["zeit"] - a["zeit"]).days) > 3 for a in auswahl):
            auswahl.append(b)
    auswahl += [b for b in held if b not in auswahl][:3 - len(auswahl)]
    for b in auswahl:
        _vorbereiten(b, modell, kante)
    _titelseite(ausgaben, titel, untertitel, jahr_text, auswahl, cfg)

    from .fortschritt import Fortschritt

    # nur für den Balken in der Oberfläche – die Seiten werden ohnehin einzeln gemeldet
    fortschritt = Fortschritt(f"{titel}: Seiten", 0, "Seiten", intervall=1e9)
    fortschritt.gesamt = len(seiten)
    for nr, seite in enumerate(seiten, start=1):
        teil = f" [{seite['teil']}]" if seite["teil"] else ""
        print(f"  Seite {nr}/{len(seiten)}: {seite['titel']}{teil} ({len(seite['bilder'])} Bilder)")
        fortschritt.schritt()
        plaetze = layout_seite(seite, cfg, modell, kante)
        for a in ausgaben:
            a.neue_seite()
            a.kopf(seite["titel"], seite["untertitel"], seite["teil"])
            for p in plaetze:
                a.bild(p)
            a.fuss()
        for b in seite["bilder"]:
            if b not in auswahl:
                b.pop("_bild", None)  # Speicher freigeben

    fortschritt.fertig()
    print("  Speichere Dateien …")
    dateien = []
    for a, endung in zip(ausgaben, (".pdf", ".pptx")):
        pfad = ziel_stamm.with_suffix(endung)
        a.speichern(pfad)
        dateien.append(pfad)
        print(f"{endung[1:].upper()} erstellt: {pfad} ({len(seiten) + 1} Seiten, "
              f"{pfad.stat().st_size / 1e6:.1f} MB)")
    return dateien


def erstelle_rueckblicke(cfg: dict, jahr: int, ausgabe: str | None = None) -> list[Path]:
    """Jahresrückblick (+ Haus-Rückblick) erstellen. Gibt die Dateipfade zurück."""
    jahr_ordner = Path(cfg["ziel"]) / str(jahr)
    if not jahr_ordner.exists():
        print(f"Ordner {jahr_ordner} gibt es nicht – erst 'fotosort sortieren' ausführen.")
        return []
    pc = cfg["pdf"]
    bilder = sammle_bilder(jahr_ordner, cfg)
    dateien: list[Path] = []

    seiten = plane_jahresrueckblick(bilder, cfg)
    if seiten:
        stamm = Path(ausgabe).with_suffix("") if ausgabe else jahr_ordner / f"Jahresrueckblick_{jahr}"
        print(f"Jahresrückblick {jahr}: {len(seiten)} Inhaltsseiten")
        dateien += _erzeuge(seiten, cfg, stamm, pc["titel"].format(jahr="").strip() or "Jahresrückblick",
                            pc["untertitel"].format(jahr=jahr), str(jahr))
    else:
        print(f"Keine Bilder mit mind. {pc['min_sterne']} Sternen in {jahr_ordner} gefunden.")

    if cfg["themen"]["haus"]["aktiv"] and cfg["themen"]["haus"]["eigenes_pdf"]:
        haus = plane_haus_rueckblick(bilder, cfg)
        if haus:
            name = cfg["themen"]["haus"]["name"]
            print(f"{name} {jahr}: {len(haus)} Inhaltsseiten")
            dateien += _erzeuge(haus, cfg, jahr_ordner / f"Hausrenovierung_{jahr}",
                                name, "nach Bereichen, chronologisch", str(jahr))
    return dateien


def erstelle_pdf(cfg: dict, jahr: int, ausgabe: str | None = None):
    """Kompatibilität: erzeugt alle Rückblicke, gibt den ersten Pfad zurück."""
    dateien = erstelle_rueckblicke(cfg, jahr, ausgabe)
    return dateien[0] if dateien else None
