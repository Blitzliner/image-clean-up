"""
Vorschau als HTML-Seite (vor dem eigentlichen Sortieren).

Zeigt alle Bilder chronologisch, gruppiert nach Ereignis/Monat:
  - grüner Rahmen + Stern = Favorit
  - grau/durchgestrichen  = wird aussortiert (mit Grund)
  - "Serie 12"            = gehört zu einer Serienaufnahme
So kannst du prüfen, ob dir die Einteilung gefällt, bevor etwas kopiert wird.
Öffnen: vorschau/vorschau.html im Browser.
"""
from __future__ import annotations

import csv
import datetime as dt
import html
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

from .ablage import SPALTEN, protokoll_zeile

QUELLEN_TEXT = {"eigene": "Ich", "mann": "Mann", "whatsapp": "WhatsApp",
                "screenshot": "Screenshot", "sonstige": "?"}


def _vorschaubild(i: dict, ordner: Path) -> str | None:
    if i["art"] == "video":
        return None
    name = f"{i.get('sha1', i['name'])[:16]}.jpg"
    ziel = ordner / name
    if not ziel.exists():
        try:
            im = Image.open(i["pfad"])
            im.draft("RGB", (400, 400))
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((260, 260))
            im.save(ziel, quality=75)
        except Exception:
            return None
    return f"thumbs/{name}"


def erstelle_vorschau(items: list[dict], gruppen: list[dict], statistik: dict,
                      ordner: str | Path) -> Path:
    ordner = Path(ordner)
    thumbs = ordner / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    print(f"Erstelle Vorschau in {ordner} ...")
    with ThreadPoolExecutor() as pool:
        bilder = list(pool.map(lambda i: _vorschaubild(i, thumbs), items))

    # Plan zusätzlich als CSV (für Excel)
    with open(ordner / "plan.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=SPALTEN, delimiter=";")
        w.writeheader()
        w.writerows(protokoll_zeile(i) for i in items)

    namen = {g["gruppe"]: g["name"] for g in gruppen}
    reihenfolge: dict[tuple, list] = {}
    for i, bild in zip(items, bilder):
        reihenfolge.setdefault((i["jahr"], i["gruppe"]), []).append((i, bild))

    teile = [f"""<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<title>Fotosortierung – Vorschau</title><style>
body{{font-family:system-ui,Segoe UI,sans-serif;margin:24px;background:#f6f5f2;color:#222}}
h1{{margin:0 0 4px}} .stat{{color:#555;margin-bottom:20px}}
h2{{margin:32px 0 4px;padding-top:12px;border-top:2px solid #ddd}}
.info{{color:#666;font-size:14px;margin-bottom:10px}}
.grid{{display:flex;flex-wrap:wrap;gap:8px}}
.k{{width:200px;background:#fff;border:3px solid #fff;border-radius:6px;overflow:hidden;font-size:12px;box-shadow:0 1px 3px #0002}}
.k img,.k .vid{{width:200px;height:150px;object-fit:cover;display:block;background:#ccc}}
.k .vid{{display:flex;align-items:center;justify-content:center;font-size:28px;color:#555}}
.k .t{{padding:4px 6px}} .fav{{border-color:#2e9e4f}} .fav .t b:first-child::before{{content:"★ ";color:#e0a800}}
.aus{{opacity:.45}} .aus .g{{color:#b00020}} .badge{{display:inline-block;padding:0 5px;border-radius:3px;background:#eee;margin-right:3px}}
.whatsapp .badge.q{{background:#d6f5dd}} .mann .badge.q{{background:#dbe7ff}}
</style></head><body><h1>Vorschau der Fotosortierung</h1>
<div class="stat">{html.escape(', '.join(f'{k}: {v}' for k, v in statistik.items()))}</div>"""]

    # Chronologisch nach Ordnername, Screenshots ans Ende des Jahres
    sortiert = sorted(reihenfolge.items(), key=lambda kv: (kv[0][0], not kv[0][1][:4].isdigit(), kv[0][1]))
    for (jahr, gruppe), eintraege in sortiert:
        n_fav = sum(1 for i, _ in eintraege if i.get("favorit"))
        n_aus = sum(1 for i, _ in eintraege if i["status"] == "aussortiert")
        titel = namen.get(gruppe, gruppe)
        teile.append(f"<h2>{jahr} / {html.escape(gruppe)}</h2><div class='info'>"
                     f"{html.escape(titel)} · {len(eintraege)} Dateien · {n_fav} Favoriten · "
                     f"{n_aus} aussortiert</div><div class='grid'>")
        for i, bild in eintraege:
            klassen = ["k", i["quelle"]]
            if i.get("favorit"):
                klassen.append("fav")
            if i["status"] == "aussortiert":
                klassen.append("aus")
            zeit = dt.datetime.fromisoformat(i["zeit"])
            bild_html = (f"<img loading='lazy' src='{bild}'>" if bild
                         else "<div class='vid'>▶</div>")
            serie = f"<span class='badge'>Serie {i['serie']}</span>" if i.get("serie") else ""
            unsicher = " ~" if i.get("zeit_unsicher") else ""
            teile.append(
                f"<div class='{' '.join(klassen)}' title='{html.escape(i['pfad'])}'>{bild_html}"
                f"<div class='t'><b>{zeit:%d.%m. %H:%M}{unsicher}</b> "
                f"<span class='badge q'>{QUELLEN_TEXT.get(i['quelle'], i['quelle'])}</span>{serie}"
                f" Note {i.get('score') or '–'}<br>{html.escape(i['name'][:32])}"
                f"<div class='g'>{html.escape(i.get('grund', ''))}</div></div></div>")
        teile.append("</div>")
    teile.append("</body></html>")
    ziel = ordner / "vorschau.html"
    ziel.write_text("".join(teile), encoding="utf-8")
    return ziel
