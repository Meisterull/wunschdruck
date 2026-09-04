#!/usr/bin/env python3
"""Loest die inherits-Ketten der Orca-Vendor-Profile auf.

Die Orca-CLI stuerzt ab, wenn man ihr die rohen Vendor-JSONs gibt (inherits
wird im CLI-Modus nicht aufgeloest). Dieses Werkzeug indexiert alle Profile
ueber ihr "name"-Feld und schreibt flache JSONs, die die CLI direkt laden kann.

Aufruf: profil_flatten.py <profiles-verzeichnis> <ausgabe-verzeichnis> <profilname> [...]
"""
import json
import sys
from pathlib import Path

# Schluessel, die die Orca-CLI (V2.3.6 / 01.10.01.50) im --load-settings-Pfad
# zum Segfault bringen — per Delta-Debugging ermittelt (proben/prozess_bisect.py).
# Ohne sie gilt der eingebaute Default.
CLI_GIFTIG = {"wipe_tower_filament"}


def profil_index(profildir: Path) -> dict[str, Path]:
    """Alle Profil-JSONs (Snapmaker + OrcaFilamentLibrary) nach "name" indexieren."""
    index: dict[str, Path] = {}
    for unterordner in ("Snapmaker", "OrcaFilamentLibrary"):
        for pfad in (profildir / unterordner).rglob("*.json"):
            try:
                daten = json.loads(pfad.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            name = daten.get("name")
            if isinstance(name, str) and name not in index:
                index[name] = pfad
    return index


def flach(name: str, index: dict[str, Path], besucht: tuple[str, ...] = ()) -> dict:
    """Profil mit aufgeloester Vererbung liefern (Kind ueberschreibt Eltern)."""
    if name in besucht:
        raise ValueError(f"Vererbungs-Zyklus: {' -> '.join(besucht)} -> {name}")
    if name not in index:
        raise KeyError(f"Profil nicht gefunden: {name!r}")
    daten = json.loads(index[name].read_text(encoding="utf-8"))
    eltern_name = daten.pop("inherits", None)
    if not eltern_name:
        return daten
    eltern = flach(eltern_name, index, besucht + (name,))
    eltern.update(daten)
    for giftig in CLI_GIFTIG:
        eltern.pop(giftig, None)
    return eltern


def main() -> None:
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    profildir, ausgabedir = Path(sys.argv[1]), Path(sys.argv[2])
    ausgabedir.mkdir(parents=True, exist_ok=True)
    index = profil_index(profildir)
    for name in sys.argv[3:]:
        ergebnis = flach(name, index)
        ziel = ausgabedir / f"{name}.json"
        ziel.write_text(json.dumps(ergebnis, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{name} -> {ziel} ({len(ergebnis)} Schluessel)")


if __name__ == "__main__":
    main()
