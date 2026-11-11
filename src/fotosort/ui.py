"""
Oberfläche im Browser:  uv run fotosort ui

Startet einen kleinen Webserver NUR auf diesem PC (127.0.0.1) und öffnet den
Browser. Darüber lässt sich alles steuern:
  1. Einstellungen  (Quellordner, NAS-Ziel, Modus, Zuhause, ...)
  2. Analysieren    (läuft im Hintergrund, Fortschritt wird angezeigt)
  3. Ereignisse     (erkannte Reisen benennen/ignorieren, eigene anlegen)
  4. Prüfen         (alle Bilder chronologisch; per Klick Favorit setzen
                     oder aussortieren/zurückholen – wird gespeichert)
  5. Ablegen & PDF  (auf das NAS kopieren/verschieben, Jahresrückblick)

Es werden nur Python-Standardbibliotheken für den Server verwendet.
Die eigentliche Arbeit machen dieselben Module wie auf der Kommandozeile.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import threading
import traceback
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml
from PIL import Image, ImageOps

from . import fortschritt, personen, planung, themen
from .ablage import ablegen
from .config import lade_config
from .ereignisse import lade_ereignisse, speichere_ereignisse
from .jahresrueckblick import erstelle_rueckblicke

HTML_DATEI = Path(__file__).with_name("ui.html")


# ---------------------------------------------------------------------------
# Zustand der Anwendung (ein Plan, ein laufender Auftrag)
# ---------------------------------------------------------------------------
class _LogSchreiber(io.TextIOBase):
    """Fängt print()-Ausgaben der Module ab und zeigt sie in der Oberfläche."""

    def __init__(self, ziel: deque):
        self.ziel = ziel
        self.puffer = ""

    def write(self, text: str) -> int:
        self.puffer += text
        while "\n" in self.puffer:
            zeile, self.puffer = self.puffer.split("\n", 1)
            if zeile.strip():
                self.ziel.append(zeile)
        return len(text)


class Zustand:
    def __init__(self, config_pfad: str):
        self.config_pfad = Path(config_pfad).resolve()
        self.lock = threading.Lock()
        self.log: deque[str] = deque(maxlen=400)
        self.auftrag: str | None = None  # Name des laufenden Auftrags
        self.fehler: str | None = None
        self.items: list[dict] = []
        self.gruppen: list[dict] = []
        self.statistik: dict = {}
        self.cfg: dict = lade_config(self.config_pfad)
        self.pdfs: list[str] = []
        self.version = 0  # steigt bei jedem Auftrag -> Oberfläche lädt neu
        self.thumb_ordner = Path(self.cfg["_basis"]) / "vorschau" / "thumbs"
        self.thumb_ordner.mkdir(parents=True, exist_ok=True)

    # --- Hintergrund-Aufträge -------------------------------------------------
    def starte(self, name: str, funktion) -> bool:
        with self.lock:
            if self.auftrag:
                return False
            self.auftrag, self.fehler = name, None
        try:  # vor jeder größeren Aktion automatisch einen Zwischenstand sichern
            sichern(self, "vor_" + name.split()[0].lower())
        except OSError:
            pass
        self.log.append(f"— {name} gestartet ({dt.datetime.now():%H:%M:%S}) —")

        def lauf():
            try:
                with contextlib.redirect_stdout(_LogSchreiber(self.log)):
                    funktion()
                self.log.append(f"— {name} fertig —")
            except SystemExit:
                self.fehler = "Abgebrochen – siehe Protokoll."
            except Exception as fehler:  # Fehler in der Oberfläche anzeigen
                # Wo ist es passiert? (letzte Stelle im fotosort-Code)
                stapel = traceback.extract_tb(fehler.__traceback__)
                eigene = [f for f in stapel if "fotosort" in f.filename.replace("\\", "/")]
                ort = ""
                if eigene:
                    f = eigene[-1]
                    ort = f" (in {Path(f.filename).name}, Zeile {f.lineno}, {f.name})"
                self.fehler = f"{type(fehler).__name__}: {fehler}{ort}"
                self.log.append(f"FEHLER: {self.fehler}")
                for zeile in traceback.format_exception(fehler)[-6:]:
                    for z in zeile.rstrip().splitlines():
                        self.log.append("  " + z)
                self.log.append("Die Oberfläche läuft weiter. Bitte diese Meldung weitergeben, "
                                "falls der Fehler bleibt.")
            finally:
                self.version += 1
                self.auftrag = None

        threading.Thread(target=lauf, daemon=True).start()
        return True

    def neu_planen(self) -> None:
        from .cli import plan_erstellen, zusammenfassung

        self.cfg = lade_config(self.config_pfad)
        items, gruppen, statistik = plan_erstellen(self.cfg, interaktiv=False)
        zusammenfassung(items, gruppen, statistik)
        self.items, self.gruppen, self.statistik = items, gruppen, statistik

    def zielpfade_neu(self) -> None:
        """Nach einer Korrektur nur die Zielpfade neu berechnen (schnell)."""
        planung.zielpfade(self.items, self.cfg)
        self.statistik["behalten"] = sum(1 for i in self.items if i["status"] == "behalten")
        self.statistik["favoriten"] = sum(1 for i in self.items if i["favorit"])


# ---------------------------------------------------------------------------
# Konfiguration lesen/schreiben (nur die Werte, die die Oberfläche kennt)
# ---------------------------------------------------------------------------
UI_FELDER = [
    "quellen", "ziel", "modus", "zuhause", "ordner.alltag", "aussortieren.staerke",
    "themen.inhalte_erkennen", "themen.kinder.aktiv", "themen.kinder.seiten",
    "themen.freunde.aktiv", "themen.freunde.seiten", "themen.haus.aktiv", "themen.haus.seiten",
    "themen.haus.eigenes_pdf", "themen.sonstiges_seiten", "pdf.max_beschnitt", "pdf.pptx",
    "ablage.verkleinern", "ablage.max_megapixel", "ablage.jpeg_qualitaet", "analyse.max_megapixel",
    "ablage.neu_komprimieren", "ablage.videos_komprimieren", "ablage.video_codec",
    "ablage.video_max_hoehe", "ablage.video_qualitaet", "ablage.video_hardware", "ablage.themen_kopien",
    "reise.min_entfernung_km", "reise.max_luecke_tage", "reise.min_fotos", "reise.min_vorschlag",
    "serien.aktion", "serien.behalten", "favoriten.anteil", "favoriten.max",
    "pdf.titel", "pdf.untertitel", "pdf.zoom_staerke", "pdf.dpi", "pdf.jpeg_qualitaet",
    "pdf.min_sterne", "pdf.bild_datum", "pdf.max_seiten_pro_ereignis", "pdf.seitenzahlen",
]


def _hole(d: dict, schluessel: str):
    for teil in schluessel.split("."):
        d = d.get(teil, {}) if isinstance(d, dict) else {}
    return d


def config_fuer_ui(cfg: dict) -> dict:
    return {k: _hole(cfg, k) for k in UI_FELDER}


def speichere_config(pfad: Path, werte: dict) -> None:
    roh = {}
    if pfad.exists():
        roh = yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}
    for k, v in werte.items():
        if k not in UI_FELDER:
            continue
        ziel = roh
        teile = k.split(".")
        for t in teile[:-1]:
            ziel = ziel.setdefault(t, {})
        ziel[teile[-1]] = v
    kopf = ("# fotosort – Konfiguration (zuletzt über die Oberfläche gespeichert).\n"
            "# Alle Einstellungen mit Erklärung: src/fotosort/config.py\n\n")
    pfad.write_text(kopf + yaml.safe_dump(roh, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


def ordner_waehlen() -> str:
    """Windows-Ordnerdialog öffnen (läuft auf demselben PC wie der Browser)."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        pfad = filedialog.askdirectory(title="Ordner wählen")
        root.destroy()
        return pfad or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Bilder für die Oberfläche
