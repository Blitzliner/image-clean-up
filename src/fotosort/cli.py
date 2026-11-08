"""
Kommandozeile.

  uv run fotosort analysieren   -> nur anschauen, Vorschau-HTML erzeugen (ändert nichts)
  uv run fotosort sortieren     -> Dateien auf das NAS kopieren/verschieben
  uv run fotosort pdf           -> Jahresrückblick-PDF aus den Favoriten
  uv run fotosort alles         -> sortieren + pdf
  uv run fotosort ui            -> Oberfläche im Browser (alles per Klick)

Optionen:
  --config PFAD    andere Konfigurationsdatei (Standard: config.yaml)
  --interaktiv     erkannte Reisen benennen (wird in ereignisse.yaml gespeichert)
  --ja             ohne Rückfrage ausführen
  --jahr 2026      Jahr für das PDF (Standard: letztes Jahr mit Fotos)
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from pathlib import Path

from . import duplikate, ereignisse, inhalt, personen, planung, themen
from .ablage import ablegen
from .analyse import analysiere_alle
from .config import lade_config
from .jahresrueckblick import erstelle_rueckblicke
from .vorschau import erstelle_vorschau


def plan_erstellen(cfg: dict, interaktiv: bool):
    """Analysieren -> Duplikate/Serien -> Ereignisse -> Favoriten -> Zielpfade."""
    items = analysiere_alle(cfg)
    if not items:
        print("Keine Fotos gefunden. Stimmen die 'quellen' in config.yaml?")
        sys.exit(1)
    inhalt.erkenne_inhalte(items, cfg)  # CLIP (optional)
    statistik = duplikate.bereinigen(items, cfg)
    gruppen = ereignisse.ordne_zu(items, cfg, interaktiv=interaktiv)
    personen.erkenne_personen(items, cfg)
    korrekturen = planung.lade_korrekturen(cfg)
    verschoben = planung.wende_ordner_korrekturen_an(items, korrekturen, cfg, gruppen)
    if verschoben:
        statistik["manuell_verschoben"] = verschoben
    planung.wende_korrekturen_an(items, korrekturen)  # manuell aussortiert/behalten
    statistik.update({f"thema_{k}": v for k, v in
                      themen.bestimme_themen(items, cfg, korrekturen).items()})
    themen.themen_ordner(items, cfg)
    statistik["favoriten"] = planung.waehle_favoriten(items, cfg)
    korrigiert = planung.wende_korrekturen_an(items, korrekturen)  # manuelle Sterne
    if korrigiert:
        statistik["manuell_korrigiert"] = korrigiert
        statistik["favoriten"] = sum(1 for i in items if i["favorit"])
    # Haus-Ordner als Gruppe für Vorschau/Übersicht bekannt machen
    bekannt = {g["gruppe"] for g in gruppen}
    for i in items:
        if i.get("gruppen_art") == "haus" and i["gruppe"] not in bekannt:
            bekannt.add(i["gruppe"])
            gruppen.append({"gruppe": i["gruppe"], "name": i["gruppe"].replace("_", " ").replace("/", " · "),
                            "art": "haus"})
    planung.zielpfade(items, cfg)
    statistik["dateien"] = len(items)
    statistik["behalten"] = sum(1 for i in items if i["status"] == "behalten")
    return items, gruppen, statistik


def zusammenfassung(items: list[dict], gruppen: list[dict], statistik: dict) -> None:
    print("\n=== Übersicht ===")
    for k, v in statistik.items():
        print(f"  {k:22} {v}")
    print("\n  Ordner:")
    zaehler = Counter((i["jahr"], i["gruppe"]) for i in items)
    for (jahr, gruppe), n in sorted(zaehler.items()):
        fav = sum(1 for i in items if i["gruppe"] == gruppe and i["jahr"] == jahr and i["favorit"])
        aus = sum(1 for i in items if i["gruppe"] == gruppe and i["jahr"] == jahr
                  and i["status"] == "aussortiert")
        print(f"    {jahr}/{gruppe:45} {n:5} Dateien  {fav:3} Favoriten  {aus:4} aussortiert")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="fotosort", description="Fotos sortieren & Jahresrückblick")
    parser.add_argument("befehl", choices=["analysieren", "sortieren", "pdf", "alles", "ui"])
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--interaktiv", action="store_true", help="erkannte Reisen benennen")
    parser.add_argument("--ja", action="store_true", help="ohne Rückfrage ausführen")
    parser.add_argument("--jahr", type=int, help="Jahr für das PDF")
    parser.add_argument("--ausgabe", help="Pfad der PDF-Datei")
    parser.add_argument("--port", type=int, default=8765, help="Port für die Oberfläche")
    args = parser.parse_args(argv)

    if args.befehl == "ui":
        from .ui import starte_ui

        starte_ui(args.config, args.port)
        return

    cfg = lade_config(args.config)
    if not Path(args.config).exists():
        print(f"Hinweis: {args.config} nicht gefunden – es gelten die Standardwerte.")

    if args.befehl in ("analysieren", "sortieren", "alles"):
        if not cfg["quellen"]:
            print("Bitte in config.yaml unter 'quellen' deine Foto-Ordner eintragen.")
            sys.exit(1)
        if not cfg["ziel"]:
            print("Bitte in config.yaml unter 'ziel' den Zielordner (NAS) eintragen.")
            sys.exit(1)
        items, gruppen, statistik = plan_erstellen(cfg, args.interaktiv)
        zusammenfassung(items, gruppen, statistik)
        vorschau = erstelle_vorschau(items, gruppen, statistik,
                                     Path(cfg["_basis"]) / "vorschau")
        print(f"\nVorschau: {vorschau}")

        if args.befehl == "analysieren":
            print("\nEs wurde nichts verändert. Zum Ausführen: uv run fotosort sortieren")
            return
        aktion = "VERSCHIEBEN" if cfg["modus"] == "verschieben" else "kopieren"
        if not args.ja:
            antwort = input(f"\n{len(items)} Dateien nach {cfg['ziel']} {aktion}? [j/N] ")
            if antwort.strip().lower() not in ("j", "ja", "y", "yes"):
                print("Abgebrochen.")
                return
        print(f"\nLege Dateien ab ({aktion}) ...")
        ergebnis = ablegen(items, cfg)
        print("Fertig:", ", ".join(f"{k}: {v}" for k, v in ergebnis.items()))
        jahre = sorted({i["jahr"] for i in items})
    else:
        jahre = []

    if args.befehl in ("pdf", "alles"):
        if args.jahr:
            jahre_pdf = [args.jahr]
        elif args.befehl == "alles" and jahre:
            jahre_pdf = jahre
        else:
            vorhandene = sorted(int(p.name) for p in Path(cfg["ziel"]).glob("[12][0-9][0-9][0-9]")
                                if p.is_dir()) if Path(cfg["ziel"]).exists() else []
            jahre_pdf = vorhandene[-1:] or [dt.date.today().year]
        for jahr in jahre_pdf:
            print(f"\nErstelle Jahresrückblick {jahr} ...")
            erstelle_rueckblicke(cfg, jahr, args.ausgabe)


if __name__ == "__main__":
    main()
