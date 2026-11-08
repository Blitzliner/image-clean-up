"""
Quelle (wer hat das Foto gemacht?) und echten Aufnahmezeitpunkt bestimmen.

Reihenfolge für den Zeitpunkt:
  1. EXIF "DateTimeOriginal" (die Kamera weiß es am besten)
  2. Datum + Uhrzeit aus dem Dateinamen (PXL_ wird von UTC umgerechnet)
  3. Nur Datum aus dem Dateinamen (WhatsApp: IMG-20260524-WA0003)
     -> Uhrzeit wird geschätzt, Bild als "zeit_unsicher" markiert
  4. Änderungsdatum der Datei (letzter Notnagel)
"""
from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

FOTO_ENDUNGEN = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff"}
VIDEO_ENDUNGEN = {".mp4", ".mov", ".3gp", ".m4v", ".avi", ".mkv"}

# (Quelle, Muster) – das erste passende Muster gewinnt.
# Eigene Muster aus der config.yaml ("quellen_muster") kommen davor.
STANDARD_QUELLEN = [
    ("screenshot", r"^Screenshot"),
    ("whatsapp", r"^(IMG|VID)-\d{8}-WA\d+"),
    ("whatsapp", r"^WhatsApp (Image|Video|Bild)"),
    ("mann", r"^PXL_"),
    ("eigene", r"^(IMG_|VID_)?\d{8}_\d{6}"),
]

# Datumsmuster im Dateinamen
_MUSTER_DATUM_ZEIT = re.compile(r"(20\d{2})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})")
_MUSTER_WHATSAPP_LANG = re.compile(
    r"(20\d{2})-(\d{2})-(\d{2}) at (\d{2})\.(\d{2})\.(\d{2})"
)
_MUSTER_NUR_DATUM = re.compile(r"(20\d{2})(\d{2})(\d{2})")


def erkenne_quelle(dateiname: str, extra_muster: list[dict] | None = None) -> str:
    """Liefert 'eigene', 'mann', 'whatsapp', 'screenshot' oder 'sonstige'."""
    muster = [(m["quelle"], m["muster"]) for m in (extra_muster or [])]
    for quelle, regex in muster + STANDARD_QUELLEN:
        if re.search(regex, dateiname, re.IGNORECASE):
            return quelle
    return "sonstige"


def erkenne_art(pfad: Path, quelle: str) -> str | None:
    """'foto', 'video', 'screenshot' oder None (keine Mediendatei)."""
    endung = pfad.suffix.lower()
    if endung in VIDEO_ENDUNGEN:
        return "video"
    if endung in FOTO_ENDUNGEN:
        return "screenshot" if quelle == "screenshot" else "foto"
    return None


def _datum(*teile) -> dt.datetime | None:
    try:
        d = dt.datetime(*[int(t) for t in teile])
    except ValueError:
        return None
    # Plausibilitätsprüfung: keine Fantasie-Daten
    if not (1990 <= d.year <= dt.date.today().year + 1):
        return None
    return d


def zeit_aus_exif(exif_text: str | None) -> dt.datetime | None:
    """EXIF-Datumsformat '2026:05:24 09:18:37' in datetime umwandeln."""
    if not exif_text:
        return None
    m = re.match(r"(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", str(exif_text))
    return _datum(*m.groups()) if m else None


def bestimme_zeit(
    pfad: Path,
    quelle: str,
    exif_zeit: dt.datetime | None,
    zeitzone: str,
    utc_quellen: list[str],
) -> tuple[dt.datetime, str, bool]:
    """
    Aufnahmezeitpunkt bestimmen.
    Rückgabe: (zeitpunkt als lokale Zeit ohne Zeitzone, Herkunft, unsicher?)
    """
    if exif_zeit:
        return exif_zeit, "exif", False

    name = pfad.name
    tz = ZoneInfo(zeitzone)

    m = _MUSTER_DATUM_ZEIT.search(name) or _MUSTER_WHATSAPP_LANG.search(name)
    if m:
        d = _datum(*m.groups())
        if d:
            if quelle in utc_quellen:
                # Pixel benennt Dateien in UTC -> in lokale Zeit umrechnen
                d = d.replace(tzinfo=dt.timezone.utc).astimezone(tz).replace(tzinfo=None)
            return d, "dateiname", False

    mtime = dt.datetime.fromtimestamp(os.path.getmtime(pfad))
    m = _MUSTER_NUR_DATUM.search(name)
    if m:
        d = _datum(*m.groups())
        if d:
            # WhatsApp: nur das Datum ist bekannt. Wenn die Datei am selben Tag
            # gespeichert wurde, ist deren Uhrzeit eine gute Schätzung,
            # sonst nehmen wir 12:00 Uhr.
            if mtime.date() == d.date():
                d = d.replace(hour=mtime.hour, minute=mtime.minute, second=mtime.second)
            else:
                d = d.replace(hour=12)
            return d, "dateiname_datum", True

    return mtime, "dateizeit", True


def gps_aus_exif(gps_ifd: dict) -> tuple[float, float] | tuple[None, None]:
    """GPS-Block aus EXIF in Dezimalgrad umrechnen."""
    try:
        def grad(werte, ref):
            g, m, s = (float(v) for v in werte)
            wert = g + m / 60 + s / 3600
            return -wert if ref in ("S", "W") else wert

        lat = grad(gps_ifd[2], gps_ifd.get(1, "N"))
        lon = grad(gps_ifd[4], gps_ifd.get(3, "E"))
        if lat == 0 and lon == 0:
            return None, None
        return round(lat, 6), round(lon, 6)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None, None
