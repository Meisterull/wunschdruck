"""Headless-Slicing ueber die Snapmaker-Orca-CLI.

Erkenntnisse aus den Proben (proben/):
- Vendor-Profile muessen flach sein (inherits loest die CLI nicht auf, sie
  stuerzt sonst ab) — werkzeuge/profil_flatten.py erledigt das.
- --export-3mf braucht einen Namen RELATIV zu --outputdir.
- Zeit/Verbrauch stehen als Kommentare im G-Code-Kopf.
"""
import json
import logging
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

log = logging.getLogger("wunschdruck.slicer")

STANDARD_SLOT_FARBE = "#808080"  # leerer Slot / kein Hex hinterlegt
# G-Code-Konfigschluessel, an denen der U1 die Filamentfarbe abliest.
_FARB_SCHLUESSEL = rb"filament_colour|extruder_colour|filament_multi_colour"


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


def hex_farbe(wert: str | None) -> str:
    """'#rrggbb' / 'rrggbb' → '#RRGGBB'; alles andere → Standardgrau."""
    h = (wert or "").strip().lstrip("#")
    if len(h) == 6 and all(c in "0123456789abcdefABCDEF" for c in h):
        return "#" + h.upper()
    return STANDARD_SLOT_FARBE


def _slot_filament(material: str, slot_nr: int, farbe: str | None = None) -> Path:
    """Filamentprofil des Slots mit eindeutigem Namen (siehe Dedupe-Falle oben)
    und der Slot-Farbe aus den Einstellungen. Wird jedes Mal frisch
    geschrieben — Lager und Slots aendern sich."""
    konfig.PROFIL_CACHE.mkdir(parents=True, exist_ok=True)
    ziel = konfig.PROFIL_CACHE / f"slot{slot_nr}-{material}.json"
    basis = json.loads(_profil_pfad(
        MATERIAL_PROFIL.get(material, MATERIAL_PROFIL["PLA"])).read_text())
    basis["name"] = f"{basis.get('name', material)} Slot{slot_nr}"
    # Der U1 gleicht G-Code-Filamente ueber Typ + Farbe mit den Slots ab;
    # ohne Farbe im Profil landen Projekt-/Profil-Standardfarben im G-Code.
    basis["default_filament_colour"] = [hex_farbe(farbe)]
    basis["filament_colour"] = [hex_farbe(farbe)]
    ziel.write_text(json.dumps(basis, ensure_ascii=False))
    return ziel


def gcode_farben_setzen(gcode: Path, slot_farben: list[str]) -> bool:
    """Slot-Farben in den G-Code-Konfigblock schreiben (filament_colour & Co.,
    Eintrag i = Slot i; ueberzaehlige Eintraege bleiben stehen).

    Sicherheitsnetz zum Profil-Weg: der Fork schreibt bei STLs seinen
    Profil-Standard (#F2754E), ein Projekt-3MF behaelt seine Farbliste
    (Bambu-PVA-Slot = #00000000) — der U1 schlug deshalb fuer beide Farben
    eines Zweifarbdrucks Slot 1 vor (Befund 12.09.2026). True = geaendert."""
    farben = [hex_farbe(f).encode() for f in slot_farben[:4]]
    daten = gcode.read_bytes()

    def ersetzen(m: re.Match) -> bytes:
        alt = m.group(2).split(b";")
        neu = [farben[i] if i < len(farben) else alt[i] for i in range(len(alt))]
        return m.group(1) + b";".join(neu)

    neu_daten, _ = re.subn(rb"^(; (?:" + _FARB_SCHLUESSEL + rb") = )([^\r\n]*)",
                           ersetzen, daten, flags=re.M)
    if neu_daten == daten:
        return False
    tmp = gcode.with_suffix(gcode.suffix + ".tmp")
    tmp.write_bytes(neu_daten)
    tmp.replace(gcode)
    return True


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
    dauer_s, gewicht, verbrauch, laenge = None, None, None, None
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
            elif zeile.startswith("; filament used [mm]"):
                # Gesamtlaenge: Bezugsgroesse fuer den Abbruch-Anteil im Lager
                # (Moonraker-Historie liefert die extrudierte Laenge in mm).
                try:
                    laenge = sum(float(w) for w in zeile.split("=", 1)[1].split(","))
                except ValueError:
                    pass
            if None not in (dauer_s, gewicht, verbrauch, laenge):
                break
    return {"dauer_s": dauer_s, "filament_g": gewicht, "verbrauch": verbrauch,
            "filament_mm": laenge}


