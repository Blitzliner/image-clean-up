"""
Diagnose: Prüft Schritt für Schritt, ob auf diesem Rechner alles funktioniert.

    uv run fotosort diagnose        (oder in der Oberfläche: Einstellungen -> "Diagnose")

Jede Prüfung läuft in einem eigenen Prozess. Stürzt eine Bibliothek ab (z.B.
onnxruntime mit Windows-Fehler 0xC0000005), wird das erkannt und benannt,
statt das ganze Programm zu beenden. Am Ende gibt es konkrete Empfehlungen.
Läuft alles durch, wird eine frühere Sperre der Inhaltserkennung aufgehoben.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path

# (Name, Python-Code). Der Code läuft jeweils in einem frischen Python-Prozess.
PRUEFUNGEN = [
    ("Grundbibliotheken (numpy, Pillow)",
     "import numpy, PIL; from PIL import Image; print('numpy', numpy.__version__, '| Pillow', PIL.__version__)"),
    ("OpenCV + Gesichtserkennung",
     "import cv2, numpy as np, sys; print('OpenCV', cv2.__version__)\n"
     "from fotosort import gesichter as g\n"
     "m = g.modelle_bereitstellen(sys.argv[1])\n"
     "print('Modelle:', {k: bool(v) for k, v in m.items()})\n"
     "print('Gesichter im Testbild:', len(g.finde_gesichter(np.zeros((200, 200, 3), np.uint8), m['erkennung'])))"),
    ("onnxruntime (Rechenkern der Inhaltserkennung)",
     "import onnxruntime as ort; print('onnxruntime', ort.__version__, '|', ort.get_device(), '|', ort.get_available_providers())"),
    ("fastembed laden",
     "import fastembed; print('fastembed', fastembed.__version__)"),
    ("CLIP-Textmodell laden (beim ersten Mal Download ~0,25 GB)",
     "import sys; from fastembed import TextEmbedding\n"
     "m = TextEmbedding('Qdrant/clip-ViT-B-32-text', cache_dir=sys.argv[1] + '/clip')\n"
     "v = list(m.embed(['a photo of a house']))[0]; print('Textvektor:', len(v), 'Werte')"),
    ("CLIP-Bildmodell laden + Testbild erkennen (beim ersten Mal ~0,35 GB)",
     "import sys; from PIL import Image; from fastembed import ImageEmbedding\n"
     "m = ImageEmbedding('Qdrant/clip-ViT-B-32-vision', cache_dir=sys.argv[1] + '/clip')\n"
     "v = list(m.embed([Image.new('RGB', (300, 200), (120, 160, 200))]))[0]; print('Bildvektor:', len(v), 'Werte')"),
]

WINDOWS_CODES = {
    3221225477: "Speicherzugriffsfehler (0xC0000005)",
    3221225501: "unbekannter Prozessorbefehl (0xC000001D) – Prozessor zu alt (kein AVX?)",
    3221226505: "interner Laufzeitfehler (0xC0000409)",
    3221225781: "fehlende DLL (0xC0000135)",
    3221225794: "DLL konnte nicht initialisiert werden (0xC0000142)",
}


def _cpu_info() -> str:
    teile = [platform.platform(), platform.machine(), platform.processor() or "?"]
    if sys.platform == "win32":
        try:
            import ctypes

            k = ctypes.windll.kernel32
            avx = bool(k.IsProcessorFeaturePresent(39))
            avx2 = bool(k.IsProcessorFeaturePresent(40))
            teile.append(f"AVX: {'ja' if avx else 'NEIN'}, AVX2: {'ja' if avx2 else 'NEIN'}")
        except Exception:
            pass
    elif Path("/proc/cpuinfo").exists():
        flags = Path("/proc/cpuinfo").read_text(errors="ignore")
        teile.append(f"AVX: {'ja' if ' avx ' in flags else 'NEIN'}, AVX2: {'ja' if 'avx2' in flags else 'NEIN'}")
    return " | ".join(teile)


def _vcruntime() -> str:
    if sys.platform != "win32":
        return "nicht nötig (kein Windows)"
    try:
        import ctypes

        system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        datei = system32 / "msvcp140.dll"
        if not datei.exists():
            return "FEHLT (msvcp140.dll nicht in System32)"
        groesse = ctypes.windll.version.GetFileVersionInfoSizeW(str(datei), None)
        puffer = ctypes.create_string_buffer(groesse)
        ctypes.windll.version.GetFileVersionInfoW(str(datei), 0, groesse, puffer)
        zeiger, laenge = ctypes.c_void_p(), ctypes.c_uint()
        ctypes.windll.version.VerQueryValueW(puffer, "\\", ctypes.byref(zeiger), ctypes.byref(laenge))
        info = ctypes.cast(zeiger, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
        version = f"{info[2] >> 16}.{info[2] & 0xFFFF}.{info[3] >> 16}"
        return f"vorhanden, Version {version}"
    except Exception as fehler:
        return f"vorhanden (Version nicht lesbar: {fehler})"


def diagnose(cfg: dict) -> bool:
    modelle = cfg["analyse"]["modell_ordner"]
    print("=== Diagnose ===")
    print(f"Python {sys.version.split()[0]} ({sys.executable})")
    print(f"System: {_cpu_info()}")
    print(f"Visual C++ Runtime: {_vcruntime()}")
    print(f"Projektordner: {cfg['_basis']}")
    umgebung = dict(os.environ, PYTHONIOENCODING="utf-8", HF_HUB_DISABLE_PROGRESS_BARS="1")
    alles_ok = True
    ergebnisse = []
    for nr, (name, code) in enumerate(PRUEFUNGEN, start=1):
        print(f"\n[{nr}/{len(PRUEFUNGEN)}] {name} …")
        start = time.time()
        try:
            r = subprocess.run([sys.executable, "-X", "faulthandler", "-c", code, modelle],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=umgebung, timeout=1800)
            code_ = r.returncode
            ausgabe = (r.stdout + r.stderr).strip().splitlines()
        except subprocess.TimeoutExpired:
            code_, ausgabe = -999, ["Zeitüberschreitung (30 min)"]
        dauer = time.time() - start
        if code_ == 0:
            for z in ausgabe[-3:]:
                print(f"    {z}")
            print(f"  OK ({dauer:.0f} s)")
            ergebnisse.append((name, "ok"))
            continue
        alles_ok = False
        if code_ == 1:
            letzte = next((z for z in reversed(ausgabe) if z.strip()), "")
            print(f"  FEHLER: {letzte[:300]}")
            ergebnisse.append((name, "fehler:" + letzte))
        else:
            grund = WINDOWS_CODES.get(code_, f"Exit-Code {code_}")
            print(f"  ABSTURZ: {grund}")
            stapel = [z for z in ausgabe if z.strip().startswith("File ")][:4]
            for z in stapel:
                print(f"    {z.strip()[:160]}")
            ergebnisse.append((name, "absturz:" + grund))
        # Folgeprüfungen bauen aufeinander auf -> abbrechen
        break

    print("\n=== Ergebnis ===")
    if alles_ok:
        sperre = Path(modelle) / "clip" / ".nicht_verfuegbar"
        if sperre.exists():
            sperre.unlink()
            print("Alles in Ordnung. Die Sperre der Inhaltserkennung wurde aufgehoben – "
                  "einfach neu analysieren.")
        else:
            print("Alles in Ordnung.")
        return True

    name, art = ergebnisse[-1]
    print(f"Problem bei: {name}")
    if name.startswith("onnxruntime") and art.startswith("absturz"):
        print("Empfehlungen (der Reihe nach probieren):")
        print("  1. Microsoft Visual C++ Redistributable (x64) installieren/reparieren UND den PC neu starten:")
        print("     https://aka.ms/vs/17/release/vc_redist.x64.exe")
        if "0xC000001D" in art:
            print("  -> Der Prozessor unterstützt die nötigen Befehle (AVX) nicht. Dann in den "
                  "Einstellungen 'Bildinhalte erkennen' auf 'nein' stellen.")
        print("  2. Ältere onnxruntime-Version probieren (im Projektordner):")
        print('''     uv add "onnxruntime==1.19.2; python_version < '3.13'"''')
        print("     danach erneut: uv run fotosort diagnose")
        print("  3. Klappt beides nicht: In den Einstellungen 'Bildinhalte erkennen' auf 'nein' stellen.")
        print("     Alles andere funktioniert; Haus/Kinder dann per Häkchen in 'Bilder prüfen'.")
    elif name.startswith("CLIP") and art.startswith("fehler"):
        print("Das Modell konnte vermutlich nicht heruntergeladen werden. Internet/Firewall/Proxy prüfen;")
        print("die Downloads kommen von huggingface.co. Danach erneut 'Diagnose' ausführen.")
    elif name.startswith("CLIP") and art.startswith("absturz"):
        print("Absturz beim Laden/Rechnen des Modells. Evtl. ist der Download beschädigt:")
        print(f"  Ordner {Path(modelle) / 'clip'} löschen und Diagnose erneut ausführen.")
        print('''  Hilft das nicht: uv add "onnxruntime==1.19.2; python_version < '3.13'" und erneut prüfen.''')
    elif name.startswith("fastembed"):
        print("Bibliothek fehlt oder ist beschädigt: im Projektordner 'uv sync' ausführen.")
    else:
        print("Bitte 'uv sync' im Projektordner ausführen und die Diagnose wiederholen.")
    return False
