"""
Ereignisse (Urlaube, Ausflüge, Feiern) erkennen und Bilder zuordnen.

Drei Wege, wie ein Ereignis entsteht:
  1. Du trägst es in ereignisse.yaml mit Datum ein ("von"/"bis").
     Optional mit Ort (lat/lon/radius_km) und/oder Schlüsselwörtern.
  2. Du trägst es OHNE Datum ein, nur mit Ort oder Schlüsselwort
     (z.B. die Boulderhalle). Dann wird jeder Besuch zu einem EIGENEN Ordner,
     sobald zwischen zwei Besuchen Tage liegen – Frühjahr und Herbst werden
     also nie vermischt.
  3. Automatisch: Tage, an denen die GPS-Fotos (Pixel) weit weg von zu Hause
     entstanden sind, werden zu Reisen zusammengefasst (ab reise.min_fotos
     Dateien automatisch ein Ordner, darunter nur ein Vorschlag).
  4. Vorschläge: Zeiträume mit auffällig vielen Fotos – auch ohne GPS.
     Mit --interaktiv (oder in der Oberfläche) entscheidest du, was ein
     eigener Ordner wird; das wird in ereignisse.yaml gespeichert.

Bilder ohne GPS (eigenes Handy, WhatsApp) werden über das Datum zugeordnet.
Zusätzlich "leihen" sie sich den Ort vom zeitlich nächsten GPS-Foto
(max. 3 Stunden Abstand) – praktisch, wenn nur ein Handy GPS speichert.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from collections import Counter
from pathlib import Path

import yaml

MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def entfernung_km(lat1, lon1, lat2, lon2) -> float:
    """Luftlinie zwischen zwei GPS-Punkten (Haversine-Formel)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def sicherer_name(text: str) -> str:
    """Text als Ordnernamen nutzbar machen (Windows/NAS-tauglich)."""
    text = re.sub(r'[<>:"/\\|?*]', "", text).strip()
    return re.sub(r"\s+", "_", text)


