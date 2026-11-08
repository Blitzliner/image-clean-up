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

TEXT_DATEI = ".fotosort_inhalt_text.npz"
SPERRE = ".nicht_verfuegbar"

# Bekannte Windows-Absturzcodes -> verständliche Erklärung
WINDOWS_CODES = {
    3221225477: "Speicherzugriffsfehler (0xC0000005)",
    3221226505: "interner Laufzeitfehler (0xC0000409)",
    3221225781: "fehlende DLL (0xC0000135)",
    3221225794: "DLL konnte nicht initialisiert werden (0xC0000142)",
}


def verfuegbar() -> bool:
    """Ist fastembed installiert? (ohne es zu laden – das Laden passiert im Hilfsprozess)"""
    import importlib.util

    return importlib.util.find_spec("fastembed") is not None


def _norm(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)


def _lade(pfad: str) -> Image.Image:
    im = Image.open(pfad)
    im.draft("RGB", (448, 448))
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((448, 448))
    return im


def _beschreibungen(gruppen: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    namen, texte = [], []
    for k, liste in gruppen.items():
        for t in liste:
            namen.append(k)
            texte.append(t)
    return namen, texte


def _text_kennung() -> str:
    """Ändern sich die Beschreibungen, müssen die Text-Vektoren neu berechnet werden."""
    import hashlib
    import json

    return hashlib.sha1(json.dumps([KATEGORIEN, HAUS_BEREICHE, TEXT_MODELL]).encode()).hexdigest()[:16]


def _wahrscheinlichkeiten(bilder: np.ndarray, namen: list[str], texte: np.ndarray) -> list[dict]:
    """CLIP-Zero-Shot: Softmax über alle Beschreibungen, je Kategorie aufsummiert."""
    logits = 100.0 * bilder @ texte.T
    logits -= logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    p /= p.sum(axis=1, keepdims=True)
    kategorien = sorted(set(namen))
    idx = {k: [j for j, n in enumerate(namen) if n == k] for k in kategorien}
    return [{k: round(float(zeile[idx[k]].sum()), 3) for k in kategorien} for zeile in p]


def _lade_vektoren(speicher: Path) -> dict[str, np.ndarray]:
    if not speicher.exists():
        return {}
    try:
        with np.load(speicher) as daten:
            return {k: daten[k] for k in daten.files}
    except Exception as fehler:
        print(f"  Hinweis: {speicher.name} beschädigt ({fehler}) – Inhalte werden neu erkannt.")
        return {}


# ---------------------------------------------------------------------------
# Hilfsprozess: hier (und nur hier) werden fastembed/onnxruntime geladen.
# Stürzt das ab, bleibt das Hauptprogramm (und die Oberfläche) am Leben.
# ---------------------------------------------------------------------------
def _arbeiter(auftrag_pfad: str) -> int:
    import json
    import time

    auftrag = json.loads(Path(auftrag_pfad).read_text(encoding="utf-8"))
    ordner, speicher = Path(auftrag["ordner"]), Path(auftrag["speicher"])
    Image.MAX_IMAGE_PIXELS = int(auftrag.get("max_megapixel", 600) * 1_000_000)  # Panoramas erlauben
    print("MELDUNG Lade Inhaltserkennung (CLIP) – beim ersten Mal ca. 0,6 GB Download …", flush=True)
    import logging

    logging.getLogger("fastembed").setLevel(logging.CRITICAL)
    from fastembed import ImageEmbedding, TextEmbedding

    text_modell = TextEmbedding(TEXT_MODELL, cache_dir=str(ordner))
    if auftrag["text_neu"]:
        kn, kt = _beschreibungen(KATEGORIEN)
        bn, bt = _beschreibungen(HAUS_BEREICHE)
        kv = _norm(np.stack(list(text_modell.embed(kt))).astype(np.float32))
        bv = _norm(np.stack(list(text_modell.embed(bt))).astype(np.float32))
        np.savez(auftrag["text_speicher"], kennung=np.array(_text_kennung()),
                 kat_namen=np.array(kn), kat=kv, ber_namen=np.array(bn), ber=bv)
        print("MELDUNG Beschreibungen vorbereitet.", flush=True)
    bild_modell = ImageEmbedding(BILD_MODELL, cache_dir=str(ordner))
    print("MELDUNG Modelle geladen.", flush=True)

    vektoren = _lade_vektoren(speicher)
    offen = [(s, p) for s, p in auftrag["bilder"] if s not in vektoren]
    letzte_sicherung = time.time()
    for start in range(0, len(offen), 32):
        teil = offen[start:start + 32]
        bilder = []
        for _, pfad in teil:
            try:
                bilder.append(_lade(pfad))
            except Exception:
                bilder.append(Image.new("RGB", (224, 224)))
        try:
            for (sha1, _), v in zip(teil, bild_modell.embed(bilder, batch_size=16)):
                vektoren[sha1] = _norm(np.asarray(v, dtype=np.float32)).astype(np.float16)
        except Exception as fehler:  # einzelner Stapel kaputt -> weiter
            print(f"MELDUNG Hinweis: {len(teil)} Bilder übersprungen ({fehler})", flush=True)
        print(f"FORTSCHRITT {min(start + 32, len(offen))} {len(offen)}", flush=True)
        if time.time() - letzte_sicherung > 60:  # Zwischenstand sichern
            np.savez_compressed(speicher, **vektoren)
            letzte_sicherung = time.time()
    np.savez_compressed(speicher, **vektoren)
    print("FERTIG", flush=True)
    return 0


def _starte_arbeiter(cfg: dict, offen: list[dict], text_neu: bool) -> tuple[bool, str]:
    """Hilfsprozess starten, Fortschritt weiterreichen. Rückgabe: (ok, Fehlertext)."""
    import json
    import os
    import subprocess
    import sys
    import tempfile
    from collections import deque

    from .fortschritt import Fortschritt, aktivitaet

    ordner = Path(cfg["analyse"]["modell_ordner"]) / "clip"
    ordner.mkdir(parents=True, exist_ok=True)
    basis = Path(cfg["_basis"])
    auftrag = {"ordner": str(ordner), "speicher": str(basis / VEKTOR_DATEI),
               "text_speicher": str(basis / TEXT_DATEI), "text_neu": text_neu,
               "max_megapixel": cfg["analyse"].get("max_megapixel", 600),
               "bilder": [[i["sha1"], i["pfad"]] for i in offen]}
    fd, auftrag_pfad = tempfile.mkstemp(suffix=".json", prefix="fotosort_inhalt_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(auftrag, f)

    umgebung = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                    HF_HUB_DISABLE_PROGRESS_BARS="1", TQDM_DISABLE="1")
    absturz_log = basis / "fotosort_absturz_inhalt.log"
    befehl = [sys.executable, "-X", "faulthandler", "-m", "fotosort.inhalt",
              "--auftrag", auftrag_pfad, "--absturzlog", str(absturz_log)]
    fortschritt = Fortschritt("Bildinhalte erkennen", len(offen), "Bilder") if offen else None
    letzte = deque(maxlen=15)
    try:
        prozess = subprocess.Popen(befehl, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", env=umgebung)
        erledigt = 0
        for zeile in prozess.stdout:
            zeile = zeile.rstrip()
            aktivitaet()
            if zeile.startswith("FORTSCHRITT "):
                n = int(zeile.split()[1])
                if fortschritt:
                    fortschritt.schritt(n - erledigt)
                erledigt = n
            elif zeile.startswith("MELDUNG "):
                print("  " + zeile[8:])
            elif zeile and zeile != "FERTIG":
                letzte.append(zeile)
        code = prozess.wait()
    finally:
        try:
            os.remove(auftrag_pfad)
        except OSError:
            pass
    if fortschritt:
        fortschritt.fertig()
    if code == 0:
        return True, ""
    grund = WINDOWS_CODES.get(code, f"Exit-Code {code}")
    details = "\n".join(f"    {z}" for z in list(letzte)[-8:])
    return False, f"{grund}" + (f"\n  Letzte Ausgaben:\n{details}" if details else "")


def erkenne_inhalte(items: list[dict], cfg: dict) -> bool:
    """
    Für alle Fotos i["inhalt"] = {kategorie: wahrscheinlichkeit} und
    i["haus_bereich"] setzen. Gibt False zurück, wenn CLIP nicht verfügbar ist.
    Die Bild-Vektoren werden in .fotosort_inhalt.npz zwischengespeichert.
    Die eigentliche Berechnung läuft in einem eigenen Prozess (absturzsicher).
    """
    import time

    if not cfg["themen"].get("inhalte_erkennen", True):
        print("  Inhaltserkennung ist ausgeschaltet.")
        return False
    if not verfuegbar():
        print("  Hinweis: Paket 'fastembed' fehlt – Inhaltserkennung aus ('uv sync' ausführen).")
        return False
    fotos = [i for i in items if i["art"] == "foto" and i.get("sha1") and not i.get("fehler")]
    if not fotos:
        return True

    basis = Path(cfg["_basis"])
    ordner = Path(cfg["analyse"]["modell_ordner"]) / "clip"
    sperre = ordner / SPERRE
    if sperre.exists() and time.time() - sperre.stat().st_mtime < 24 * 3600:
        print(f"  Inhaltserkennung übersprungen – beim letzten Versuch: "
              f"{sperre.read_text(encoding='utf-8').splitlines()[0]}\n"
              f"  Erneut versuchen: Datei {sperre} löschen (oder 1 Tag warten).")
        return False

    vektoren = _lade_vektoren(basis / VEKTOR_DATEI)
    offen = [i for i in fotos if i["sha1"] not in vektoren]
    text = _lade_vektoren(basis / TEXT_DATEI)
    text_neu = str(text.get("kennung", "")) != _text_kennung()
    print(f"  {len(fotos) - len(offen):_} Fotos schon bekannt, {len(offen):_} neu.".replace("_", "."))

    if offen or text_neu:
        ok, fehler = _starte_arbeiter(cfg, offen, text_neu)
        if not ok:
            ordner.mkdir(parents=True, exist_ok=True)
            nativ = not fehler.startswith("Exit-Code 1")  # 1 = normale Python-Exception
            zeilen = fehler.splitlines()
            letzte = next((z.strip() for z in reversed(zeilen) if z.strip()), "")
            if nativ:
                kurz = f"abgestürzt ({zeilen[0]})"
            else:
                kurz = f"mit einem Fehler abgebrochen: {letzte[:200]}"
            sperre.write_text(f"Inhaltserkennung {kurz}", encoding="utf-8")
            print(f"  ACHTUNG: Die Inhaltserkennung ist {kurz}")
            print("  Das restliche Programm läuft normal weiter – nur Haus/Kinder-Erkennung über "
                  "Bildinhalte fehlt (Häkchen in der Oberfläche gehen weiterhin).")
            if nativ:
                print(f"  Details: {basis / 'fotosort_absturz_inhalt.log'}")
                print("  Häufigste Ursache unter Windows: fehlende/alte 'Microsoft Visual C++ "
                      "Redistributable' – installieren von https://aka.ms/vs/17/release/vc_redist.x64.exe "
                      "und den PC neu starten.")
            elif "load model" in letzte or "download" in letzte.lower():
                print("  Ursache: Das Modell konnte nicht heruntergeladen werden (Internet/Firewall?).")
            for zeile in zeilen[1:]:
                print(zeile)
        vektoren = _lade_vektoren(basis / VEKTOR_DATEI)  # auch Teilergebnisse nutzen
        text = _lade_vektoren(basis / TEXT_DATEI)

    if str(text.get("kennung", "")) != _text_kennung():
        return False
    fotos = [i for i in fotos if i["sha1"] in vektoren]
    if not fotos:
        return False
    matrix = _norm(np.stack([vektoren[i["sha1"]].astype(np.float32) for i in fotos]))
    for i, w in zip(fotos, _wahrscheinlichkeiten(matrix, list(text["kat_namen"]), text["kat"])):
        i["inhalt"] = w
    for i, w in zip(fotos, _wahrscheinlichkeiten(matrix, list(text["ber_namen"]), text["ber"])):
        i["haus_bereich"] = max(w, key=w.get)
    print(f"  Bildinhalte für {len(fotos):_} Fotos bestimmt.".replace("_", "."))
    return True


if __name__ == "__main__":
    import argparse
    import faulthandler
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("--auftrag", required=True)
    parser.add_argument("--absturzlog")
    args = parser.parse_args()
    if args.absturzlog:  # bei nativen Abstürzen den Stapel in eine Datei schreiben
        faulthandler.enable(open(args.absturzlog, "w", encoding="utf-8"))
    sys.exit(_arbeiter(args.auftrag))
