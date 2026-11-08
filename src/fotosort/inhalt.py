"""
Bildinhalte erkennen mit CLIP (über das Paket "fastembed").

CLIP vergleicht ein Bild mit kurzen Beschreibungen ("a photo of a bathroom
under renovation") und sagt, wie gut sie passen. Damit werden u.a. erkannt:
  - Haus & Renovierung (inkl. Bereich: Bad, Küche, Wohnräume, Außen, Baustelle)
  - Kinder auf dem Bild (auch von hinten, ohne erkennbares Gesicht)
  - wenig aussagekräftige Bilder (Dokumente, Schilder, Karten)

Die Modelle (~0,6 GB) werden beim ersten Mal automatisch nach models/clip
geladen. Abschalten: in config.yaml  themen: {inhalte_erkennen: false}

Die Beschreibungen sind englisch, weil CLIP damit am besten funktioniert.
Du kannst sie hier ergänzen – mehr Beschreibungen = feinere Erkennung.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

BILD_MODELL = "Qdrant/clip-ViT-B-32-vision"
TEXT_MODELL = "Qdrant/clip-ViT-B-32-text"
VEKTOR_DATEI = ".fotosort_inhalt.npz"

# Kategorien für alle Fotos. Jede Kategorie hat mehrere Beschreibungen.
KATEGORIEN: dict[str, list[str]] = {
    "haus": [
        "a photo of a house renovation", "a photo of a construction site inside a house",
        "a photo of a room being renovated", "a photo of drywall and building materials",
        "a photo of power tools and paint", "a photo of new tiles being laid",
        "a photo of an unfinished room with bare walls", "a photo of a bathroom under construction",
        "a photo of a kitchen being installed", "a photo of the outside of a house",
        "a photo of a house facade", "a photo of a roof", "a photo of a garden with a lawn",
        "a photo of a terrace", "a photo of an empty room", "a photo of a staircase",
    ],
    "kinder": [
        "a photo of a baby", "a photo of a toddler", "a photo of a young child",
        "a photo of children playing", "a photo of a kid",
    ],
    "menschen": [
        "a photo of a group of adults", "a photo of friends at a party",
        "a photo of people eating together", "a selfie of adults", "a photo of a couple",
    ],
    "landschaft": [
        "a photo of a landscape", "a photo of the beach", "a photo of mountains",
        "a photo of a forest", "a photo of a city street", "a photo of a lake",
    ],
    "essen": ["a photo of food", "a photo of a meal on a plate", "a photo of a cake"],
    "dokument": [
        "a screenshot", "a photo of a document", "a photo of a map", "a photo of a sign",
        "a photo of a receipt", "a photo of a whiteboard", "a photo of a product label",
    ],
    "sonstiges": [
        "a photo of an object", "a photo of a flower", "a photo of an animal",
        "a photo of a car", "a photo of a toy",
    ],
}

# Bereiche innerhalb von "Haus & Renovierung" (für Unterordner und das Haus-PDF)
HAUS_BEREICHE: dict[str, list[str]] = {
    "Bad": ["a photo of a bathroom", "a photo of a shower and toilet", "a photo of bathroom tiles"],
    "Küche": ["a photo of a kitchen", "a photo of kitchen cabinets", "a photo of a stove and sink"],
    "Wohnräume": ["a photo of a living room", "a photo of a bedroom", "a photo of a children's room",
                  "a photo of a staircase", "a photo of a hallway", "a photo of a floor"],
    "Außen & Garten": ["a photo of the outside of a house", "a photo of a garden",
                       "a photo of a roof", "a photo of a facade", "a photo of a terrace",
                       "a photo of a driveway"],
    "Baustelle & Material": ["a photo of building materials", "a photo of tools",
                             "a photo of a construction site", "a photo of drywall",
                             "a photo of a wall being demolished"],
}

_modelle: dict = {}


def verfuegbar() -> bool:
    try:
        import fastembed  # noqa: F401
        return True
    except ImportError:
        return False


def _hole_modelle(ordner: Path):
    if "bild" not in _modelle:
        from fastembed import ImageEmbedding, TextEmbedding

        print("Lade Inhaltserkennung (CLIP) – beim ersten Mal ca. 0,6 GB Download ...")
        _modelle["bild"] = ImageEmbedding(BILD_MODELL, cache_dir=str(ordner))
        _modelle["text"] = TextEmbedding(TEXT_MODELL, cache_dir=str(ordner))
    return _modelle["bild"], _modelle["text"]


def _norm(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)


def _lade(pfad: str) -> Image.Image:
    im = Image.open(pfad)
    im.draft("RGB", (448, 448))
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((448, 448))
    return im


def _text_vektoren(text_modell, beschreibungen: dict[str, list[str]]):
    namen, texte = [], []
    for k, liste in beschreibungen.items():
        for t in liste:
            namen.append(k)
            texte.append(t)
    v = _norm(np.stack(list(text_modell.embed(texte))).astype(np.float32))
    return namen, v


def _wahrscheinlichkeiten(bilder: np.ndarray, namen: list[str], texte: np.ndarray) -> list[dict]:
    """CLIP-Zero-Shot: Softmax über alle Beschreibungen, je Kategorie aufsummiert."""
    logits = 100.0 * bilder @ texte.T
    logits -= logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    p /= p.sum(axis=1, keepdims=True)
    kategorien = sorted(set(namen))
    idx = {k: [j for j, n in enumerate(namen) if n == k] for k in kategorien}
    return [{k: round(float(zeile[idx[k]].sum()), 3) for k in kategorien} for zeile in p]


def erkenne_inhalte(items: list[dict], cfg: dict) -> bool:
    """
    Für alle Fotos i["inhalt"] = {kategorie: wahrscheinlichkeit} und
    i["haus_bereich"] setzen. Gibt False zurück, wenn CLIP nicht verfügbar ist.
    Die Bild-Vektoren werden in .fotosort_inhalt.npz zwischengespeichert.
    """
    if not cfg["themen"].get("inhalte_erkennen", True):
        return False
    if not verfuegbar():
        print("Hinweis: Paket 'fastembed' fehlt – Inhaltserkennung aus ('uv sync' ausführen).")
        return False
    fotos = [i for i in items if i["art"] == "foto" and i.get("sha1") and not i.get("fehler")]
    if not fotos:
        return True

    ordner = Path(cfg["analyse"]["modell_ordner"]) / "clip"
    speicher = Path(cfg["_basis"]) / VEKTOR_DATEI
    vektoren: dict[str, np.ndarray] = {}
    if speicher.exists():
        with np.load(speicher) as daten:
            vektoren = {k: daten[k] for k in daten.files}

    # Nach einem fehlgeschlagenen Download nicht bei jedem Lauf erneut warten
    sperre = ordner / ".download_fehlgeschlagen"
    import time

    if sperre.exists() and time.time() - sperre.stat().st_mtime < 24 * 3600:
        print("Inhaltserkennung übersprungen (Download heute schon fehlgeschlagen; "
              f"zum erneuten Versuch {sperre} löschen).")
        return False
    try:
        import logging

        logging.getLogger("fastembed").setLevel(logging.CRITICAL)
        bild_modell, text_modell = _hole_modelle(ordner)
    except Exception as fehler:
        print(f"Inhaltserkennung nicht verfügbar ({fehler}).")
        ordner.mkdir(parents=True, exist_ok=True)
        sperre.write_text(str(fehler), encoding="utf-8")
        return False

    offen = [i for i in fotos if i["sha1"] not in vektoren]
    if offen:
        from .fortschritt import Fortschritt

        fortschritt = Fortschritt("Bildinhalte erkennen", len(offen), "Bilder")
        letzte_sicherung = time.time()
        for start in range(0, len(offen), 32):
            teil = offen[start:start + 32]
            bilder = []
            for i in teil:
                try:
                    bilder.append(_lade(i["pfad"]))
                except Exception:
                    bilder.append(Image.new("RGB", (224, 224)))
            try:
                for i, v in zip(teil, bild_modell.embed(bilder, batch_size=16)):
                    vektoren[i["sha1"]] = _norm(np.asarray(v, dtype=np.float32)).astype(np.float16)
            except Exception as fehler:  # einzelner Stapel kaputt -> weiter
                print(f"  Hinweis: Inhaltserkennung für {len(teil)} Bilder fehlgeschlagen ({fehler})")
            fortschritt.schritt(len(teil))
            if time.time() - letzte_sicherung > 120:  # Zwischenstand sichern
                np.savez_compressed(speicher, **vektoren)
                letzte_sicherung = time.time()
        np.savez_compressed(speicher, **vektoren)
        fortschritt.fertig()

    fotos = [i for i in fotos if i["sha1"] in vektoren]
    if not fotos:
        return False
    matrix = _norm(np.stack([vektoren[i["sha1"]].astype(np.float32) for i in fotos]))
    namen, texte = _text_vektoren(text_modell, KATEGORIEN)
    for i, w in zip(fotos, _wahrscheinlichkeiten(matrix, namen, texte)):
        i["inhalt"] = w
    namen_b, texte_b = _text_vektoren(text_modell, HAUS_BEREICHE)
    for i, w in zip(fotos, _wahrscheinlichkeiten(matrix, namen_b, texte_b)):
        i["haus_bereich"] = max(w, key=w.get)
    return True
