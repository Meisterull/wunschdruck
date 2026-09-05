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

from werkzeuge.profil_flatten import CLI_GIFTIG, flach, profil_index

from . import konfig

# Aus dem Desktop-Orca exportierte Prozess-Profile ("Druckprofile") des Nutzers.
EIGENE_PROFILE = konfig.DATEN_DIR / "profile-eigene"

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


# Fork-eigene G-Code-Variablen, die Vanilla-Orca nicht kennt → Literalwert.
# (Gefunden per Iteration am echten MakerWorld-3MF; 0 = Normalmodus.)
VANILLA_FORK_VARS = {"chamber_cooling_mode": "0"}


def _vanilla_maschine() -> Path:
    """U1-Maschinenprofil für Vanilla-Orca: Fork-Variablen in den G-Code-
    Templates durch Literale ersetzt (sonst 'Not a variable name')."""
    ziel = konfig.PROFIL_CACHE / "vanilla-maschine.json"
    if ziel.exists():
        return ziel
    maschine = json.loads(_profil_pfad(MASCHINE).read_text())
    for feld, wert in list(maschine.items()):
        if feld.endswith("_gcode") and isinstance(wert, str):
            for var, literal in VANILLA_FORK_VARS.items():
                wert = re.sub(rf"\b{var}\b", literal, wert)
            maschine[feld] = wert
    ziel.write_text(json.dumps(maschine, ensure_ascii=False))
    return ziel


def eigene_profile() -> list[str]:
    if not EIGENE_PROFILE.exists():
        return []
    return sorted(p.stem for p in EIGENE_PROFILE.glob("*.json"))


def eigenes_profil_importieren(roh: bytes) -> str:
    """Exportiertes Orca-Prozessprofil (JSON) übernehmen: Vererbung gegen die
    Vendor-Profile auflösen, CLI-giftige Schlüssel entfernen, ablegen."""
    global _index_cache
    daten = json.loads(roh)
    if daten.get("type") != "process":
        raise ValueError("Kein Druckprofil: im JSON steht nicht type=process")
    name = re.sub(r"[^\w\-. ]", "_", str(daten.get("name") or "Eigenes Profil")).strip()[:60]
    eltern = daten.pop("inherits", None)
    if eltern:
        if _index_cache is None:
            _index_cache = profil_index(konfig.PROFILDIR)
        basis = flach(eltern, _index_cache)
        basis.update(daten)
        daten = basis
    for schluessel in CLI_GIFTIG:
        daten.pop(schluessel, None)
    EIGENE_PROFILE.mkdir(parents=True, exist_ok=True)
    (EIGENE_PROFILE / f"{name}.json").write_text(
        json.dumps(daten, indent=2, ensure_ascii=False), encoding="utf-8")
    return name


def _slot_filament(material: str, slot_nr: int) -> Path:
    """Filamentprofil des Slots mit eindeutigem Namen (siehe Dedupe-Falle oben)."""
    konfig.PROFIL_CACHE.mkdir(parents=True, exist_ok=True)
    ziel = konfig.PROFIL_CACHE / f"slot{slot_nr}-{material}.json"
    if not ziel.exists():
        basis = json.loads(_profil_pfad(
            MATERIAL_PROFIL.get(material, MATERIAL_PROFIL["PLA"])).read_text())
        basis["name"] = f"{basis.get('name', material)} Slot{slot_nr}"
        ziel.write_text(json.dumps(basis, ensure_ascii=False))
    return ziel


def _dauer_sekunden(text: str) -> int:
    """'1h 15m 30s' → Sekunden."""
    gesamt = 0
    for wert, einheit in re.findall(r"(\d+)\s*([dhms])", text):
        gesamt += int(wert) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[einheit]
    return gesamt


def gcode_metadaten(gcode: Path) -> dict:
    """Zeit und Verbrauch aus den Orca-Kommentaren ziehen — die stehen je nach
    Version im Kopf ODER im Fussteil, daher die ganze Datei scannen.
    verbrauch: Gramm je Filament-Slot (Komma-Liste im G-Code)."""
    dauer_s, gewicht, verbrauch = None, None, None
    with gcode.open(encoding="utf-8", errors="replace") as f:
        for zeile in f:
            if not zeile.startswith(";"):
                continue
            if zeile.startswith("; estimated printing time (normal mode)"):
                dauer_s = _dauer_sekunden(zeile.split("=", 1)[1])
            elif zeile.startswith("; total filament used [g]"):
                gewicht = float(zeile.split("=", 1)[1])
            elif zeile.startswith("; filament used [g]"):
                try:
                    verbrauch = [float(w) for w in zeile.split("=", 1)[1].split(",")]
                except ValueError:
                    pass
            if dauer_s is not None and gewicht is not None and verbrauch is not None:
                break
    return {"dauer_s": dauer_s, "filament_g": gewicht, "verbrauch": verbrauch}


