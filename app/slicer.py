"""Headless-Slicing ueber die Snapmaker-Orca-CLI.

Erkenntnisse aus den Proben (proben/):
- Vendor-Profile muessen flach sein (inherits loest die CLI nicht auf, sie
  stuerzt sonst ab) — werkzeuge/profil_flatten.py erledigt das.
- --export-3mf braucht einen Namen RELATIV zu --outputdir.
- Zeit/Verbrauch stehen als Kommentare im G-Code-Kopf.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

from werkzeuge.profil_flatten import flach, profil_index

from . import konfig

MASCHINE = "Snapmaker U1 (0.4 nozzle)"
QUALITAET_PROFIL = {
    "fein": "0.12 Fine @Snapmaker U1 (0.4 nozzle)",
    "standard": "0.20 Standard @Snapmaker U1 (0.4 nozzle)",
    "schnell": "0.24 Draft @Snapmaker U1 (0.4 nozzle)",
}
MATERIAL_PROFIL = {
    "PLA": "Generic PLA",
    "PETG": "Generic PETG",
    "TPU": "Generic TPU",
}

_index_cache: dict | None = None


def _profil_pfad(name: str) -> Path:
    """Flaches Profil aus dem Cache liefern, bei Bedarf frisch rechnen."""
    global _index_cache
    konfig.PROFIL_CACHE.mkdir(parents=True, exist_ok=True)
    ziel = konfig.PROFIL_CACHE / f"{name}.json"
    if not ziel.exists():
        if _index_cache is None:
            _index_cache = profil_index(konfig.PROFILDIR)
        ziel.write_text(json.dumps(flach(name, _index_cache), indent=2, ensure_ascii=False))
    return ziel


def _dauer_sekunden(text: str) -> int:
    """'1h 15m 30s' → Sekunden."""
    gesamt = 0
    for wert, einheit in re.findall(r"(\d+)\s*([dhms])", text):
        gesamt += int(wert) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[einheit]
    return gesamt


def gcode_metadaten(gcode: Path) -> dict:
    """Zeit und Verbrauch aus den Orca-Kommentaren ziehen — die stehen je nach
    Version im Kopf ODER im Fussteil, daher die ganze Datei scannen."""
    dauer_s, gewicht = None, None
    with gcode.open(encoding="utf-8", errors="replace") as f:
        for zeile in f:
            if not zeile.startswith(";"):
                continue
            if zeile.startswith("; estimated printing time (normal mode)"):
                dauer_s = _dauer_sekunden(zeile.split("=", 1)[1])
            elif zeile.startswith("; total filament used [g]"):
                gewicht = float(zeile.split("=", 1)[1])
            if dauer_s is not None and gewicht is not None:
                break
    return {"dauer_s": dauer_s, "filament_g": gewicht}


def slicen(quelle: Path, ziel_dir: Path, material: str, qualitaet: str,
           stuetzen: bool, slot: int, platte: int) -> dict:
    """Modell slicen; liefert {'gcode': Path, 'dauer_s': int, 'filament_g': float}.

    Wirft RuntimeError mit den letzten Logzeilen, wenn die CLI scheitert.
    """
    ziel_dir.mkdir(parents=True, exist_ok=True)
    maschine = _profil_pfad(MASCHINE)
    filament = _profil_pfad(MATERIAL_PROFIL[material])

    # Prozessprofil pro Job kopieren und die Stuetzen-Wahl hineinschreiben.
    prozess_basis = json.loads(_profil_pfad(QUALITAET_PROFIL[qualitaet]).read_text())
    prozess_basis["enable_support"] = "1" if stuetzen else "0"
    if stuetzen:
        prozess_basis.setdefault("support_type", "tree(auto)")
    prozess = ziel_dir / "prozess.json"
    prozess.write_text(json.dumps(prozess_basis))

    # Slot N drucken heisst: N Filament-Eintraege laden und dem Objekt die
    # Filament-ID N geben — die CLI zaehlt dabei ab 1.
    filamente = ";".join([str(filament)] * slot)
    kommando = [
        konfig.ORCA_APPRUN, "--debug", "1",
        "--slice", str(platte), "--arrange", "1", "--orient", "1",
        "--load-settings", f"{maschine};{prozess}",
        "--load-filaments", filamente,
        "--export-3mf", "projekt.gcode.3mf",
        "--outputdir", str(ziel_dir),
        str(quelle),
    ]
    if slot > 1:
        kommando[1:1] = ["--load-filament-ids", str(slot)]

    log = ziel_dir / "slice.log"
    with log.open("w") as logdatei:
        lauf = subprocess.run(kommando, stdout=logdatei, stderr=subprocess.STDOUT,
                              timeout=konfig.SLICE_TIMEOUT_S)
    gcodes = sorted(ziel_dir.glob("plate_*.gcode"))
    if lauf.returncode != 0 or not gcodes:
        letzte = "".join(log.read_text(errors="replace").splitlines(keepends=True)[-15:])
        raise RuntimeError(f"Slicer-Exit {lauf.returncode}, kein G-Code.\n{letzte}")
    gcode = gcodes[0]
    return {"gcode": gcode, **gcode_metadaten(gcode)}


def aufraeumen(job_dir: Path):
    shutil.rmtree(job_dir, ignore_errors=True)