def _zeit(i: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(i["zeit"])


def _datum(wert) -> dt.date | None:
    if wert is None or isinstance(wert, dt.date):
        return wert
    return dt.date.fromisoformat(str(wert))


_geocoder_cache: dict = {}


def ortsname(lat: float, lon: float) -> str:
    """Offline-Ortsname zu GPS-Koordinaten (Paket reverse_geocoder)."""
    schluessel = (round(lat, 2), round(lon, 2))
    if schluessel in _geocoder_cache:
        return _geocoder_cache[schluessel]
    try:
        import reverse_geocoder as rg

        treffer = rg.search([schluessel], mode=1, verbose=False)[0]
        name = treffer["name"]
        if treffer.get("cc") and treffer["cc"] != "DE":
            name += f" ({treffer['cc']})"
    except Exception:
        name = f"{lat:.2f},{lon:.2f}"
    _geocoder_cache[schluessel] = name
    return name


# ---------------------------------------------------------------------------
# ereignisse.yaml lesen/schreiben
# ---------------------------------------------------------------------------
def lade_ereignisse(pfad: str | Path) -> list[dict]:
    pfad = Path(pfad)
    if not pfad.exists():
        return []
    with open(pfad, encoding="utf-8") as f:
        daten = yaml.safe_load(f) or {}
    ereignisse = daten.get("ereignisse") or []
    for e in ereignisse:
        e["von"] = _datum(e.get("von"))
        e["bis"] = _datum(e.get("bis")) or e["von"]
    return ereignisse


def speichere_ereignisse(pfad: str | Path, ereignisse: list[dict]) -> None:
    kopf = (
        "# Deine Ereignisse. Du kannst diese Datei frei bearbeiten.\n"
        "# Felder: name, von, bis (JJJJ-MM-TT), optional:\n"
        "#   ort: {lat: 48.4, lon: 2.6, radius_km: 30}\n"
        "#   schluesselwoerter: [Bouldern, Kletterhalle]   (im Datei-/Ordnernamen oder Ort)\n"
        "#   ignorieren: true   (automatisch erkannte Reise NICHT als Ordner anlegen –\n"
        "#                       die Fotos landen dann im Alltag)\n"
        "#   im_rueckblick: false  (eigener Ordner ja, aber nicht im Jahresrückblick)\n"
        "# Ohne von/bis gilt ein Ereignis für jeden Besuch einzeln (z.B. Boulderhalle).\n\n"
    )
    sauber = []
    for e in ereignisse:
        s = {k: v for k, v in e.items() if not k.startswith("_") and v not in (None, [], "")}
        for k in ("von", "bis"):
            if isinstance(s.get(k), dt.date):
                s[k] = s[k].isoformat()
        sauber.append(s)
    with open(pfad, "w", encoding="utf-8") as f:
        f.write(kopf)
        yaml.safe_dump({"ereignisse": sauber}, f, allow_unicode=True, sort_keys=False)


# ---------------------------------------------------------------------------
# Orte schätzen, Zuhause bestimmen, Reisen finden
# ---------------------------------------------------------------------------
def orte_schaetzen(items: list[dict], max_stunden: float = 3) -> None:
    """Bildern ohne GPS den Ort des zeitlich nächsten GPS-Bildes geben."""
    mit_gps = sorted((i for i in items if i.get("lat") is not None), key=_zeit)
    if not mit_gps:
        return
    import bisect

    zeiten = [_zeit(i) for i in mit_gps]
    grenze = dt.timedelta(hours=max_stunden)
    for i in items:
        if i.get("lat") is not None or i.get("zeit_unsicher"):
            continue
        t = _zeit(i)
        k = bisect.bisect_left(zeiten, t)
        kandidaten = [mit_gps[j] for j in (k - 1, k) if 0 <= j < len(mit_gps)]
        bester = min(kandidaten, key=lambda g: abs(_zeit(g) - t), default=None)
        if bester and abs(_zeit(bester) - t) <= grenze:
            i["ort_lat"], i["ort_lon"] = bester["lat"], bester["lon"]


def _ort(i: dict) -> tuple[float, float] | None:
    if i.get("lat") is not None:
        return i["lat"], i["lon"]
    if i.get("ort_lat") is not None:
        return i["ort_lat"], i["ort_lon"]
    return None


def bestimme_zuhause(items: list[dict], cfg: dict) -> tuple[float, float] | None:
    """Wohnort: aus config, sonst der Ort, an dem an den meisten Tagen fotografiert wurde."""
    z = cfg.get("zuhause")
    if isinstance(z, dict) and "lat" in z:
        return float(z["lat"]), float(z["lon"])
    tage_pro_zelle: dict[tuple, set] = {}
    punkte: dict[tuple, list] = {}
    for i in items:
        if i.get("lat") is None:
            continue
        zelle = (round(i["lat"] * 10), round(i["lon"] * 10))  # ca. 10-km-Raster
        tage_pro_zelle.setdefault(zelle, set()).add(i["zeit"][:10])
        punkte.setdefault(zelle, []).append((i["lat"], i["lon"]))
    if not tage_pro_zelle:
        return None
    beste = max(tage_pro_zelle, key=lambda z: len(tage_pro_zelle[z]))
    pts = punkte[beste]
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def finde_reisen(items: list[dict], zuhause, cfg: dict, min_anzahl: int) -> list[dict]:
    """Zusammenhängende Tage "weit weg von zu Hause" finden (per GPS)."""
    if not zuhause:
        return []
    rc = cfg["reise"]
    weg_pro_tag: dict[dt.date, list[bool]] = {}
    orte_pro_tag: dict[dt.date, list[tuple]] = {}
    for i in items:
        if i.get("lat") is None:
            continue
        tag = _zeit(i).date()
        weit = entfernung_km(i["lat"], i["lon"], *zuhause) > rc["min_entfernung_km"]
        weg_pro_tag.setdefault(tag, []).append(weit)
        if weit:
            orte_pro_tag.setdefault(tag, []).append((i["lat"], i["lon"]))

    # Ein Tag gilt als "unterwegs", wenn mind. die Hälfte der GPS-Fotos weit weg ist
    weg_tage = sorted(t for t, w in weg_pro_tag.items() if sum(w) >= len(w) / 2)
    zuhause_tage = {t for t, w in weg_pro_tag.items() if sum(w) < len(w) / 2}

    reisen: list[list[dt.date]] = []
    for tag in weg_tage:
        if reisen:
            letzter = reisen[-1][-1]
            luecke = (tag - letzter).days - 1
            dazwischen_daheim = any(
                letzter + dt.timedelta(days=k) in zuhause_tage for k in range(1, luecke + 1)
            )
            if luecke <= rc["max_luecke_tage"] and not dazwischen_daheim:
                reisen[-1].append(tag)
                continue
        reisen.append([tag])

    ergebnis = []
    for tage in reisen:
        von, bis = tage[0], tage[-1]
        anzahl = sum(1 for i in items if von <= _zeit(i).date() <= bis)
        if anzahl < min_anzahl:
            continue
        # Häufigster Ortsname der Reise
        punkte = [p for t in tage for p in orte_pro_tag.get(t, [])]
        stichprobe = punkte[:: max(1, len(punkte) // 50)]  # max. ~50 Orte nachschlagen
        namen = Counter(ortsname(*p) for p in stichprobe)
        ort = namen.most_common(1)[0][0] if namen else "unterwegs"
        art = "Reise" if (bis - von).days >= 1 else "Ausflug"
        ergebnis.append({"name": f"{art} {ort}", "von": von, "bis": bis,
                         "_auto": True, "_anzahl": anzahl, "_ort": ort, "_quelle": "GPS"})
    return ergebnis


def finde_fotoreiche_zeiten(items: list[dict], cfg: dict, belegt: set,
                            heimatort: str = "") -> list[dict]:
    """
    Zeiträume mit auffällig vielen Fotos finden – auch ganz ohne GPS
    (z.B. Urlaub, bei dem nur dein eigenes Handy fotografiert hat, oder ein
    Geburtstag). Das sind nur VORSCHLÄGE: ein Ordner entsteht erst, wenn du
    sie übernimmst.

    "Auffällig viel" = mind. reise.dichte_min_pro_tag Fotos und mind. doppelt
    so viele wie an einem normalen Foto-Tag (Median).
    belegt = Tage, die schon zu einem Ereignis/einer Reise gehören.
    """
    rc = cfg["reise"]
    pro_tag = Counter(_zeit(i).date() for i in items if i["art"] != "screenshot")
    if not pro_tag:
        return []
    werte = sorted(pro_tag.values())
    median = werte[len(werte) // 2]
    schwelle = max(rc["dichte_min_pro_tag"], 2 * median)
    aktive = {t for t, n in pro_tag.items() if n >= schwelle and t not in belegt}
    # Randtage mitnehmen: direkt neben einem fotoreichen Tag reicht die halbe
    # Schwelle (An-/Abreisetag, ruhigerer Urlaubstag ...)
    for _ in range(3):
        rand = {t for t, n in pro_tag.items()
                if t not in aktive and t not in belegt and n >= schwelle / 2
                and (t - dt.timedelta(days=1) in aktive or t + dt.timedelta(days=1) in aktive)}
        if not rand:
            break
        aktive |= rand
    aktive = sorted(aktive)

    gruppen: list[list[dt.date]] = []
    for tag in aktive:
        if gruppen:
            letzter = gruppen[-1][-1]
            luecke = [letzter + dt.timedelta(days=k) for k in range(1, (tag - letzter).days)]
            if len(luecke) <= rc["max_luecke_tage"] and not any(t in belegt for t in luecke):
                gruppen[-1].append(tag)
                continue
        gruppen.append([tag])

    ergebnis = []
    for tage in gruppen:
        von, bis = tage[0], tage[-1]
        anzahl = sum(n for t, n in pro_tag.items() if von <= t <= bis)
        if anzahl < rc["min_vorschlag"]:
            continue
        # Ortsname, falls Bilder (geschätzte) GPS-Daten haben
        orte = Counter(ortsname(*_ort(i)) for i in items
                       if von <= _zeit(i).date() <= bis and _ort(i))
        ort = orte.most_common(1)[0][0] if orte else ""
        if ort == heimatort:
            ort = ""  # zu Hause: Ortsname im Vorschlag weglassen
        if von == bis:
            name = f"Ereignis {von:%d.%m.}" + (f" {ort}" if ort else "")
        else:
            name = f"Ereignis {von:%d.%m.}–{bis:%d.%m.}" + (f" {ort}" if ort else "")
        ergebnis.append({"name": name, "von": von, "bis": bis, "_anzahl": anzahl,
                         "_ort": ort or "zu Hause / unbekannt", "_quelle": "viele Fotos"})
    return ergebnis


# ---------------------------------------------------------------------------
# Zuordnung
# ---------------------------------------------------------------------------
def _passt(i: dict, e: dict) -> bool:
    """Gehört Bild i zu Ereignis e? (Datum, Ort und Schlüsselwörter prüfen)"""
    tag = _zeit(i).date()
    if e.get("von") and not (e["von"] <= tag <= e["bis"]):
        return False
    ort = e.get("ort")
    if ort:
        p = _ort(i)
        if p is None:
            # Ohne Ortsinfo: nur wenn das Ereignis ein Datum hat, sonst unklar
            if not e.get("von"):
                return False
        elif entfernung_km(*p, ort["lat"], ort["lon"]) > ort.get("radius_km", 5):
            return False
    woerter = e.get("schluesselwoerter")
    if woerter:
        text = i["pfad"].lower()
        p = _ort(i)
        if p:
            text += " " + ortsname(*p).lower()
        if not any(w.lower() in text for w in woerter):
            return False
    return True


def _monatsgruppe(t: dt.datetime) -> str:
    return f"{t:%Y-%m}_{MONATE[t.month - 1]}"


def ordne_zu(items: list[dict], cfg: dict, interaktiv: bool = False) -> list[dict]:
    """
    Jedem Bild "gruppe" (Ordnername) und "jahr" geben.
    Gibt die Liste der verwendeten Ereignisse zurück (für Vorschau/PDF).
    """
    orte_schaetzen(items)
    datei = cfg["ereignisse_datei"]
    eigene = lade_ereignisse(datei)
    zuhause = bestimme_zuhause(items, cfg)
    if zuhause:
        print(f"Zuhause: {zuhause[0]:.3f}, {zuhause[1]:.3f} ({ortsname(*zuhause)})")

    def _ueberschneidet(r, liste):
        return any(e.get("von") and not (r["bis"] < e["von"] or r["von"] > e["bis"]) for e in liste)

    # GPS-Reisen, die nicht schon in ereignisse.yaml stehen.
    # Ab reise.min_fotos Dateien wird der Ordner automatisch angelegt,
    # kleinere Reisen erscheinen nur als Vorschlag.
    auto, vorschlaege = [], []
    for r in finde_reisen(items, zuhause, cfg, min_anzahl=cfg["reise"]["min_vorschlag"]):
        if _ueberschneidet(r, eigene):
            continue
        (auto if r["_anzahl"] >= cfg["reise"]["min_fotos"] else vorschlaege).append(r)

    # Fotoreiche Zeiträume (auch ohne GPS) als weitere Vorschläge
    belegt = set()
    for e in eigene + auto + vorschlaege:
        if e.get("von"):
            belegt.update(e["von"] + dt.timedelta(days=k) for k in range((e["bis"] - e["von"]).days + 1))
    vorschlaege += finde_fotoreiche_zeiten(items, cfg, belegt,
                                          ortsname(*zuhause) if zuhause else "")
    vorschlaege.sort(key=lambda r: r["von"])

    if interaktiv and (auto or vorschlaege):
        print("\nErkannte Reisen und Vorschläge:")
        for r in sorted(auto + vorschlaege, key=lambda r: r["von"]):
            ist_auto = r in auto
            print(f"\n  {r['von']:%d.%m.%Y} – {r['bis']:%d.%m.%Y}  ({r['_anzahl']} Dateien, "
                  f"{r['_quelle']}, Ort: {r['_ort']})")
            standard = r["name"] if ist_auto else "-"
            antwort = input(f"  Name [{standard}] ('-' = kein eigener Ordner): ").strip() or standard
            if antwort == "-":
                r["ignorieren"] = True
            else:
                r["name"] = antwort
            eigene.append(r)
        speichere_ereignisse(datei, eigene)
        print(f"\nGespeichert in {datei} – dort kannst du Namen/Daten jederzeit ändern.")
        auto, vorschlaege = [], []

    # Für die Oberfläche merken, was erkannt/vorgeschlagen wurde
    cfg["_auto_reisen"] = [
        {"name": r["name"], "von": r["von"].isoformat(), "bis": r["bis"].isoformat(),
         "anzahl": r["_anzahl"], "ort": r["_ort"], "quelle": r["_quelle"],
         "automatisch": r in auto} for r in sorted(auto + vorschlaege, key=lambda r: r["von"])
    ]
    cfg["_zuhause"] = list(zuhause) if zuhause else None

    aktive = [e for e in eigene + auto if not e.get("ignorieren")]
    mit_datum = [e for e in aktive if e.get("von")]
    ohne_datum = [e for e in aktive if not e.get("von")]

    # 1) Ereignisse mit Datum
    for i in items:
        i["ereignis"] = None
        i["im_rueckblick"] = True
        if i["art"] == "screenshot":
            continue
        for e in mit_datum:
            if _passt(i, e):
                i["ereignis"] = e["name"]
                i["_ereignis_start"] = e["von"]
                i["im_rueckblick"] = e.get("im_rueckblick", True)
                break

    # 2) Ereignisse ohne Datum (wiederkehrend): jeder Besuch eigener Ordner
    for e in ohne_datum:
        treffer = sorted((i for i in items if i["ereignis"] is None
                          and i["art"] != "screenshot" and _passt(i, e)), key=_zeit)
        besuch_start = None
        letzter_tag = None
        for i in treffer:
            tag = _zeit(i).date()
            if letzter_tag is None or (tag - letzter_tag).days > cfg["reise"]["max_luecke_tage"] + 1:
                besuch_start = tag
            i["ereignis"] = e["name"]
            i["_ereignis_start"] = besuch_start
            i["im_rueckblick"] = e.get("im_rueckblick", True)
            letzter_tag = tag

    # 3) Ordnernamen festlegen
    verwendet: dict[str, dict] = {}
    for i in items:
        t = _zeit(i)
        if i["art"] == "screenshot":
            i["jahr"] = t.year
            i["gruppe"] = cfg["ordner"]["screenshots"]
            i["gruppen_art"] = "screenshots"
        elif i["ereignis"]:
            start = i["_ereignis_start"]
            i["jahr"] = start.year
            i["gruppe"] = f"{start:%Y-%m}_{sicherer_name(i['ereignis'])}"
            i["gruppen_art"] = "ereignis"
            verwendet.setdefault(i["gruppe"], {"name": i["ereignis"], "art": "ereignis"})
        elif cfg["ordner"]["alltag"] == "monat":
            i["jahr"] = t.year
            i["gruppe"] = _monatsgruppe(t)
            i["gruppen_art"] = "alltag"
            verwendet.setdefault(i["gruppe"], {"name": f"{MONATE[t.month - 1]} {t.year}",
                                               "art": "alltag"})
        else:
            # Standard: alles ohne Ereignis in EINEN Ordner pro Jahr
            i["jahr"] = t.year
            i["gruppe"] = sicherer_name(cfg["ordner"]["alltag_name"])
            i["gruppen_art"] = "alltag"
            verwendet.setdefault(i["gruppe"], {"name": f"{cfg['ordner']['alltag_name']} {t.year}",
                                               "art": "alltag"})
        i.pop("_ereignis_start", None)
    return [dict(gruppe=g, **v) for g, v in sorted(verwendet.items())]
