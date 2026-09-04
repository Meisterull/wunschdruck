#!/bin/bash
# Eingrenzung des CLI-Segfaults: welche Kombination der Settings-Dateien crasht?
set -u
P="/work/profile"
M="$P/Snapmaker U1 (0.4 nozzle).json"
PR="$P/0.20 Standard @Snapmaker U1 (0.4 nozzle).json"
F="$P/Generic PLA.json"

probe() {
  beschreibung="$1"; shift
  /opt/orca/AppRun --debug 1 "$@" /tests/wuerfel_20mm.stl >/tmp/p.log 2>&1
  echo "EXIT=$? $beschreibung"
}

probe "nur Maschine"                --slice 0 --load-settings "$M" --export-3mf /work/a.3mf --outputdir /work
probe "nur Prozess"                 --slice 0 --load-settings "$PR" --export-3mf /work/b.3mf --outputdir /work
probe "Prozess;Maschine (getauscht)" --slice 0 --load-settings "$PR;$M" --export-3mf /work/c.3mf --outputdir /work
probe "M;P ohne arrange/orient"     --slice 0 --load-settings "$M;$PR" --export-3mf /work/d.3mf --outputdir /work
probe "M;P mit Filament"            --slice 0 --load-settings "$M;$PR" --load-filaments "$F" --export-3mf /work/e.3mf --outputdir /work
xvfb-run -a /opt/orca/AppRun --debug 1 --slice 0 --load-settings "$M;$PR" --load-filaments "$F" --export-3mf /work/f.3mf --outputdir /work /tests/wuerfel_20mm.stl >/tmp/p.log 2>&1
echo "EXIT=$? M;P mit xvfb"
ls -la /work/*.3mf /work/*.gcode 2>/dev/null
