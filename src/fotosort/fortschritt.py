"""
Fortschrittsanzeige für lange Schritte (Einlesen, Inhaltserkennung, Ablegen ...).

Schreibt in regelmäßigen Abständen eine Zeile wie
    Einlesen: 1.200 / 10.000 (12 %) – 8,3 Bilder/s – noch ca. 18 min
in die Konsole bzw. das Protokoll der Oberfläche. Den aktuellen Stand liest
die Oberfläche zusätzlich über stand() für den Fortschrittsbalken.
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_aktuell: dict = {}


def _dauer(sekunden: float) -> str:
    if sekunden < 90:
        return f"{max(1, int(sekunden))} s"
    if sekunden < 5400:
        return f"{int(round(sekunden / 60))} min"
    return f"{sekunden / 3600:.1f} h".replace(".", ",")


def _zahl(n: int) -> str:
    return f"{n:,}".replace(",", ".")


class Fortschritt:
    """
    Verwendung:
        f = Fortschritt("Einlesen", gesamt=len(dateien), einheit="Bilder")
        for ...:
            f.schritt()
        f.fertig()
    Ausgabe höchstens alle `intervall` Sekunden (und bei 100 %).
    """

    def __init__(self, aufgabe: str, gesamt: int, einheit: str = "Dateien", intervall: float = 5.0):
        self.aufgabe, self.gesamt, self.einheit = aufgabe, max(gesamt, 0), einheit
        self.intervall = intervall
        self.start = time.time()
        self.letzte_ausgabe = time.time()  # erste Meldung nach `intervall` Sekunden
        self.erledigt = 0
        self._setze()
        if gesamt:
            print(f"{aufgabe}: {_zahl(gesamt)} {einheit} …")

    def _setze(self, rest: float | None = None):
        with _lock:
            _aktuell.clear()
            _aktuell.update({"aufgabe": self.aufgabe, "erledigt": self.erledigt,
                             "gesamt": self.gesamt, "rest_s": rest})

    def schritt(self, n: int = 1):
        self.erledigt += n
        jetzt = time.time()
        vergangen = jetzt - self.start
        rate = self.erledigt / vergangen if vergangen > 0 else 0
        rest = (self.gesamt - self.erledigt) / rate if rate > 0 else None
        self._setze(rest)  # für den Balken in der Oberfläche immer aktuell
        if jetzt - self.letzte_ausgabe >= self.intervall or self.erledigt >= self.gesamt:
            self.letzte_ausgabe = jetzt
            if self.erledigt < self.gesamt:
                prozent = 100 * self.erledigt / max(self.gesamt, 1)
                rate_text = f"{rate:.1f}".replace(".", ",")
                zeile = (f"  {self.aufgabe}: {_zahl(self.erledigt)} / {_zahl(self.gesamt)} "
                         f"({prozent:.0f} %) – {rate_text} {self.einheit}/s")
                if rest is not None and self.erledigt >= 3:
                    zeile += f" – noch ca. {_dauer(rest)}"
                print(zeile)

    def fertig(self, zusatz: str = ""):
        dauer = time.time() - self.start
        if self.gesamt:
            print(f"  {self.aufgabe} fertig: {_zahl(self.erledigt)} {self.einheit} in {_dauer(dauer)}"
                  + (f" – {zusatz}" if zusatz else ""))
        with _lock:
            _aktuell.clear()


def stand() -> dict:
    """Aktueller Stand für die Oberfläche (leer, wenn nichts läuft)."""
    with _lock:
        return dict(_aktuell)
