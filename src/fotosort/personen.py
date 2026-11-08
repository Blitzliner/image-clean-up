"""
Personen erkennen: gleiche Gesichter über alle Fotos zu Personen gruppieren.

Ablauf:
  1. Jedes Gesicht hat einen Fingerabdruck (siehe gesichter.py).
  2. Gesichter, die einer bereits benannten Person ähneln, werden ihr zugeordnet.
  3. Die übrigen werden zu "unbekannten Personen" gruppiert.
  4. In der Oberfläche (Reiter "Personen") gibst du den Gruppen einen Namen
     und eine Rolle:  Kind | Familie | Freund | ignorieren
     Das wird in personen.json gespeichert und beim nächsten Lauf wiedererkannt.

Die Rollen steuern die Themen-Ordner:
  - "Kinder"       = Fotos, auf denen ein Kind (Rolle Kind) zu sehen ist
  - "Mit Freunden" = Fotos, auf denen ein Freund (Rolle Freund) zu sehen ist
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .gesichter import text_zu_vektor

PERSONEN_DATEI = "personen.json"
ROLLEN = ["Kind", "Familie", "Freund", "ignorieren"]

# Ähnlichkeitsschwellen (Kosinus, 1 = identisch). SFace empfiehlt ~0.36.
SCHWELLE_BEKANNT = 0.40  # Gesicht -> benannte Person
SCHWELLE_GRUPPE = 0.46  # Gesicht -> unbekannte Gruppe
SCHWELLE_VERSCHMELZEN = 0.52  # zwei unbekannte Gruppen sind dieselbe Person
MAX_VEKTOREN = 40  # so viele Beispiel-Fingerabdrücke werden je Person gespeichert


def _pfad(cfg: dict) -> Path:
    return Path(cfg["_basis"]) / PERSONEN_DATEI


def lade_personen(cfg: dict) -> list[dict]:
    p = _pfad(cfg)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("personen", [])
    except json.JSONDecodeError:
        return []


def speichere_personen(cfg: dict, personen: list[dict]) -> None:
    _pfad(cfg).write_text(json.dumps({"personen": personen}, ensure_ascii=False, indent=1),
                          encoding="utf-8")


def _norm(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)


def erkenne_personen(items: list[dict], cfg: dict) -> list[dict]:
    """
    Personen auf allen Fotos bestimmen. Setzt pro Bild:
      i["personen"] = Namen der erkannten (benannten) Personen
      i["rollen"]   = deren Rollen (z.B. ["Kind", "Familie"])
      i["person_ids"] = je Gesicht die Personen-/Gruppen-ID (oder "")
    Gibt die Personenliste für die Oberfläche zurück.
    """
    bekannte = lade_personen(cfg)
    # Gesichter mit Fingerabdruck einsammeln
    gesichter = []  # (item_index, gesicht_index, vektor, groesse)
    for idx, i in enumerate(items):
        i["personen"], i["rollen"] = [], []
        i["person_ids"] = [""] * len(i.get("gesichter", []))
        for g_idx, text in enumerate(i.get("gesichts_vektoren") or []):
            if text:
                gesichter.append((idx, g_idx, text_zu_vektor(text), i["gesichter"][g_idx][2]))
    if not gesichter:
        return []

    vektoren = _norm(np.stack([g[2] for g in gesichter]))
    zuordnung = [""] * len(gesichter)

    # 1) bekannte Personen: Mittelwert ihrer gespeicherten Fingerabdrücke
    if bekannte:
        zentren = _norm(np.stack([
            _norm(np.stack([text_zu_vektor(t) for t in p["vektoren"]])).mean(axis=0)
            for p in bekannte]))
        aehnlich = vektoren @ zentren.T
        beste = aehnlich.argmax(axis=1)
        for k in range(len(gesichter)):
            if aehnlich[k, beste[k]] >= SCHWELLE_BEKANNT:
                zuordnung[k] = bekannte[beste[k]]["id"]

    # 2) übrige Gesichter gruppieren (große, gut erkennbare Gesichter zuerst)
    offen = sorted((k for k in range(len(gesichter)) if not zuordnung[k]),
                   key=lambda k: -gesichter[k][3])
    gruppen: list[list[int]] = []
    summen: list[np.ndarray] = []
    for k in offen:
        if summen:
            z = _norm(np.stack(summen))
            s = z @ vektoren[k]
            j = int(s.argmax())
            if s[j] >= SCHWELLE_GRUPPE:
                gruppen[j].append(k)
                summen[j] = summen[j] + vektoren[k]
                continue
        gruppen.append([k])
        summen.append(vektoren[k].copy())

    # 3) sehr ähnliche Gruppen verschmelzen
    verschmolzen = True
    while verschmolzen and len(gruppen) > 1:
        verschmolzen = False
        z = _norm(np.stack(summen))
        s = z @ z.T
        np.fill_diagonal(s, -1)
        a, b = np.unravel_index(s.argmax(), s.shape)
        if s[a, b] >= SCHWELLE_VERSCHMELZEN:
            gruppen[a] += gruppen[b]
            summen[a] = summen[a] + summen[b]
            del gruppen[b], summen[b]
            verschmolzen = True

    # Gruppen-IDs vergeben: stabil über den Fingerabdruck des größten Gesichts
    gruppen.sort(key=lambda g: -len(g))
    for nr, g in enumerate(gruppen, start=1):
        for k in g:
            zuordnung[k] = f"u{nr}"

    # 4) Ergebnisse an die Bilder schreiben
    nach_id = {p["id"]: p for p in bekannte}
    for k, (idx, g_idx, _, _) in enumerate(gesichter):
        pid = zuordnung[k]
        items[idx]["person_ids"][g_idx] = pid
        p = nach_id.get(pid)
        if p and p.get("rolle") != "ignorieren":
            if p["name"] not in items[idx]["personen"]:
                items[idx]["personen"].append(p["name"])
            if p.get("rolle") and p["rolle"] not in items[idx]["rollen"]:
                items[idx]["rollen"].append(p["rolle"])

    # 5) Liste für die Oberfläche
    liste = []
    beispiele: dict[str, list] = {}
    for k, (idx, g_idx, _, groesse) in enumerate(gesichter):
        beispiele.setdefault(zuordnung[k], []).append((groesse, idx, g_idx, k))
    for pid, eintraege in beispiele.items():
        eintraege.sort(reverse=True)
        p = nach_id.get(pid)
        fotos = len({e[1] for e in eintraege})
        if not p and fotos < 2:  # Einzelgesichter nicht anzeigen
            continue
        liste.append({
            "id": pid, "name": p["name"] if p else "", "rolle": p.get("rolle", "") if p else "",
            "bekannt": bool(p), "fotos": fotos,
            "ausgeblendet": bool(p and p.get("rolle") == "ignorieren"),
            "beispiele": [[e[1], e[2]] for e in eintraege[:8]],
            "_faces": [e[3] for e in eintraege],
        })
    liste.sort(key=lambda x: (not x["bekannt"], -x["fotos"]))
    # Fingerabdrücke merken, damit neue Benennungen gespeichert werden können
    for eintrag in liste:
        eintrag["_vektoren"] = [vektoren[k] for k in eintrag.pop("_faces")]
    cfg["_personen"] = liste
    return liste


def _automatischer_name(personen: list[dict], rolle: str) -> str:
    """Name für eine Gruppe, der nur eine Rolle gegeben wurde (z.B. 'Freund 3')."""
    basis = {"ignorieren": "Ausgeblendet"}.get(rolle, rolle or "Person")
    vergeben = {p["name"] for p in personen}
    n = 1
    while f"{basis} {n}" in vergeben:
        n += 1
    return f"{basis} {n}"


def vergessen(cfg: dict, pid: str) -> None:
    """Gespeicherte Person entfernen (ihre Gesichter erscheinen wieder als unbekannt)."""
    speichere_personen(cfg, [p for p in lade_personen(cfg) if p["id"] != pid])


def benenne(cfg: dict, gruppe: dict, name: str, rolle: str) -> None:
    """
    Eine (unbekannte oder bekannte) Gruppe benennen. Gibt es den Namen schon,
    werden die Gruppen zusammengeführt (z.B. Kind als Baby und ein Jahr später).
    Ein Name ist nicht Pflicht: Nur mit Rolle bekommt die Gruppe einen
    automatischen Namen ("Freund 2"), mit Rolle "ignorieren" wird sie ausgeblendet.
    """
    from .gesichter import vektor_zu_text

    personen = lade_personen(cfg)
    name = name.strip()
    if not name and rolle:
        ziel_alt = next((p for p in personen if p["id"] == gruppe["id"]), None)
        name = ziel_alt["name"] if ziel_alt else _automatischer_name(personen, rolle)
    ziel = next((p for p in personen if p["id"] == gruppe["id"]), None)
    gleicher_name = next((p for p in personen if name and p["name"].lower() == name.lower()), None)
    neue = [vektor_zu_text(v) for v in gruppe.get("_vektoren", [])[:MAX_VEKTOREN]]

    if not name and ziel:  # Name gelöscht -> Person vergessen
        personen.remove(ziel)
    elif gleicher_name and gleicher_name is not ziel:
        gleicher_name["vektoren"] = (gleicher_name["vektoren"] + neue)[-MAX_VEKTOREN:]
        gleicher_name["rolle"] = rolle or gleicher_name.get("rolle", "")
        if ziel:
            personen.remove(ziel)
    elif ziel:
        ziel["name"], ziel["rolle"] = name, rolle
    elif name:
        nr = 1 + max((int(p["id"][1:]) for p in personen if p["id"][1:].isdigit()), default=0)
        personen.append({"id": f"p{nr}", "name": name, "rolle": rolle, "vektoren": neue})
    speichere_personen(cfg, personen)
