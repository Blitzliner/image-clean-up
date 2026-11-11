"""
Jede Datei einmal "anschauen": Metadaten, Fingerabdrücke und Qualität.

Für jedes Foto wird berechnet:
  - sha1   : exakter Datei-Hash (findet 1:1-Kopien)
  - phash/dhash : "Wahrnehmungs-Hash" – ähnliche Bilder haben ähnliche Hashes,
                  auch wenn WhatsApp sie verkleinert/neu komprimiert hat
  - schaerfe, helligkeit, Anteil dunkler/heller Pixel
  - gesichter (Position + Größe) und je Gesicht ein Fingerabdruck
    (gesichts_vektoren) für die Personen-Erkennung
  - score  : Gesamtnote 0..100 (siehe berechne_score)

Ergebnisse werden in einem Cache gespeichert, damit ein zweiter Lauf
(z.B. nach Änderungen an ereignisse.yaml) nur Sekunden dauert.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from . import gesichter as gesichter_mod
from .fortschritt import Fortschritt
from .metadaten import (
    bestimme_zeit,
    erkenne_art,
    erkenne_quelle,
    gps_aus_exif,
    zeit_aus_exif,
)

ANALYSE_VERSION = 4  # erhöhen, wenn sich die Berechnung ändert -> Cache neu
ANALYSE_KANTE = 1024  # Bilder werden für die Analyse auf diese Kante verkleinert

# Pillow warnt ab ~89 Megapixeln vor "Decompression Bombs" (manipulierte
# Dateien). Echte Panoramas haben schnell 100-200 MP – wir erlauben bis zu
# analyse.max_megapixel (Standard 600) und vermerken große Bilder als Hinweis.
STANDARD_MAX_MP = 600


def setze_pixel_grenze(max_mp: float = STANDARD_MAX_MP) -> None:
    Image.MAX_IMAGE_PIXELS = int(max_mp * 1_000_000)


setze_pixel_grenze()

try:  # HEIC-Unterstützung (iPhone/Samsung-HEIF), falls installiert
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass


def datei_sha1(pfad: Path) -> str:
    h = hashlib.sha1()
    with open(pfad, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def lade_analysebild(pfad: Path) -> tuple[Image.Image, Image.Image, tuple[int, int]]:
    """
    Bild öffnen, schnell verkleinert dekodieren und richtig drehen.
    Rückgabe: (verkleinertes RGB-Bild, Originalobjekt für EXIF, Originalgröße)
    """
    im = Image.open(pfad)
    original_groesse = im.size
    # Hochkant-Fotos: Originalgröße passend zur Drehung angeben
    if im.getexif().get(0x0112) in (5, 6, 7, 8):
        original_groesse = (original_groesse[1], original_groesse[0])
    # draft() lässt JPEG direkt in kleinerer Auflösung dekodieren (viel schneller)
    im.draft("RGB", (ANALYSE_KANTE, ANALYSE_KANTE))
    klein = ImageOps.exif_transpose(im).convert("RGB")
    klein.thumbnail((ANALYSE_KANTE, ANALYSE_KANTE))
    return klein, im, original_groesse


def qualitaet(klein: Image.Image) -> dict:
    """Schärfe und Belichtung messen."""
    import cv2

    grau = np.asarray(klein.convert("L"), dtype=np.float32)
    lap = cv2.Laplacian(grau, cv2.CV_32F)
    h, w = grau.shape
    # Bild in 4x4 Kacheln teilen und die schärfsten Kacheln bewerten. So werden
    # Porträts mit unscharfem Hintergrund (Bokeh) nicht als "unscharf" gewertet.
    kacheln = sorted(
        float(lap[y * h // 4:(y + 1) * h // 4, x * w // 4:(x + 1) * w // 4].var())
        for y in range(4) for x in range(4)
    )
    return {
        "schaerfe": round(float(np.mean(kacheln[-3:])), 1),
        "helligkeit": round(float(grau.mean() / 255), 3),
        "dunkel_anteil": round(float((grau < 20).mean()), 3),
        "hell_anteil": round(float((grau > 245).mean()), 3),
    }


def berechne_score(info: dict) -> float:
    """
    Gesamtnote 0..100 aus Schärfe, Belichtung, Gesichtern und Auflösung.
    Die Gewichte kannst du hier anpassen.
    """
    # Schärfe: logarithmisch, <100 = unscharf, >1000 = scharf
    s = info.get("schaerfe", 0)
    note_schaerfe = min(1.0, max(0.0, (math.log10(max(s, 1)) - 1.7) / 1.5))

    # Belichtung: ideal um 0.45 mittlere Helligkeit, Abzug für Schwarz/Weiß
    hell = info.get("helligkeit", 0.5)
    note_belichtung = max(0.0, 1 - abs(hell - 0.47) * 2.2)
    note_belichtung -= info.get("dunkel_anteil", 0) * 0.8 + info.get("hell_anteil", 0) * 1.2
    note_belichtung = max(0.0, note_belichtung)

    # Gesichter: Menschen auf dem Bild machen es für die Familie interessant.
    # Große Gesichter zählen mehr als winzige im Hintergrund.
    gs = info.get("gesichter", [])
    if gs:
        groesste = max(g[2] for g in gs)
        note_gesicht = min(1.0, 0.55 + min(len(gs), 4) * 0.08 + min(groesste, 0.3) * 1.0)
    else:
        note_gesicht = 0.0

    # Auflösung: WhatsApp-Kopien und Screenshots sind kleiner
    mp = info.get("breite", 0) * info.get("hoehe", 0) / 1e6
    note_aufloesung = min(1.0, mp / 8)

    score = (
        40 * note_schaerfe
        + 20 * note_belichtung
        + 30 * note_gesicht
        + 10 * note_aufloesung
    )
    return round(score, 1)


def analysiere_datei(pfad_text: str, optionen: dict) -> dict | None:
    """
    Eine Datei vollständig analysieren. Läuft in einem eigenen Prozess.
    Gibt ein Dictionary mit allen Infos zurück (oder None, wenn keine Mediendatei).
    Warnungen (z.B. sehr große Bilder) landen in info["hinweise"] statt in der Konsole.
    """
    setze_pixel_grenze(optionen.get("max_megapixel", STANDARD_MAX_MP))
    with warnings.catch_warnings(record=True) as gesammelt:
        warnings.simplefilter("always")
        info = _analysiere(pfad_text, optionen)
    if info is not None:
        hinweise = list(dict.fromkeys(str(w.message).split(",")[0][:160] for w in gesammelt))
        mp = info.get("breite", 0) * info.get("hoehe", 0) / 1e6
        if mp > 89:
            hinweise = [h for h in hinweise if "decompression bomb" not in h.lower()]
            hinweise.append(f"sehr großes Bild ({mp:.0f} Megapixel, z.B. Panorama) – wird normal verarbeitet")
        if hinweise:
            info["hinweise"] = hinweise
    return info


def _analysiere(pfad_text: str, optionen: dict) -> dict | None:
    pfad = Path(pfad_text)
    quelle = erkenne_quelle(pfad.name, optionen.get("quellen_muster"))
    art = erkenne_art(pfad, quelle)
    if art is None:
        return None

    info: dict = {
        "pfad": str(pfad),
        "name": pfad.name,
        "quelle": quelle,
        "art": art,
        "groesse": pfad.stat().st_size,
        "breite": 0, "hoehe": 0, "lat": None, "lon": None,
        "gesichter": [],
    }
    exif_zeit = None
    try:
        info["sha1"] = datei_sha1(pfad)
        if art != "video":
            klein, original, (b, h) = lade_analysebild(pfad)
            info["breite"], info["hoehe"] = b, h
            exif = original.getexif()
            exif_zeit = zeit_aus_exif(
                exif.get_ifd(0x8769).get(36867) or exif.get(306)
            )
            info["lat"], info["lon"] = gps_aus_exif(exif.get_ifd(0x8825))
            info["modell"] = str(exif.get(272) or "")

            import imagehash

            info["phash"] = str(imagehash.phash(klein))
            info["dhash"] = str(imagehash.dhash(klein))
            info.update(qualitaet(klein))
            if art == "foto":
                info["gesichter"], info["gesichts_vektoren"] = gesichter_mod.finde_gesichter(
                    np.asarray(klein), optionen.get("modell_pfad"),
                    optionen.get("erkenner_pfad"), mit_vektoren=True,
                )
            info["score"] = berechne_score(info)
    except Image.DecompressionBombError as fehler:
        info["fehler"] = (f"Bild zu groß ({fehler}). Grenze in config.yaml unter "
                          f"analyse.max_megapixel erhöhen.")
    except Exception as fehler:  # defekte Datei -> trotzdem einsortieren
        info["fehler"] = f"{type(fehler).__name__}: {fehler}"

    zeit, herkunft, unsicher = bestimme_zeit(
        pfad, quelle, exif_zeit, optionen["zeitzone"], optionen["dateiname_utc"]
    )
    info["zeit"] = zeit.isoformat(timespec="seconds")
    info["zeit_herkunft"] = herkunft
    info["zeit_unsicher"] = unsicher
    return info


# ---------------------------------------------------------------------------
# Sammeln aller Dateien + paralleles Analysieren mit Cache
# ---------------------------------------------------------------------------
def finde_dateien(quellen: list[str], ausschliessen: list[str]) -> list[Path]:
    """Alle Dateien in den Quellordnern (rekursiv) auflisten."""
    ausschliessen_l = {a.lower() for a in ausschliessen}
    dateien = []
    for quelle in quellen:
        for wurzel, ordner, namen in os.walk(quelle):
            # Bereits sortierte Unterordner wie "Aussortiert" nicht erneut lesen
            ordner[:] = [o for o in ordner if o.lower() not in ausschliessen_l
                         and not o.startswith(".")]
            for n in namen:
                if ".fotosort_tmp" in n:  # Reste eines abgebrochenen Komprimierens
                    continue
                p = Path(wurzel) / n
                if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".heic", ".heif",
                                        ".webp", ".tif", ".tiff", ".mp4", ".mov",
                                        ".3gp", ".m4v", ".avi", ".mkv"}:
                    dateien.append(p)
    return sorted(dateien)


def _cache_schluessel(p: Path) -> str | None:
    try:
        st = p.stat()
    except OSError:  # Datei inzwischen verschwunden / Netzlaufwerk weg
        return None
    return f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}|v{ANALYSE_VERSION}"


def bereinige_werte(i: dict) -> None:
    """Ungültige Zahlen (NaN, unendlich) entfernen, damit spätere Schritte nicht scheitern."""
    def ok(x):
        return isinstance(x, (int, float)) and math.isfinite(x)

    lat, lon = i.get("lat"), i.get("lon")
    if lat is not None and not (ok(lat) and ok(lon) and abs(lat) <= 90 and abs(lon) <= 180):
        i["lat"] = i["lon"] = None
    for feld, ersatz in (("schaerfe", 0.0), ("helligkeit", 0.5), ("dunkel_anteil", 0.0),
                         ("hell_anteil", 0.0), ("score", 0.0)):
        if feld in i and i[feld] is not None and not ok(i[feld]):
            i[feld] = ersatz
    for feld in ("breite", "hoehe", "groesse"):
        if not ok(i.get(feld, 0)):
            i[feld] = 0
    gesichter = i.get("gesichter") or []
    gueltig = [all(ok(x) for x in g) for g in gesichter]
    if not all(gueltig):
        vektoren = i.get("gesichts_vektoren") or [""] * len(gesichter)
        i["gesichter"] = [g for g, ja in zip(gesichter, gueltig) if ja]
        i["gesichts_vektoren"] = [v for v, ja in zip(vektoren, gueltig) if ja]


def lade_cache(pfad: Path) -> dict:
    """Cache lesen. Ist er beschädigt (z.B. Abbruch beim Schreiben), die Sicherung nehmen."""
    for kandidat in (pfad, pfad.with_suffix(pfad.suffix + ".bak")):
        if kandidat.exists():
            try:
                return json.loads(kandidat.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                print(f"Hinweis: {kandidat.name} ist beschädigt – versuche Sicherung.")
    return {}


def speichere_cache(pfad: Path, cache: dict) -> None:
    """Sicher speichern: erst temporäre Datei, alte Version als .bak behalten."""
    tmp = pfad.with_suffix(pfad.suffix + ".tmp")
    tmp.write_text(json.dumps(cache), encoding="utf-8")
    if pfad.exists():
        os.replace(pfad, pfad.with_suffix(pfad.suffix + ".bak"))
    os.replace(tmp, pfad)


def analysiere_alle(cfg: dict) -> list[dict]:
    """Alle Quelldateien analysieren (parallel, mit Cache und Fortschrittsanzeige)."""
    print("Suche Dateien in den Quellordnern …")
    dateien = finde_dateien(cfg["quellen"], [cfg["ordner"]["aussortiert"]])
    print(f"{len(dateien):_} Mediendateien gefunden.".replace("_", "."))

    cache_pfad = Path(cfg["analyse"]["cache_datei"])
    cache = lade_cache(cache_pfad)

    modelle = gesichter_mod.modelle_bereitstellen(cfg["analyse"]["modell_ordner"])
    optionen = {
        "zeitzone": cfg["zeitzone"],
        "dateiname_utc": cfg["dateiname_utc"],
        "quellen_muster": cfg["quellen_muster"],
        "modell_pfad": modelle["erkennung"],
        "erkenner_pfad": modelle["wiedererkennung"],
        "max_megapixel": cfg["analyse"].get("max_megapixel", STANDARD_MAX_MP),
    }

    ergebnisse: list[dict] = []
    offen: list[tuple[str, Path]] = []
    for p in dateien:
        k = _cache_schluessel(p)
        if k is None:
            continue
        eintrag = cache.get(k)
        if k in cache and not (eintrag and eintrag.get("fehler")):
            if eintrag is not None:
                ergebnisse.append(eintrag)
        else:
            # neu – oder beim letzten Mal fehlgeschlagen (z.B. alte Größengrenze): neu versuchen
            offen.append((k, p))

    if offen:
        print(f"{len(offen):_} neue Dateien einlesen ({len(dateien) - len(offen):_} aus dem Cache)."
              .replace("_", "."))
        prozesse = cfg["analyse"]["prozesse"] or max(1, (os.cpu_count() or 2) - 1)
        fortschritt = Fortschritt("Einlesen", len(offen), "Bilder")
        letzte_sicherung = time.time()

        def uebernehmen(k, info):
            nonlocal letzte_sicherung
            cache[k] = info
            if info is not None:
                ergebnisse.append(info)
            fortschritt.schritt()
            # Zwischenspeichern alle 60 s: ein Abbruch verliert kaum Arbeit
            if time.time() - letzte_sicherung > 60:
                speichere_cache(cache_pfad, cache)
                letzte_sicherung = time.time()

        try:
            with ProcessPoolExecutor(max_workers=prozesse) as pool:
                auftraege = {pool.submit(analysiere_datei, str(p), optionen): (k, p) for k, p in offen}
                for auftrag in as_completed(auftraege):
                    k, p = auftraege[auftrag]
                    try:
                        info = auftrag.result()
                    except BrokenProcessPool:
                        raise
                    except Exception as fehler:
                        print(f"  Fehler bei {p.name}: {fehler}")
                        info = None
                    uebernehmen(k, info)
        except BrokenProcessPool:
            # Ein Hilfsprozess ist abgestürzt (meist zu wenig Arbeitsspeicher bei
            # riesigen Dateien) -> den Rest nacheinander im Hauptprozess einlesen
            erledigt = {k for k in cache}
            rest = [(k, p) for k, p in offen if k not in erledigt]
            print(f"  Hinweis: Ein Hilfsprozess ist abgestürzt – lese die restlichen "
                  f"{len(rest)} Dateien nacheinander ein (langsamer, aber sicher).")
            for k, p in rest:
                try:
                    info = analysiere_datei(str(p), optionen)
                except Exception as fehler:
                    print(f"  Fehler bei {p.name}: {fehler}")
                    info = None
                uebernehmen(k, info)
        speichere_cache(cache_pfad, cache)
        fortschritt.fertig()

    # Zahlenwerte prüfen: NaN/unendlich (z.B. aus kaputtem EXIF) unschädlich machen
    for i in ergebnisse:
        bereinige_werte(i)

    # Sehr große Bilder auch aus dem Cache als Hinweis kennzeichnen
    for i in ergebnisse:
        mp = i.get("breite", 0) * i.get("hoehe", 0) / 1e6
        if mp > 89 and not i.get("hinweise"):
            i["hinweise"] = [f"sehr großes Bild ({mp:.0f} Megapixel, z.B. Panorama) – wird normal verarbeitet"]

    # Probleme zusammenfassen (stehen auch in der Oberfläche unter "Probleme")
    fehler = [i for i in ergebnisse if i.get("fehler")]
    hinweise = [i for i in ergebnisse if i.get("hinweise")]
    if fehler:
        print(f"Achtung: {len(fehler)} Dateien konnten nicht (vollständig) gelesen werden, z.B.:")
        for i in fehler[:5]:
            print(f"  {i['name']}: {i['fehler'][:150]}")
    if hinweise:
        print(f"Hinweis: {len(hinweise)} Dateien mit Hinweisen, z.B.:")
        for i in hinweise[:5]:
            print(f"  {i['name']}: {i['hinweise'][0]}")

    # Sortierung strikt nach Aufnahmezeit
    ergebnisse.sort(key=lambda i: (i["zeit"], i["name"]))
    return ergebnisse
