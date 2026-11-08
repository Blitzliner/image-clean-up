"""
Gesichter finden (YuNet) und wiedererkennen (SFace) – beides OpenCV-Modelle.

  - YuNet  (~0,2 MB): findet Gesichter und markante Punkte (Augen, Nase, Mund)
  - SFace  (~37 MB) : berechnet für jedes Gesicht einen "Fingerabdruck"
                      (128 Zahlen). Gleiche Person = ähnlicher Fingerabdruck.
                      Damit werden Personen über alle Fotos hinweg gruppiert
                      (siehe personen.py).

Beide Modelle werden beim ersten Start automatisch nach "models" geladen.
Ohne Modelle läuft alles weiter, nur ohne Gesichter.
"""
from __future__ import annotations

import base64
import os
import urllib.request

# Technische OpenCV-Meldungen (z.B. "WARN ... net_impl_backend") unterdrücken
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
from pathlib import Path

import numpy as np

_BASIS = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
MODELLE = {
    "erkennung": ("face_detection_yunet_2023mar.onnx",
                  _BASIS + "face_detection_yunet/face_detection_yunet_2023mar.onnx", 100_000),
    "wiedererkennung": ("face_recognition_sface_2021dec.onnx",
                        _BASIS + "face_recognition_sface/face_recognition_sface_2021dec.onnx", 30_000_000),
}
MODELL_NAME = MODELLE["erkennung"][0]  # für ältere Aufrufe

# Pro Prozess werden die Modelle nur einmal geladen
_detektor = None
_detektor_pfad = None
_erkenner = None
_erkenner_pfad = None


def _lade_modell(ordner: Path, schluessel: str) -> Path | None:
    name, url, mindestgroesse = MODELLE[schluessel]
    ziel = ordner / name
    if ziel.exists() and ziel.stat().st_size > mindestgroesse:
        return ziel
    try:
        ordner.mkdir(parents=True, exist_ok=True)
        print(f"Lade Modell {name} herunter -> {ziel}")
        urllib.request.urlretrieve(url, ziel)
        if ziel.stat().st_size > mindestgroesse:
            return ziel
    except Exception as fehler:  # kein Internet o.ä.
        print(f"  {name} konnte nicht geladen werden ({fehler}).")
    return None


def modell_bereitstellen(ordner: str | Path) -> Path | None:
    """Gesichtserkennungs-Modell (YuNet) bereitstellen. Gibt Pfad oder None."""
    return _lade_modell(Path(ordner), "erkennung")


def modelle_bereitstellen(ordner: str | Path) -> dict:
    """Beide Modelle bereitstellen: {'erkennung': pfad|None, 'wiedererkennung': pfad|None}"""
    ordner = Path(ordner)
    return {k: (str(p) if (p := _lade_modell(ordner, k)) else None) for k in MODELLE}


def _hole_detektor(modell_pfad: str | None):
    global _detektor, _detektor_pfad
    if not modell_pfad:
        return None
    if _detektor is None or _detektor_pfad != modell_pfad:
        import cv2

        if hasattr(cv2, "setLogLevel"):
            cv2.setLogLevel(0)
        _detektor = cv2.FaceDetectorYN.create(
            modell_pfad, "", (320, 320), score_threshold=0.75, nms_threshold=0.3
        )
        _detektor_pfad = modell_pfad
    return _detektor


def _hole_erkenner(modell_pfad: str | None):
    global _erkenner, _erkenner_pfad
    if not modell_pfad:
        return None
    if _erkenner is None or _erkenner_pfad != modell_pfad:
        import cv2

        _erkenner = cv2.FaceRecognizerSF.create(modell_pfad, "")
        _erkenner_pfad = modell_pfad
    return _erkenner


def vektor_zu_text(v: np.ndarray) -> str:
    """Fingerabdruck platzsparend als Text speichern (für den JSON-Cache)."""
    return base64.b64encode(np.asarray(v, dtype=np.float16).tobytes()).decode("ascii")


def text_zu_vektor(t: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(t), dtype=np.float16).astype(np.float32)


def finde_gesichter(rgb: np.ndarray, modell_pfad: str | None,
                    erkenner_pfad: str | None = None, mit_vektoren: bool = False):
    """
    Gesichter in einem (verkleinerten) RGB-Bild finden.

    Rückgabe: Liste [x, y, breite, hoehe, sicherheit] – relativ zur Bildgröße
    (0..1). Mit mit_vektoren=True zusätzlich eine zweite Liste mit den
    Fingerabdrücken (Text, siehe vektor_zu_text) in derselben Reihenfolge.
    """
    detektor = _hole_detektor(modell_pfad)
    if detektor is None:
        return ([], []) if mit_vektoren else []
    import cv2

    h, w = rgb.shape[:2]
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    detektor.setInputSize((w, h))
    _, treffer = detektor.detect(bgr)
    gesichter, vektoren = [], []
    erkenner = _hole_erkenner(erkenner_pfad) if mit_vektoren else None
    if treffer is not None:
        for f in treffer:
            x, y, fw, fh, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1])
            # Winzige "Gesichter" (Muster im Hintergrund) ignorieren
            if fw < w * 0.025:
                continue
            gesichter.append([
                round(max(0, x) / w, 4), round(max(0, y) / h, 4),
                round(fw / w, 4), round(fh / h, 4), round(score, 3),
            ])
            if mit_vektoren:
                vektor = ""
                if erkenner is not None and fw >= 28:  # zu kleine Gesichter sind unzuverlässig
                    try:
                        ausschnitt = erkenner.alignCrop(bgr, f)
                        merkmal = erkenner.feature(ausschnitt).flatten()
                        merkmal = merkmal / (np.linalg.norm(merkmal) + 1e-9)
                        vektor = vektor_zu_text(merkmal)
                    except Exception:
                        vektor = ""
                vektoren.append(vektor)
    return (gesichter, vektoren) if mit_vektoren else gesichter