# ---------------------------------------------------------------------------
def _bild_bytes(item: dict, kante: int, cache: Path | None) -> bytes | None:
    datei = None
    if cache is not None:
        datei = cache / f"{item.get('sha1', '')[:16]}_{kante}.jpg"
        if datei.exists():
            return datei.read_bytes()
    if item["art"] == "video":
        daten = _video_vorschau(item, kante)
        if daten and datei is not None:
            datei.write_bytes(daten)
        return daten
    try:
        im = Image.open(item["pfad"])
        im.draft("RGB", (kante * 2, kante * 2))
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((kante, kante))
        puffer = io.BytesIO()
        im.save(puffer, "JPEG", quality=80)
        daten = puffer.getvalue()
        if datei is not None:
            datei.write_bytes(daten)
        return daten
    except Exception:
        return None



# ---------------------------------------------------------------------------
# Zwischenstand: Entwürfe (nicht gespeicherte Eingaben) und Sicherungen
# ---------------------------------------------------------------------------
ZUSTANDS_DATEIEN = ["config.yaml", "ereignisse.yaml", "korrekturen.json", "personen.json", "entwurf.json"]
MAX_SICHERUNGEN = 40


def sichern(z: "Zustand", grund: str) -> str | None:
    """Alle Zustandsdateien in sicherungen/<Zeit>_<grund>/ kopieren (die 40 neuesten bleiben)."""
    import shutil

    basis = Path(z.cfg["_basis"])
    vorhanden = [basis / n for n in ZUSTANDS_DATEIEN if (basis / n).exists()]
    if not vorhanden:
        return None
    ordner = basis / "sicherungen" / f"{dt.datetime.now():%Y-%m-%d_%H-%M-%S}_{grund}"
    ordner.mkdir(parents=True, exist_ok=True)
    for d in vorhanden:
        shutil.copy2(d, ordner / d.name)
    alle = sorted((basis / "sicherungen").iterdir())
    for alt in alle[:-MAX_SICHERUNGEN]:
        shutil.rmtree(alt, ignore_errors=True)
    return ordner.name


