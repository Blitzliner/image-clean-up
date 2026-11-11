"""
Speicherplatz sparen: Fotos verkleinern/neu komprimieren, Videos platzsparend
umwandeln. Zwei Zeitpunkte sind möglich:

  - beim Ablegen (letzter Schritt): Die Kopie auf dem NAS wird komprimiert,
    die Originale bleiben (bzw. werden beim Verschieben entfernt).
  - vorher in den Quellordnern (erster Schritt): Die Originale werden durch
    kleinere Fassungen ersetzt – schafft sofort Platz auf dem PC.
    ACHTUNG: Das verändert die Originale! Nur nutzen, wenn es eine Sicherung gibt
    oder der Qualitätsverlust in Ordnung ist.

Videos: H.265/HEVC in MP4 (Kennung "hvc1"), Ton AAC. Etwa halb so groß wie das
übliche H.264 bei gleicher Qualität und auf allen aktuellen Handys, Macs und
Windows-PCs abspielbar (Windows: "HEVC-Videoerweiterungen" – auf den meisten
neuen PCs vorinstalliert; VLC spielt alles). Alternativ "h264" = maximal kompatibel.

Datum, GPS und Kameradaten (EXIF bzw. Video-Metadaten) bleiben erhalten.
Eine Datei wird nur ersetzt, wenn das Ergebnis deutlich kleiner ist.
ffmpeg kommt über das Paket "imageio-ffmpeg" mit – keine Extra-Installation.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

BILD_ENDUNGEN = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_ENDUNGEN = {".mp4", ".mov", ".3gp", ".m4v", ".avi", ".mkv"}
MINDEST_ERSPARNIS = 0.15  # nur ersetzen, wenn mind. 15 % kleiner

_encoder_cache: dict = {}


# ---------------------------------------------------------------------------
# ffmpeg finden und Videos untersuchen
# ---------------------------------------------------------------------------
def ffmpeg_pfad() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def _ohne_fenster() -> dict:
    """Unter Windows kein schwarzes Konsolenfenster pro ffmpeg-Aufruf öffnen."""
    if os.name == "nt":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def video_info(datei: Path, exe: str | None = None) -> dict | None:
    """Dauer, Codec, Auflösung und Bitrate eines Videos (aus der ffmpeg-Ausgabe)."""
    exe = exe or ffmpeg_pfad()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-hide_banner", "-i", str(datei)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60, **_ohne_fenster())
    except (subprocess.TimeoutExpired, OSError):
        return None
    text = r.stderr
    info = {"dauer": 0.0, "codec": "", "breite": 0, "hoehe": 0, "kbit": 0}
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", text)
    if m:
        info["dauer"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"Stream #[^\n]*Video: (\w+)[^\n]*?, (\d{2,5})x(\d{2,5})", text)
    if m:
        info["codec"], info["breite"], info["hoehe"] = m.group(1), int(m.group(2)), int(m.group(3))
    m = re.search(r"bitrate: (\d+) kb/s", text)
    if m:
        info["kbit"] = int(m.group(1))
    return info if info["codec"] else None


def _teste_encoder(exe: str, name: str, args: list[str]) -> bool:
    try:
        r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc=size=640x360:rate=25", "-t", "0.4", "-c:v", name, *args,
                            "-f", "null", "-"], capture_output=True, timeout=60, **_ohne_fenster())
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def waehle_encoder(exe: str, ac: dict) -> tuple[str, list[str]]:
    """
    Video-Encoder wählen. Mit video_hardware "auto" wird zuerst die Grafikkarte
    probiert (NVIDIA/Intel/AMD – viel schneller), sonst der Prozessor (x265/x264).
    """
    codec, q = ac["video_codec"], int(ac["video_qualitaet"])
    schluessel = (exe, codec, q, ac["video_hardware"])
    if schluessel in _encoder_cache:
        return _encoder_cache[schluessel]
    if codec == "h264":
        software = ("libx264", ["-preset", "veryfast", "-crf", str(q - 3)])
        hardware = [("h264_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", str(q - 1), "-b:v", "0"]),
                    ("h264_qsv", ["-global_quality", str(q - 2)]),
                    ("h264_amf", ["-rc", "cqp", "-qp_i", str(q - 3), "-qp_p", str(q - 1)])]
        kennung: list[str] = []
    else:
        software = ("libx265", ["-preset", "veryfast", "-crf", str(q), "-x265-params", "log-level=error"])
        hardware = [("hevc_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", str(q + 2), "-b:v", "0"]),
                    ("hevc_qsv", ["-global_quality", str(q)]),
                    ("hevc_amf", ["-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q + 2)])]
        kennung = ["-tag:v", "hvc1"]  # nötig für Apple-Geräte
    gewaehlt = None
    if ac["video_hardware"] == "auto":
        for name, args in hardware:
            if _teste_encoder(exe, name, args):
                gewaehlt = (name, args + kennung)
                break
    if gewaehlt is None:
        gewaehlt = (software[0], software[1] + kennung)
    _encoder_cache[schluessel] = gewaehlt
    print(f"  Video-Encoder: {gewaehlt[0]}" + (" (Grafikkarte)" if "lib" not in gewaehlt[0] else " (Prozessor)"))
    return gewaehlt


# ---------------------------------------------------------------------------
# Videos
# ---------------------------------------------------------------------------
def video_ziel_endung(pfad: Path) -> str:
    """MP4/MOV bleiben, alles andere (3GP, AVI, MKV) wird zu MP4."""
    return pfad.suffix if pfad.suffix.lower() in (".mp4", ".mov", ".m4v") else ".mp4"


def video_komprimieren(quelle: Path, ziel: Path, cfg: dict, meldung=print) -> tuple[bool, str]:
    """
    Video platzsparend umwandeln. Rückgabe: (umgewandelt?, Hinweis).
    ziel darf gleich quelle sein (Ersetzen im Quellordner).
    """
    ac = cfg["ablage"]
    exe = ffmpeg_pfad()
    if not exe:
        return False, "ffmpeg nicht gefunden"
    info = video_info(quelle, exe)
    if not info:
        return False, "Video nicht lesbar"
    kurz = min(info["breite"], info["hoehe"])
    zielcodec = "hevc" if ac["video_codec"] == "hevc" else "h264"
    # Schon platzsparend? Gleicher Codec, nicht zu groß UND niedrige Datenrate.
    # (Handys speichern HEVC oft mit 10-20 Mbit/s – da ist noch viel zu holen.)
    ziel_kbit = (4000 if zielcodec == "hevc" else 7000) * (min(kurz, ac["video_max_hoehe"]) / 1080) ** 2
    if info["codec"] == zielcodec and kurz <= ac["video_max_hoehe"] and 0 < info["kbit"] <= ziel_kbit * 1.3:
        return False, "schon platzsparend"
    name, args = waehle_encoder(exe, ac)
    filter_ = []
    if kurz > ac["video_max_hoehe"]:  # kürzere Seite begrenzen (gilt für Hoch- und Querformat)
        m = int(ac["video_max_hoehe"])
        filter_ = ["-vf", f"scale='if(gt(iw,ih),-2,{m})':'if(gt(iw,ih),{m},-2)'"]
    tmp = ziel.with_name(ziel.stem + ".fotosort_tmp" + ziel.suffix)
    befehl = [exe, "-hide_banner", "-loglevel", "error", "-y", "-i", str(quelle),
              "-map", "0:v:0", "-map", "0:a?", "-map_metadata", "0", *filter_,
              "-c:v", name, *args, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
              "-movflags", "+faststart+use_metadata_tags", "-progress", "pipe:1", "-nostats", str(tmp)]
    start = letzte = time.time()
    try:
        p = subprocess.Popen(befehl, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             encoding="utf-8", errors="replace", **_ohne_fenster())
        for zeile in p.stdout:
            if zeile.startswith("out_time_ms=") and info["dauer"] > 0 and time.time() - letzte > 15:
                letzte = time.time()
                try:
                    anteil = int(zeile.split("=")[1]) / 1e6 / info["dauer"]
                    meldung(f"    {quelle.name}: {min(anteil, 1) * 100:.0f} %")
                except ValueError:
                    pass
        fehler = p.stderr.read()
        code = p.wait()
    except OSError as e:
        return False, f"ffmpeg-Fehler: {e}"
    if code != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        return False, f"ffmpeg-Fehler: {fehler.strip()[-200:]}"
    alt, neu = quelle.stat().st_size, tmp.stat().st_size
    if neu > alt * (1 - MINDEST_ERSPARNIS):
        tmp.unlink(missing_ok=True)
        return False, "kein nennenswerter Gewinn – Original behalten"
    st = quelle.stat()
    os.replace(tmp, ziel)
    os.utime(ziel, (st.st_atime, st.st_mtime))
    mb = lambda x: f"{x / 1e6:.1f}".replace(".", ",") if x < 1e7 else f"{x / 1e6:.0f}"
    return True, f"{mb(alt)} → {mb(neu)} MB ({100 * (1 - neu / alt):.0f} % kleiner) in {time.time() - start:.0f} s"


# ---------------------------------------------------------------------------
# Fotos
# ---------------------------------------------------------------------------
def bild_komprimieren(quelle: Path, ziel: Path, cfg: dict) -> tuple[bool, str]:
    """
    Foto verkleinern (über ablage.max_megapixel) und/oder neu komprimieren
    (ablage.neu_komprimieren). EXIF, Farbprofil und XMP bleiben erhalten.
    ziel darf gleich quelle sein. Rückgabe: (geändert?, Hinweis)
    """
    ac = cfg["ablage"]
    endung = quelle.suffix.lower()
    if endung not in BILD_ENDUNGEN or not (ac.get("verkleinern") or ac.get("neu_komprimieren")):
        return False, ""
    from PIL import Image

    grenze = ac["max_megapixel"] * 1_000_000
    with Image.open(quelle) as im:
        breite, hoehe = im.size
        zu_gross = ac.get("verkleinern") and breite * hoehe > grenze
        if not zu_gross and not (ac.get("neu_komprimieren") and endung in (".jpg", ".jpeg", ".webp")):
            return False, ""
        if zu_gross:
            f = (grenze / (breite * hoehe)) ** 0.5
            neu = (max(1, int(breite * f)), max(1, int(hoehe * f)))
            im.draft("RGB", neu)  # JPEG direkt verkleinert dekodieren (schnell, wenig Speicher)
        else:
            neu = (breite, hoehe)
        info = dict(im.info)
        if im.mode not in ("RGB", "L", "RGBA"):
            im = im.convert("RGB")
        bild = im.resize(neu, Image.LANCZOS) if im.size != neu else im.copy()
    optionen = {"optimize": True}
    for schluessel in ("exif", "icc_profile", "xmp"):
        if info.get(schluessel):
            optionen[schluessel] = info[schluessel]
    tmp = ziel.with_name(ziel.stem + ".fotosort_tmp" + ziel.suffix)
    try:
        if endung in (".jpg", ".jpeg"):
            bild.save(tmp, "JPEG", quality=ac["jpeg_qualitaet"], **optionen)
        elif endung == ".webp":
            bild.save(tmp, "WEBP", quality=ac["jpeg_qualitaet"], **optionen)
        else:
            optionen.pop("xmp", None)
            bild.save(tmp, "PNG", **optionen)
    except TypeError:  # ältere Pillow-Version ohne xmp-Parameter
        optionen.pop("xmp", None)
        bild.save(tmp, "JPEG" if endung in (".jpg", ".jpeg") else endung[1:].upper(),
                  **({"quality": ac["jpeg_qualitaet"]} if endung != ".png" else {}), **optionen)
    alt, neu_groesse = quelle.stat().st_size, tmp.stat().st_size
    if not zu_gross and neu_groesse > alt * (1 - MINDEST_ERSPARNIS):
        tmp.unlink(missing_ok=True)
        return False, ""
    st = quelle.stat()
    os.replace(tmp, ziel)
    os.utime(ziel, (st.st_atime, st.st_mtime))
    return True, f"{alt / 1e6:.1f} → {neu_groesse / 1e6:.1f} MB"


# ---------------------------------------------------------------------------
# Erster Schritt: Quellordner komprimieren (ersetzt Originale!)
# ---------------------------------------------------------------------------
def schaetze(items: list[dict], cfg: dict) -> dict:
    """Wie viele Dateien wären betroffen, wie viel Platz würde frei? (grobe Schätzung)"""
    ac = cfg["ablage"]
    fotos = videos = 0
    ersparnis = 0.0
    for i in items:
        groesse = i.get("groesse", 0)
        endung = Path(i["pfad"]).suffix.lower()
        if i["art"] in ("foto", "screenshot") and endung in BILD_ENDUNGEN:
            mp = i.get("breite", 0) * i.get("hoehe", 0) / 1e6
            if ac.get("verkleinern") and mp > ac["max_megapixel"]:
                fotos += 1
                ersparnis += groesse * (1 - ac["max_megapixel"] / mp) * 0.9
            elif ac.get("neu_komprimieren") and endung in (".jpg", ".jpeg"):
                fotos += 1
                ersparnis += groesse * 0.2
        elif i["art"] == "video" and ac.get("videos_komprimieren") and endung in VIDEO_ENDUNGEN:
            videos += 1
            ersparnis += groesse * 0.55  # HEVC/1080p spart typischerweise 40-70 %
    return {"fotos": fotos, "videos": videos, "ersparnis_gb": round(ersparnis / 1e9, 1),
            "ersparnis_mb": round(ersparnis / 1e6)}


def quellordner_komprimieren(items: list[dict], cfg: dict) -> dict:
    """Originale in den Quellordnern durch komprimierte Fassungen ersetzen."""
    from .fortschritt import Fortschritt

    ac = cfg["ablage"]
    kandidaten = [i for i in items if Path(i["pfad"]).exists() and (
        (i["art"] in ("foto", "screenshot") and Path(i["pfad"]).suffix.lower() in BILD_ENDUNGEN
         and (ac.get("verkleinern") or ac.get("neu_komprimieren")))
        or (i["art"] == "video" and ac.get("videos_komprimieren")
            and Path(i["pfad"]).suffix.lower() in VIDEO_ENDUNGEN))]
    # Videos zuletzt (dauern am längsten), große Fotos zuerst
    kandidaten.sort(key=lambda i: (i["art"] == "video", -i.get("groesse", 0)))
    statistik = {"fotos": 0, "videos": 0, "frei_mb": 0, "fehler": 0}
    fortschritt = Fortschritt("Quellordner komprimieren", len(kandidaten), "Dateien")
    for i in kandidaten:
        pfad = Path(i["pfad"])
        vorher = pfad.stat().st_size
        try:
            if i["art"] == "video":
                ziel = pfad.with_suffix(video_ziel_endung(pfad))
                ok, hinweis = video_komprimieren(pfad, ziel, cfg)
                if ok and ziel != pfad:
                    pfad.unlink()  # z.B. AVI -> MP4: alte Datei entfernen
                if ok:
                    statistik["videos"] += 1
                    print(f"  Video {pfad.name}: {hinweis}")
                    statistik["frei_mb"] += (vorher - ziel.stat().st_size) / 1e6
                elif "Fehler" in hinweis:
                    print(f"  {pfad.name}: {hinweis}")
                    statistik["fehler"] += 1
            else:
                ok, _ = bild_komprimieren(pfad, pfad, cfg)
                if ok:
                    statistik["fotos"] += 1
                    statistik["frei_mb"] += (vorher - pfad.stat().st_size) / 1e6
        except Exception as fehler:
            print(f"  FEHLER bei {pfad.name}: {fehler}")
            statistik["fehler"] += 1
        fortschritt.schritt()
    fortschritt.fertig(f"{statistik['frei_mb'] / 1000:.1f} GB frei geworden".replace(".", ","))
    return statistik