def _bett_typ() -> str:
    """default_bed_type des Maschinenprofils (U1: "Textured PEI Plate")."""
    try:
        return json.loads(_profil_pfad(MASCHINE).read_text()).get(
            "default_bed_type") or "Textured PEI Plate"
    except (OSError, ValueError):
        return "Textured PEI Plate"


def slicen(quelle: Path, ziel_dir: Path, material: str, qualitaet: str,
           stuetzen: bool, slot: int, platte: int,
           slot_materialien: list[str] | None = None,
           gruppen: list[tuple[Path, int]] | None = None,
           stuetz_slot: int = 0, ausrichtung: str = "auto",
           anzahl: int = 1, mehrfarb_projekt: bool = False,
           fuellung: int | None = None, stuetz_art: str = "profil",
           waende: int | None = None, deckschichten: int | None = None,
           bodenschichten: int | None = None, naht: str | None = None,
           slot_farben: list[str] | None = None,
           slot_im_modell: bool = False) -> dict:
    """Modell slicen; liefert {'gcode': Path, 'dauer_s': int, 'filament_g': float}.

    slot_materialien (Mehrfarb-Pfad): Materialien der 4 physischen Slots in
    Reihenfolge – es werden 4 Filament-Profile geladen; `material`/`slot` sind
    dann ohne Bedeutung.
    slot_farben: Hex-Farben der 4 Slots (Einstellungen) – landen im Profil
    und im G-Code-Konfigblock, damit der U1 die Slots automatisch zuordnet.
    gruppen (robuster Mehrfarb-Weg): [(stl, slot)] aus dreimf.zerlegen – die
    STLs liegen schon plattenzentriert, darum arrange/orient AUS und die
    Slot-Nummern gehen als --load-filament-ids mit.
    slot_im_modell: der Slot steht schon im 3MF (dreimf.slot_einbetten) – dann
    KEIN --load-filament-ids, das lehnt die CLI bei 3MFs ab.
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
    # Betttyp = Standard des U1-Profils (Textured PEI). Ohne diesen Eintrag nimmt
    # die CLI "Cool Plate" — dort hat PETG 0 °C und PLA 35 °C hinterlegt: Der
    # erste PETG-Druck löste sich und wurde Spaghetti (Befund 16.10.2026).
    prozess_basis["curr_bed_type"] = _bett_typ()
    prozess_basis["enable_support"] = "1" if stuetzen else "0"
    if fuellung is not None:
        prozess_basis["sparse_infill_density"] = f"{fuellung}%"
    if waende:
        prozess_basis["wall_loops"] = str(waende)
    if deckschichten is not None:
        prozess_basis["top_shell_layers"] = str(deckschichten)
    if bodenschichten is not None:
        prozess_basis["bottom_shell_layers"] = str(bodenschichten)
    if naht:
        prozess_basis["seam_position"] = naht
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
        farben = slot_farben or []
        filamente = ";".join(
            str(_slot_filament(m, i + 1, farben[i] if i < len(farben) else None))
            for i, m in enumerate(slot_materialien))
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
    if slot > 1 and not gruppen and not slot_im_modell:
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

    protokoll = ziel_dir / "slice.log"
    with protokoll.open("w") as logdatei:
        lauf = subprocess.run(kommando, stdout=logdatei, stderr=subprocess.STDOUT,
                              timeout=konfig.SLICE_TIMEOUT_S)
    gcodes = sorted(ziel_dir.glob("plate_*.gcode"))
    if lauf.returncode != 0 or not gcodes:
        letzte = "".join(protokoll.read_text(errors="replace").splitlines(keepends=True)[-15:])
        raise RuntimeError(f"Slicer-Exit {lauf.returncode}, kein G-Code.\n{letzte}")
    gcode = gcodes[0]
    if slot_farben and gcode_farben_setzen(gcode, slot_farben):
        # Der Profil-Weg hat nicht gereicht (Projektfarben/Fork-Standard) —
        # das Sicherheitsnetz hat die Farben nachgetragen.
        log.info("Slot-Farben im G-Code nachgetragen: %s", gcode.name)
    return {"gcode": gcode, **gcode_metadaten(gcode)}


def aufraeumen(job_dir: Path):
    shutil.rmtree(job_dir, ignore_errors=True)