def sicherungen_liste(z: "Zustand") -> list[dict]:
    ordner = Path(z.cfg["_basis"]) / "sicherungen"
    if not ordner.exists():
        return []
    return [{"name": o.name, "dateien": sorted(p.name for p in o.iterdir())}
            for o in sorted(ordner.iterdir(), reverse=True) if o.is_dir()]


def wiederherstellen(z: "Zustand", name: str) -> bool:
    import shutil

    basis = Path(z.cfg["_basis"])
    quelle = basis / "sicherungen" / Path(name).name
    if not quelle.is_dir():
        return False
    sichern(z, "vor_wiederherstellen")
    for n in ZUSTANDS_DATEIEN:
        if (quelle / n).exists():
            shutil.copy2(quelle / n, basis / n)
        elif n == "entwurf.json" and (basis / n).exists():
            (basis / n).unlink()
    return True


def lade_entwurf(z: "Zustand") -> dict:
    p = Path(z.cfg["_basis"]) / "entwurf.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def speichere_entwurf(z: "Zustand", teil: str, daten) -> str:
    entwurf = lade_entwurf(z)
    if daten is None:
        entwurf.pop(teil, None)
    else:
        entwurf[teil] = {"daten": daten, "zeit": f"{dt.datetime.now():%d.%m. %H:%M:%S}"}
    p = Path(z.cfg["_basis"]) / "entwurf.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(entwurf, ensure_ascii=False, indent=1), encoding="utf-8")
    import os

    os.replace(tmp, p)
    return entwurf.get(teil, {}).get("zeit", "")


def ereignis_details(z: "Zustand", eigene: list[dict], auto: list[dict]) -> dict:
    """
    Für die Ereignis-Ansicht: wie viele Fotos/Videos landen in welchem Ordner,
    5 Beispielbilder und die Tage des Zeitraums (mit Anzahl, ausgenommen ja/nein).
    """
    from collections import Counter

    pro_tag = Counter(i["zeit"][:10] for i in z.items if i["art"] != "screenshot")
    zugeordnet: dict[str, list[int]] = {}
    for idx, i in enumerate(z.items):
        if i.get("ereignis"):
            zugeordnet.setdefault(i["ereignis"], []).append(idx)

    def beispiele(ids):
        fotos = [k for k in ids if z.items[k]["art"] == "foto" and z.items[k]["status"] == "behalten"]
        fotos.sort(key=lambda k: -(z.items[k].get("score") or 0))
        auswahl = fotos[:5] or [k for k in ids if z.items[k]["art"] != "foto"][:5]
        return sorted(auswahl, key=lambda k: z.items[k]["zeit"])

    def tage(e):
        if not e.get("von"):
            return []
        von, bis = dt.date.fromisoformat(str(e["von"])), dt.date.fromisoformat(str(e.get("bis") or e["von"]))
        aus = {str(t) for t in (e.get("ohne_tage") or [])}
        liste = []
        tag = von
        while tag <= bis and len(liste) < 120:
            k = tag.isoformat()
            liste.append({"tag": k, "anzahl": pro_tag.get(k, 0), "aus": k in aus})
            tag += dt.timedelta(days=1)
        return liste

    for e in eigene:
        ids = zugeordnet.get(e["name"], [])
        ordner = sorted({f'{z.items[k]["jahr"]}/{z.items[k]["gruppe"]}' for k in ids
                         if z.items[k]["status"] == "behalten"})
        e["_info"] = {
            "fotos": sum(1 for k in ids if z.items[k]["art"] == "foto"),
            "videos": sum(1 for k in ids if z.items[k]["art"] == "video"),
            "aussortiert": sum(1 for k in ids if z.items[k]["status"] == "aussortiert"),
            "ordner": ordner, "beispiele": beispiele(ids), "tage": tage(e),
        }
    for r in auto:
        von, bis = r["von"], r["bis"]
        ids = [k for k, i in enumerate(z.items) if von <= i["zeit"][:10] <= bis and i["art"] != "screenshot"]
        r["_info"] = {"beispiele": beispiele(ids), "tage": tage(r)}
    return dict(pro_tag)


