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
        aktivitaet()
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


# ---------------------------------------------------------------------------
# Schritte + Wachhund: zeigt an, woran gerade gearbeitet wird, und meldet
# automatisch die Code-Stelle, wenn ein Schritt lange keine Meldung gibt.
# ---------------------------------------------------------------------------
_schritt: dict = {}
_letzte_aktivitaet = [time.time()]


def aktivitaet() -> None:
    """Von Fortschritt aufgerufen: es passiert etwas."""
    _letzte_aktivitaet[0] = time.time()


class Schritt:
    """
    with Schritt(2, 7, "Bildinhalte erkennen"):
        ...
    gibt "Schritt 2/7: Bildinhalte erkennen …" und am Ende die Dauer aus.
    """

    def __init__(self, nr: int, gesamt: int, name: str):
        self.nr, self.gesamt, self.name = nr, gesamt, name

    def __enter__(self):
        self.start = time.time()
        with _lock:
            _schritt.update({"nr": self.nr, "gesamt": self.gesamt, "name": self.name,
                             "start": self.start, "thread": threading.get_ident()})
        aktivitaet()
        print(f"Schritt {self.nr}/{self.gesamt}: {self.name} …")
        return self

    def __exit__(self, *fehler):
        dauer = time.time() - self.start
        if dauer >= 2:
            print(f"  ({self.name}: {_dauer(dauer)})")
        with _lock:
            _schritt.clear()
        aktivitaet()
        return False


def aktueller_schritt() -> dict:
    with _lock:
        return {k: v for k, v in _schritt.items() if k != "thread"}


def _code_stelle(thread_id: int) -> str:
    """Wo steckt der Thread gerade? (letzte Zeilen im fotosort-Code + Bibliothek)"""
    import sys
    import traceback

    rahmen = sys._current_frames().get(thread_id)
    if rahmen is None:
        return "unbekannt"
    stapel = traceback.extract_stack(rahmen)
    eigene = [f for f in stapel if "fotosort" in f.filename.replace("\\", "/")]
    teile = []
    if eigene:
        f = eigene[-1]
        teile.append(f"{f.filename.replace(chr(92), '/').split('/')[-1]} Zeile {f.lineno} ({f.name})")
    if stapel and (not eigene or stapel[-1] is not eigene[-1]):
        f = stapel[-1]
        teile.append(f"in {f.filename.replace(chr(92), '/').split('/')[-1]}:{f.lineno} {f.name}")
    return " → ".join(teile)


_absturz_datei = []


def absturzprotokoll(ordner, name: str = "fotosort_absturz.log") -> str:
    """
    Bei einem harten Absturz (z.B. in einer C-Bibliothek) schreibt Python den
    Aufrufstapel in diese Datei. Gibt den Inhalt eines früheren Absturzes zurück.
    """
    import faulthandler
    from pathlib import Path

    pfad = Path(ordner) / name
    frueher = ""
    try:
        if pfad.exists():
            text = pfad.read_text(encoding="utf-8", errors="replace").strip()
            if "Fatal" in text or "exception" in text:
                frueher = text
        datei = open(pfad, "w", encoding="utf-8")
        _absturz_datei.append(datei)  # offen halten
        faulthandler.enable(datei, all_threads=True)
    except OSError:
        pass
    return frueher


def starte_wachhund(ausgabe=print, still_sekunden: int = 60) -> None:
    """
    Hintergrund-Thread: Läuft ein Schritt und kam länger als `still_sekunden`
    keine Fortschrittsmeldung, wird gemeldet, wo das Programm gerade steckt.
    """
    def lauf():
        gemeldet = 0.0
        while True:
            time.sleep(10)
            with _lock:
                s = dict(_schritt)
            if not s:
                continue
            still = time.time() - _letzte_aktivitaet[0]
            if still >= still_sekunden and time.time() - gemeldet >= still_sekunden:
                gemeldet = time.time()
                ausgabe(f"  … Schritt {s['nr']}/{s['gesamt']} „{s['name']}“ läuft seit "
                        f"{_dauer(time.time() - s['start'])}, aktuell in: {_code_stelle(s['thread'])}")

    threading.Thread(target=lauf, daemon=True, name="wachhund").start()
