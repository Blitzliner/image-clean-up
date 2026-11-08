"""
Sterne-Bewertung lesen und schreiben (nur JPEG).

Favoriten bekommen 5 Sterne. Die Sterne werden so gespeichert, wie es auch
der Windows-Explorer macht (EXIF "Rating" + XMP "xmp:Rating"). Dadurch:
  - siehst du die Sterne im Explorer (Spalte "Bewertung") und vielen Foto-Apps
  - kannst du im Explorer selbst Sterne vergeben/entfernen – das PDF
    ("fotosort pdf") nimmt dann genau deine Auswahl.

Die Bilddaten selbst werden dabei NICHT neu komprimiert.
"""
from __future__ import annotations

import os
import re
import struct
import tempfile
from pathlib import Path

XMP_KOPF = b"http://ns.adobe.com/xap/1.0/\x00"
PROZENT = {0: 0, 1: 1, 2: 25, 3: 50, 4: 75, 5: 99}  # so speichert es Windows


def _segmente(daten: bytes):
    """JPEG-Segmente bis zum Bildbeginn (SOS) durchlaufen: (start, ende, marker)."""
    pos = 2  # nach SOI (FFD8)
    while pos + 4 <= len(daten) and daten[pos] == 0xFF:
        marker = daten[pos + 1]
        if marker == 0xDA:  # Start of Scan -> ab hier Bilddaten
            break
        laenge = struct.unpack(">H", daten[pos + 2:pos + 4])[0]
        yield pos, pos + 2 + laenge, marker
        pos += 2 + laenge


def _xmp_finden(daten: bytes) -> tuple[int, int] | None:
    for start, ende, marker in _segmente(daten):
        if marker == 0xE1 and daten[start + 4:start + 4 + len(XMP_KOPF)] == XMP_KOPF:
            return start, ende
    return None


def _xmp_mit_rating(xmp: str, sterne: int) -> str:
    """xmp:Rating in einem vorhandenen XMP-Paket setzen oder ergänzen."""
    if re.search(r'xmp:Rating="\d+"', xmp):
        return re.sub(r'xmp:Rating="\d+"', f'xmp:Rating="{sterne}"', xmp)
    if re.search(r"<xmp:Rating>\d+</xmp:Rating>", xmp):
        return re.sub(r"<xmp:Rating>\d+</xmp:Rating>", f"<xmp:Rating>{sterne}</xmp:Rating>", xmp)
    # Attribut an die erste rdf:Description anhängen
    m = re.search(r"<rdf:Description\b([^>]*?)(/?)>", xmp)
    if not m:
        return xmp
    attribute = m.group(1)
    zusatz = "" if "xmlns:xmp=" in attribute else ' xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
    zusatz += f' xmp:Rating="{sterne}"'
    return xmp[:m.end(1)] + zusatz + xmp[m.end(1):]


def _neues_xmp(sterne: int) -> str:
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
        f'xmp:Rating="{sterne}"/></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
    )


def schreibe_sterne(pfad: str | Path, sterne: int = 5) -> bool:
    """Sterne in eine JPEG-Datei schreiben. Gibt True bei Erfolg zurück."""
    pfad = Path(pfad)
    if pfad.suffix.lower() not in (".jpg", ".jpeg"):
        return False

    # 1) EXIF-Rating (piexif fügt EXIF ein, ohne das Bild neu zu komprimieren)
    try:
        import piexif

        exif = piexif.load(str(pfad))
        exif["0th"][0x4746] = sterne
        exif["0th"][0x4749] = PROZENT.get(sterne, 0)
        piexif.insert(piexif.dump(exif), str(pfad))
    except Exception:
        pass  # manche Hersteller-EXIFs mag piexif nicht – XMP reicht dann

    # 2) XMP-Rating
    try:
        daten = pfad.read_bytes()
        if daten[:2] != b"\xff\xd8":
            return False
        fund = _xmp_finden(daten)
        if fund:
            start, ende = fund
            alt = daten[start + 4 + len(XMP_KOPF):ende].decode("utf-8", errors="replace")
            neu_xmp = _xmp_mit_rating(alt, sterne).encode("utf-8")
        else:
            # hinter die vorhandenen APP-Segmente (EXIF) einfügen
            start = ende = max((e for s, e, m in _segmente(daten) if 0xE0 <= m <= 0xEF), default=2)
            neu_xmp = _neues_xmp(sterne).encode("utf-8")
        nutzlast = XMP_KOPF + neu_xmp
        if len(nutzlast) + 2 > 0xFFFF:
            return False
        segment = b"\xff\xe1" + struct.pack(">H", len(nutzlast) + 2) + nutzlast
        neu = daten[:start] + segment + daten[ende:]

        # Sicher schreiben: erst temporäre Datei, dann ersetzen
        fd, tmp = tempfile.mkstemp(dir=pfad.parent, suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(neu)
        stat = pfad.stat()
        os.replace(tmp, pfad)
        os.utime(pfad, (stat.st_atime, stat.st_mtime))  # Zeitstempel behalten
        return True
    except Exception:
        return False


def lese_sterne(pfad: str | Path) -> int:
    """Sterne lesen (XMP bevorzugt, sonst EXIF). 0 = keine Bewertung."""
    pfad = Path(pfad)
    try:
        with open(pfad, "rb") as f:
            kopf = f.read(512 * 1024)
        m = re.search(rb'xmp:Rating="(\d+)"', kopf) or re.search(rb"<xmp:Rating>(\d+)</xmp:Rating>", kopf)
        if m:
            return int(m.group(1))
        from PIL import Image

        with Image.open(pfad) as im:
            return int(im.getexif().get(0x4746) or 0)
    except Exception:
        return 0