def _video_vorschau(item: dict, kante: int) -> bytes | None:
    """Ein Standbild aus dem Video (nach ca. 1 s) als Vorschau."""
    import subprocess

    from .komprimieren import _ohne_fenster, ffmpeg_pfad

    exe = ffmpeg_pfad()
    if not exe:
        return None
    for start in ("1", "0"):
        try:
            r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-ss", start, "-i", item["pfad"],
                                "-frames:v", "1", "-vf", f"scale='min({kante},iw)':-2", "-f", "image2",
                                "-c:v", "mjpeg", "pipe:1"], capture_output=True, timeout=30, **_ohne_fenster())
            if r.returncode == 0 and r.stdout:
                return r.stdout
        except (subprocess.TimeoutExpired, OSError):
            return None
    return None


def _gesicht_bytes(z: "Zustand", idx: int, g_idx: int) -> bytes | None:
    """Ausschnitt eines Gesichts (für den Reiter "Personen")."""
    try:
        item = z.items[idx]
        x, y, w, h, _ = item["gesichter"][g_idx]
        im = Image.open(item["pfad"])
        im.draft("RGB", (1600, 1600))
        im = ImageOps.exif_transpose(im).convert("RGB")
        W, H = im.size
        rand = 0.35
        box = (int(max(0, (x - w * rand) * W)), int(max(0, (y - h * rand) * H)),
               int(min(W, (x + w * (1 + rand)) * W)), int(min(H, (y + h * (1 + rand)) * H)))
        teil = im.crop(box)
        teil.thumbnail((160, 160))
        puffer = io.BytesIO()
        teil.save(puffer, "JPEG", quality=85)
        return puffer.getvalue()
    except Exception:
        return None


def rueckblick_vorschau(z: "Zustand") -> list[dict]:
    """
    Seiten des Jahresrückblicks so planen, wie sie mit dem aktuellen Plan
    entstehen würden (Favoriten = Sterne). Für die Ansicht "Rückblick".
    """
    from .jahresrueckblick import plane_jahresrueckblick

    seiten = []
    for jahr in sorted({i["jahr"] for i in z.items}):
        bilder = [{
            "id": idx, "pfad": Path(i["pfad"]), "zeit": dt.datetime.fromisoformat(i["zeit"]),
            "score": i.get("score") or 0, "gruppe": i["gruppe"],
            "gruppen_art": i.get("gruppen_art") or "alltag", "themen": set(i.get("themen", [])),
            "haus_bereich": i.get("haus_bereich", ""), "sterne": 5 if i.get("favorit") else 0,
            "im_rueckblick": i.get("im_rueckblick", True),
        } for idx, i in enumerate(z.items)
            if i["jahr"] == jahr and i["art"] == "foto" and i["status"] == "behalten"]
        geplant = plane_jahresrueckblick(bilder, z.cfg)
        benutzt = set()
        for nr, s in enumerate(geplant, start=1):
            ids = [b["id"] for b in s["bilder"]]
            benutzt.update(ids)
            seiten.append({"jahr": jahr, "nr": nr, "titel": s["titel"], "untertitel": s["untertitel"],
                           "teil": s["teil"], "ids": ids})
        rest = [b for b in bilder if b["sterne"] and b["id"] not in benutzt]
        if rest:
            seiten.append({"jahr": jahr, "nr": 0, "titel": "Favoriten ohne Platz im Rückblick",
                           "untertitel": "Ereignis ausgeschlossen oder Seiten voll", "teil": "",
                           "ids": [b["id"] for b in sorted(rest, key=lambda b: b["zeit"])]})
    return seiten


