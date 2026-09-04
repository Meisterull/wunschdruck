#!/usr/bin/env python3
"""Findet per Delta-Debugging die Schluessel im Prozessprofil, die die Orca-CLI crashen.

Laeuft IM Container. Strategie: erst Minimalprofil testen, dann Schluessel
in Haelften dazunehmen; crasht eine Menge, wird sie weiter halbiert.
"""
import json
import subprocess
import sys
from pathlib import Path

PROFIL = Path("/work/profile/0.20 Standard @Snapmaker U1 (0.4 nozzle).json")
MASCHINE = "/work/profile/Snapmaker U1 (0.4 nozzle).json"
TRIAL = Path("/tmp/trial_prozess.json")

voll = json.loads(PROFIL.read_text())
meta_keys = [k for k in ("type", "name", "from", "instantiation", "version") if k in voll]
rest = [k for k in voll if k not in meta_keys]


def crasht(keys: list[str]) -> bool:
    daten = {k: voll[k] for k in meta_keys + keys}
    TRIAL.write_text(json.dumps(daten))
    p = subprocess.run(
        ["/opt/orca/AppRun", "--debug", "0", "--slice", "0",
         "--load-settings", f"{MASCHINE};{TRIAL}",
         "--export-3mf", "/tmp/trial.3mf", "--outputdir", "/tmp",
         "/tests/wuerfel_20mm.stl"],
        capture_output=True, timeout=180)
    return p.returncode in (139, -11)


print(f"{len(rest)} Kandidaten-Schluessel; Minimalprofil crasht: {crasht([])}", flush=True)

# Halbierungssuche: finde EINEN crashenden Schluessel (Annahme: einzelner Ausloeser),
# sonst weiter unten linear.
menge = rest
if crasht(rest):
    while len(menge) > 1:
        h1, h2 = menge[: len(menge) // 2], menge[len(menge) // 2:]
        if crasht(h1):
            menge = h1
        elif crasht(h2):
            menge = h2
        else:
            print(f"Interaktion: keine Haelfte von {len(menge)} crasht allein — linear ab hier", flush=True)
            schuldige = []
            basis: list[str] = []
            for k in menge:
                if not crasht(basis + [k]):
                    basis.append(k)
                else:
                    schuldige.append(k)
                    print(f"  crasht mit: {k} = {json.dumps(voll[k])[:120]}", flush=True)
            print("Schuldige (Interaktion):", schuldige)
            sys.exit(0)
        print(f"  eingegrenzt auf {len(menge)}", flush=True)
    print(f"SCHULDIG: {menge[0]} = {json.dumps(voll[menge[0]])[:200]}")
    uebrige = [k for k in rest if k != menge[0]]
    print(f"Gegenprobe ohne diesen Schluessel: crasht={crasht(uebrige)}")
else:
    print("Volles Profil crasht ploetzlich nicht mehr?!")
