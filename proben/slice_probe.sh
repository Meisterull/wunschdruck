#!/bin/bash
# Slice-Probe: Testwuerfel headless mit U1-Profilen slicen (laeuft IM Container).
set -u
P="/work/profile"
rm -f /work/probe.log /work/*.gcode* 2>/dev/null

/opt/orca/AppRun --debug 4 --slice 0 --arrange 1 --orient 1 \
  --load-settings "$P/Snapmaker U1 (0.4 nozzle).json;$P/0.20 Standard @Snapmaker U1 (0.4 nozzle).json" \
  --load-filaments "$P/Generic PLA.json" \
  --export-3mf wuerfel.gcode.3mf \
  --outputdir /work /tests/wuerfel_20mm.stl >/work/probe.log 2>&1
echo "EXIT=$?"
ls -la /work/