def plan_als_json(z: Zustand) -> dict:
    namen = {g["gruppe"]: g for g in z.gruppen}
    gruppen: dict[tuple, dict] = {}
    for idx, i in enumerate(z.items):
        schluessel = (i["jahr"], i["gruppe"])
        g = gruppen.setdefault(schluessel, {
            "jahr": i["jahr"], "gruppe": i["gruppe"],
            "name": namen.get(i["gruppe"], {}).get("name", i["gruppe"]),
            "art": namen.get(i["gruppe"], {}).get("art", i.get("gruppen_art") or "sonstiges"),
            "bilder": []})
        g["bilder"].append({
            "id": idx, "name": i["name"], "zeit": i["zeit"], "quelle": i["quelle"],
            "art": i["art"], "status": i["status"], "grund": i.get("grund", ""),
            "favorit": i.get("favorit", False), "score": i.get("score"),
            "serie": i.get("serie"), "unsicher": i.get("zeit_unsicher", False),
            "manuell": i.get("manuell", False), "ziel": i.get("ziel", ""),
            "pfad": i["pfad"], "gesichter": len(i.get("gesichter", [])),
            "themen": i.get("themen", []), "personen": i.get("personen", []),
            "kopien": len(i.get("kopien", [])),
            "problem": i.get("fehler") or "; ".join(i.get("hinweise", [])),
        })
    liste = sorted(gruppen.values(),
                   key=lambda g: (g["jahr"], not g["gruppe"][:4].isdigit(), g["gruppe"]))
    # Rückblick-Vorschau + Abzeichen je Bild ("Seite 3 · Kinder")
    seiten = rueckblick_vorschau(z)
    abzeichen = {}
    for s in seiten:
        text = (f"S. {s['nr']} · {s['titel']}" if s["nr"] else "kein Platz im Rückblick")
        for i in s["ids"]:
            abzeichen[i] = text
    for g in liste:
        for b in g["bilder"]:
            b["rueckblick"] = abzeichen.get(b["id"], "")
    ereignisse = sorted({i["ereignis"] for i in z.items if i.get("ereignis")})
    return {"gruppen": liste, "statistik": z.statistik, "rueckblick": seiten, "ereignisse": ereignisse}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def baue_handler(z: Zustand):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keine Konsolen-Ausgabe pro Anfrage
            pass

        # -- Antworten --
        def _json(self, daten, code=200):
            roh = json.dumps(daten, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(roh)))
            self.end_headers()
            self.wfile.write(roh)

        def _bytes(self, daten: bytes, typ: str, cache=True):
            self.send_response(200)
            self.send_header("Content-Type", typ)
            self.send_header("Content-Length", str(len(daten)))
            if cache:
                self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(daten)

        def _body(self) -> dict:
            laenge = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(laenge) or b"{}")

        def _item(self, text: str) -> dict | None:
            try:
                return z.items[int(text)]
            except (ValueError, IndexError):
                return None

        # -- GET --
        def do_GET(self):
            url = urlparse(self.path)
            teile = url.path.strip("/").split("/")
            if url.path == "/":
                return self._bytes(HTML_DATEI.read_bytes(), "text/html; charset=utf-8", cache=False)
            if url.path == "/api/zustand":
                return self._json({
                    "auftrag": z.auftrag, "fehler": z.fehler, "version": z.version,
                    "fortschritt": fortschritt.stand(), "schritt": fortschritt.aktueller_schritt(), "log": list(z.log)[-150:],
                    "hat_plan": bool(z.items), "statistik": z.statistik,
                    "config": config_fuer_ui(z.cfg), "config_pfad": str(z.config_pfad),
                    "jahre": sorted({i["jahr"] for i in z.items}), "pdfs": z.pdfs,
                    "zuhause": z.cfg.get("_zuhause"),
                })
            if url.path == "/api/plan":
                return self._json(plan_als_json(z))
            if url.path == "/api/personen":
                liste = [{k: v for k, v in p.items() if not k.startswith("_")}
                         for p in z.cfg.get("_personen", [])]
                return self._json({"personen": liste, "rollen": personen.ROLLEN})
            if teile[0] == "gesicht" and len(teile) == 3:
                daten = _gesicht_bytes(z, int(teile[1]), int(teile[2]))
                if daten:
                    return self._bytes(daten, "image/jpeg")
                return self._json({"fehler": "nicht gefunden"}, 404)
            if url.path == "/api/ereignisse":
                eigene = lade_ereignisse(z.cfg["ereignisse_datei"])
                for e in eigene:
                    for k in ("von", "bis"):
                        if isinstance(e.get(k), dt.date):
                            e[k] = e[k].isoformat()
                    e["ohne_tage"] = [t.isoformat() for t in e.get("ohne_tage", []) if t]
                auto = [dict(r) for r in z.cfg.get("_auto_reisen", [])]
                pro_tag = ereignis_details(z, eigene, auto) if z.items else {}
                for e in eigene + auto:
                    if "_info" in e:
                        e["info"] = e.pop("_info")
                return self._json({"eigene": eigene, "auto": auto, "pro_tag": pro_tag,
                                   "entwurf": lade_entwurf(z).get("ereignisse")})
            if url.path == "/api/entwurf":
                return self._json(lade_entwurf(z))
            if url.path == "/api/sicherungen":
                return self._json({"sicherungen": sicherungen_liste(z)})
            if url.path == "/api/komprimieren_schaetzen":
                from .komprimieren import ffmpeg_pfad, schaetze

                return self._json({**schaetze(z.items, z.cfg), "ffmpeg": bool(ffmpeg_pfad()),
                                   "hat_plan": bool(z.items)})
            if teile[0] in ("thumb", "bild") and len(teile) == 2:
                item = self._item(teile[1])
                if item:
                    kante = 320 if teile[0] == "thumb" else 1600
                    daten = _bild_bytes(item, kante, z.thumb_ordner if kante == 320 else None)
                    if daten:
                        return self._bytes(daten, "image/jpeg")
                return self._json({"fehler": "nicht gefunden"}, 404)
            if url.path in ("/pdf", "/datei"):
                nr = int(parse_qs(url.query).get("nr", ["0"])[0])
                if 0 <= nr < len(z.pdfs) and Path(z.pdfs[nr]).exists():
                    datei = Path(z.pdfs[nr])
                    if datei.suffix == ".pdf":
                        return self._bytes(datei.read_bytes(), "application/pdf", cache=False)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument."
                                     "presentationml.presentation")
                    self.send_header("Content-Disposition", f'attachment; filename="{datei.name}"')
                    roh = datei.read_bytes()
                    self.send_header("Content-Length", str(len(roh)))
                    self.end_headers()
                    self.wfile.write(roh)
                    return
                return self._json({"fehler": "keine Datei"}, 404)
            self._json({"fehler": "unbekannt"}, 404)

        # -- POST --
        def do_POST(self):
            pfad = urlparse(self.path).path
            daten = self._body()

            if pfad == "/api/config":
                speichere_config(z.config_pfad, daten)
                z.cfg = lade_config(z.config_pfad)
                return self._json({"ok": True})

            if pfad == "/api/ordner_waehlen":
                return self._json({"pfad": ordner_waehlen()})

            if pfad == "/api/entwurf":
                zeit = speichere_entwurf(z, daten["teil"], daten.get("daten"))
                return self._json({"ok": True, "zeit": zeit})

            if pfad == "/api/sichern":
                name = sichern(z, "manuell")
                return self._json({"ok": True, "name": name})

            if pfad == "/api/wiederherstellen":
                if z.auftrag:
                    return self._json({"ok": False, "fehler": "Bitte warten, Auftrag läuft."})
                if not wiederherstellen(z, daten.get("name", "")):
                    return self._json({"ok": False, "fehler": "Sicherung nicht gefunden"})
                z.cfg = lade_config(z.config_pfad)
                return self._json({"ok": z.starte("Wiederherstellen", z.neu_planen)})

            if pfad == "/api/komprimieren":
                from .komprimieren import quellordner_komprimieren

                def komprimieren():
                    ergebnis = quellordner_komprimieren(z.items, z.cfg)
                    print("Fertig:", ", ".join(f"{k}: {v:.0f}" if isinstance(v, float) else f"{k}: {v}"
                                               for k, v in ergebnis.items()))
                    print("Die Dateien haben sich geändert – lese neu ein …")
                    z.neu_planen()

                return self._json({"ok": z.starte("Quellordner komprimieren", komprimieren)})

            if pfad == "/api/diagnose":
                from .diagnose import diagnose

                return self._json({"ok": z.starte("Diagnose", lambda: diagnose(z.cfg))})

            if pfad == "/api/analysieren":
                ok = z.starte("Analyse", z.neu_planen)
                return self._json({"ok": ok})

            if pfad == "/api/ereignisse":
                eigene = daten.get("eigene", [])
                # Umbenannte/gelöschte Ereignisse auch in den manuellen Zuordnungen nachziehen
                umbenannt = {e["alt_name"]: e["name"] for e in eigene
                             if e.get("alt_name") and e["alt_name"] != e["name"]}
                behalten = {e["name"] for e in eigene}
                korrekturen = planung.lade_korrekturen(z.cfg)
                geaendert = False
                for k in korrekturen.values():
                    ziel = (k.get("ordner") or {}).get("ereignis")
                    if ziel in umbenannt:
                        k["ordner"]["ereignis"] = umbenannt[ziel]
                        geaendert = True
                    elif ziel and ziel not in behalten and daten.get("geloescht") and ziel in daten["geloescht"]:
                        k.pop("ordner")  # Gruppe gelöscht -> Bild wieder automatisch zuordnen
                        geaendert = True
                if geaendert:
                    planung.speichere_korrekturen(z.cfg, korrekturen)
                for e in eigene:
                    e.pop("alt_name", None)
                    e.pop("info", None)
                    for k in ("von", "bis"):
                        if e.get(k):
                            e[k] = dt.date.fromisoformat(e[k])
                        else:
                            e.pop(k, None)
                speichere_ereignisse(z.cfg["ereignisse_datei"], eigene)
                speichere_entwurf(z, "ereignisse", None)  # Entwurf ist jetzt übernommen
                ok = z.starte("Neu einteilen", z.neu_planen) if daten.get("neu_planen") else True
                return self._json({"ok": ok})

            if pfad == "/api/korrektur":
                if z.auftrag:
                    return self._json({"ok": False, "fehler": "Bitte warten, Auftrag läuft."})
                korrekturen = planung.lade_korrekturen(z.cfg)
                for eintrag in daten.get("aenderungen", []):
                    item = self._item(str(eintrag["id"]))
                    if not item:
                        continue
                    k = korrekturen.setdefault(item["pfad"], {})
                    for feld in ("status", "favorit"):
                        if feld in eintrag:
                            k[feld] = eintrag[feld]
                    if "themen" in eintrag:
                        k.setdefault("themen", {}).update(eintrag["themen"])
                    planung.wende_korrekturen_an([item], {item["pfad"]: k})
                planung.speichere_korrekturen(z.cfg, korrekturen)
                # Themen neu bestimmen (Haus-Ordner / Kopien können sich ändern)
                themen.bestimme_themen(z.items, z.cfg, korrekturen)
                themen.themen_ordner(z.items, z.cfg)
                z.zielpfade_neu()
                return self._json({"ok": True, "statistik": z.statistik,
                                   "rueckblick": rueckblick_vorschau(z)})

            if pfad == "/api/verschieben":
                if z.auftrag:
                    return self._json({"ok": False, "fehler": "Bitte warten, Auftrag läuft."})
                korrekturen = planung.lade_korrekturen(z.cfg)
                ziel = daten.get("ereignis") or None
                if ziel:
                    eigene = lade_ereignisse(z.cfg["ereignisse_datei"])
                    if ziel not in {e["name"] for e in eigene}:
                        bekannt = {i.get("ereignis") for i in z.items}
                        if ziel not in bekannt:  # wirklich neu -> als eigene Gruppe merken
                            eigene.append({"name": ziel, "manuell": True})
                            speichere_ereignisse(z.cfg["ereignisse_datei"], eigene)
                for idx in daten.get("ids", []):
                    item = self._item(str(idx))
                    if item:
                        korrekturen.setdefault(item["pfad"], {})["ordner"] = {"ereignis": ziel}
                planung.speichere_korrekturen(z.cfg, korrekturen)
                planung.wende_ordner_korrekturen_an(z.items, korrekturen, z.cfg, z.gruppen)
                themen.bestimme_themen(z.items, z.cfg, korrekturen)
                themen.themen_ordner(z.items, z.cfg)
                z.zielpfade_neu()
                return self._json({"ok": True})

            if pfad == "/api/personen" and "aenderungen" in daten:
                # Alle Änderungen der Personen-Seite auf einmal (Name, Rolle, aus-/einblenden)
                alle = {p["id"]: p for p in z.cfg.get("_personen", [])}
                for a in daten["aenderungen"]:
                    gruppe = alle.get(a.get("id"))
                    if not gruppe:
                        continue
                    if a.get("aktion") == "einblenden":
                        personen.vergessen(z.cfg, gruppe["id"])
                        if a.get("name") or a.get("rolle"):
                            personen.benenne(z.cfg, gruppe, a.get("name", ""), a.get("rolle", ""))
                    elif a.get("aktion") == "ausblenden":
                        personen.benenne(z.cfg, gruppe, "", "ignorieren")
                    else:
                        personen.benenne(z.cfg, gruppe, a.get("name", ""), a.get("rolle", ""))
                speichere_entwurf(z, "personen", None)
                return self._json({"ok": z.starte("Personen neu zuordnen", z.neu_planen)})

            if pfad == "/api/personen":
                gruppe = next((p for p in z.cfg.get("_personen", []) if p["id"] == daten.get("id")), None)
                if not gruppe:
                    return self._json({"ok": False, "fehler": "Gruppe nicht gefunden"})
                if daten.get("aktion") == "einblenden":
                    personen.vergessen(z.cfg, gruppe["id"])
                elif daten.get("aktion") == "ausblenden":
                    personen.benenne(z.cfg, gruppe, "", "ignorieren")
                else:
                    personen.benenne(z.cfg, gruppe, daten.get("name", ""), daten.get("rolle", ""))
                ok = z.starte("Personen neu zuordnen", z.neu_planen) if daten.get("neu_planen", True) else True
                return self._json({"ok": ok})

            if pfad == "/api/korrekturen_loeschen":
                planung.speichere_korrekturen(z.cfg, {})
                ok = z.starte("Neu einteilen", z.neu_planen)
                return self._json({"ok": ok})

            if pfad == "/api/sortieren":
                if not z.items:
                    return self._json({"ok": False, "fehler": "Erst analysieren."})

                def sortieren():
                    ergebnis = ablegen(z.items, z.cfg)
                    print("Fertig:", ", ".join(f"{k}: {v}" for k, v in ergebnis.items()))

                return self._json({"ok": z.starte("Ablegen", sortieren)})

            if pfad == "/api/pdf":
                jahr = int(daten.get("jahr") or dt.date.today().year)

                def pdf():
                    dateien = erstelle_rueckblicke(z.cfg, jahr)
                    z.pdfs = [str(d) for d in dateien] + [p for p in z.pdfs if p not in map(str, dateien)]

                return self._json({"ok": z.starte(f"Rückblick {jahr}", pdf)})

            self._json({"fehler": "unbekannt"}, 404)

    return Handler