def slicen(quelle: Path, ziel_dir: Path, material: str, qualitaet: str,
           stuetzen: bool, slot: int, platte: int,
           slot_materialien: list[str] | None = None,
           gruppen: list[tuple[Path, int]] | None = None,
           stuetz_slot: int = 0, ausrichtung: str = "auto",
           anzahl: int = 1, mehrfarb_projekt: bool = False,
           fuellung: int | None = None, stuetz_art: str = "profil") -> dict:
    """Modell slicen; liefert {'gcode': Path, 'dauer_s': int, 'filament_g': float}.

    slot_materialien (Mehrfarb-Pfad): Materialien der 4 physischen Slots in
    Reihenfolge – es werden 4 Filament-Profile geladen; `material`/`slot` sind
    dann ohne Bedeutung.
    gruppen (robuster Mehrfarb-Weg): [(stl, slot)] aus dreimf.zerlegen – die
    STLs liegen schon plattenzentriert, darum arrange/orient AUS und die
    Slot-Nummern gehen als --load-filament-ids mit.
    Wirft RuntimeError mit den letzten Logzeilen, wenn die CLI scheitert.
    """
    ziel_dir.mkdir(parents=True, exist_ok=True)
    # Mehrfarb-Projekte sliced Vanilla-Orca (Fork-CLI crasht daran), mit
    # angepasstem Maschinenprofil; alles andere der Snapmaker-Fork.
    appruf = konfig.ORCA_VANILLA if mehrfarb_projekt else konfig.ORCA_APPRUN
    maschine = _vanilla_maschine() if mehrfarb_projekt else _profil_pfad(MASCHINE)
    filament = _profil_pfad(MATERIAL_PROFIL[material])

    # Prozessprofil pro Job kopieren und die Stuetzen-Wahl hineinschreiben.
    if qualitaet.startswith("eigen:"):
        prozess_basis = json.loads((EIGENE_PROFILE / f"{qualitaet[6:]}.json").read_text())
    else:
        prozess_basis = json.loads(_profil_pfad(QUALITAET_PROFIL[qualitaet]).read_text())
    # Keine G2/G3-Bögen: ob die U1-Klipper-Firmware gcode_arcs kann, ist
    # unverifiziert (der Snapmaker-Fork schreibt selbst keine), und Vanilla
    # hätte Arc-Fitting sonst an (04.09.: Bögen wurden in der Vorschau zu
    # Sehnen quer durchs Modell — "Spinnennetz").
    prozess_basis["enable_arc_fitting"] = "0"
    prozess_basis["enable_support"] = "1" if stuetzen else "0"
    if fuellung is not None:
        prozess_basis["sparse_infill_density"] = f"{fuellung}%"
    if stuetzen and stuetz_art == "baum":
        prozess_basis["support_type"] = "tree(auto)"
    elif stuetzen and stuetz_art == "normal":
        prozess_basis["support_type"] = "normal(auto)"
    if stuetzen and stuetz_slot:
        # Stuetzen (Basis + Kontaktflaeche) aus einem eigenen Slot drucken.
        prozess_basis["support_filament"] = str(stuetz_slot)
        prozess_basis["support_interface_filament"] = str(stuetz_slot)
    prozess = ziel_dir / "prozess.json"
    prozess.write_text(json.dumps(prozess_basis))

    # Slot N drucken heisst: N Filament-Eintraege laden und dem Objekt die
    # Filament-ID N geben — die CLI zaehlt dabei ab 1.
    if slot_materialien:
        # Je Slot eine EINDEUTIG benannte Profilkopie: bei 4x identischem Namen
        # dedupliziert Orca, haelt den Drucker fuer einfarbig und schaltet den
        # (fuer den U1 empfohlenen) Prime-Tower still ab (Befund 05.09.).
        filamente = ";".join(
            str(_slot_filament(m, i + 1)) for i, m in enumerate(slot_materialien))
    else:
        filamente = ";".join([str(filament)] * slot)
    # "wie in Datei": Rotation unangetastet lassen (--orient 0), die Platzierung
    # auf der Platte übernimmt weiterhin --arrange. Mehrfarb-Projekte behalten
    # IMMER ihre Datei-Ausrichtung (Bemalung ist lageabhängig designt).
    orient = "1" if (ausrichtung == "auto" and not mehrfarb_projekt) else "0"
    kommando = [
        appruf, "--debug", "1", "--allow-newer-file",
        "--slice", str(platte), "--arrange", "1", "--orient", orient,
        "--load-settings", f"{maschine};{prozess}",
        "--load-filaments", filamente,
        "--export-3mf", "projekt.gcode.3mf",
        "--outputdir", str(ziel_dir),
        str(quelle),
    ]
    if slot > 1 and not gruppen:
        kommando[1:1] = ["--load-filament-ids", str(slot)]
    if anzahl > 1 and not gruppen:
        # Mehrere Exemplare; --arrange packt sie auf die Platte. Der Wert ist die
        # GESAMT-Stueckzahl (verifiziert: 3 -> 3 Wuerfel). --repetitions lehnt
        # der Platten-Modus ab ("can not set repetitions when slice all").
        # Im Gruppen-Pfad (eigene Platzierung) laegen Kopien uebereinander — tabu.
        kommando[1:1] = ["--clone-objects", str(anzahl)]

    if gruppen:
        # Eigene Platzierung aus dreimf.zerlegen — Arrange/Orient wuerden die
        # ausgerichteten Gruppen auseinanderreissen.
        kommando[kommando.index("--arrange") + 1] = "0"
        kommando[kommando.index("--orient") + 1] = "0"
        kommando[1:1] = ["--load-filament-ids", ",".join(str(s) for _, s in gruppen)]
        kommando = kommando[:-1] + [str(p) for p, _ in gruppen]

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