def starte_ui(config_pfad: str = "config.yaml", port: int = 8765) -> None:
    import warnings

    z = Zustand(config_pfad)

    # Python-Warnungen (z.B. von Pillow) auch im Protokoll der Oberfläche zeigen
    def warnung_ins_protokoll(nachricht, kategorie, datei, zeile, file=None, line=None):
        text = f"Hinweis: {str(nachricht)[:200]}"
        if not z.log or z.log[-1] != text:
            z.log.append(text)
        print(text)
    warnings.showwarning = warnung_ins_protokoll
    # Meldet im Protokoll, wo das Programm steckt, wenn ein Schritt lange still ist
    fortschritt.starte_wachhund(lambda text: z.log.append(text))
    # Harte Abstürze in eine Datei schreiben; einen früheren Absturz anzeigen
    frueher = fortschritt.absturzprotokoll(z.cfg["_basis"])
    if frueher:
        z.log.append("Hinweis: Beim letzten Mal ist das Programm abgestürzt "
                     "(Details: fotosort_absturz.log):")
        z.log.extend("  " + zeile for zeile in frueher.splitlines()[:12])
    try:  # Stand beim Start sichern (falls man später zurück möchte)
        name = sichern(z, "start")
        if name:
            print(f"Zwischenstand gesichert: sicherungen/{name}")
    except OSError as e:
        print("Sicherung beim Start nicht möglich:", e)
    server = ThreadingHTTPServer(("127.0.0.1", port), baue_handler(z))
    adresse = f"http://127.0.0.1:{port}/"
    print(f"Oberfläche läuft auf {adresse}  (Beenden: Strg+C)")
    webbrowser.open(adresse)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")
