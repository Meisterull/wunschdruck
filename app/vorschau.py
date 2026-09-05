"""G-Code → kompakte 3D-Vorschau-Daten (JSON) für die Web-Ansicht.

Geparst werden nur Extrusions-Bewegungen (G1 mit wachsendem E), gruppiert in
Polylinien je Ebene und (Werkzeug, Klasse). Klassen: 'teil', 'stuetze'
(;TYPE:*upport*), 'rand' (Skirt/Brim/Purge/Custom). Punkte werden ausgedünnt,
damit auch große Drucke handy-tauglich bleiben.
"""
import json
import re
from pathlib import Path

MAX_PUNKTE = 400_000          # harte Obergrenze über alle Ebenen
MIN_ABSTAND2 = 0.15 ** 2      # Basis-Ausdünnung: Punkte < 0.15 mm werden verschmolzen

_RAND_TYPEN = ("skirt", "brim", "purge", "custom", "prime")


def _klasse(typ: str) -> str:
    t = typ.lower()
    if "support" in t:
        return "stuetze"
    if any(r in t for r in _RAND_TYPEN):
        return "rand"
    # Innenleben (Sparse/Internal/Bridge) separat — die Ansicht blendet es
    # standardmäßig aus, sonst wirkt das Gitter wie ein "Spinnennetz" im Modell.
    if "infill" in t or "bridge" in t:
        return "fuellung"
    return "teil"


def gcode_vorschau(gcode: Path) -> dict:
    """Liefert {bett, hoehe, werkzeuge, ebenen:[{z, spuren:[{t,k,p:[x,y,…]}]}]}."""
    n_g1 = sum(1 for z in gcode.open(encoding="utf-8", errors="replace")
               if z.startswith("G1"))
    # Ausdünnung an die Dateigröße koppeln (grob: halbe G1-Zahl = Punktzahl).
    min_abstand2 = MIN_ABSTAND2
    if n_g1 / 2 > MAX_PUNKTE:
        min_abstand2 = MIN_ABSTAND2 * (n_g1 / 2 / MAX_PUNKTE)

    x = y = z = 0.0
    e = 0.0
    e_relativ = False
    werkzeug = 0
    klasse = "rand"
    bett = [270.0, 270.0]
    hoehe = 0.0
    ebenen: list[dict] = []
    spur: list[float] | None = None
    spuren_der_ebene: dict[tuple[int, str], list[list[float]]] = {}
    werkzeuge: set[int] = {0}

    def ebene_abschliessen():
        nonlocal spuren_der_ebene, spur
        spur = None
        if spuren_der_ebene and ebenen:
            ebenen[-1]["spuren"] = [
                {"t": t, "k": k, "p": [rund for linie in linien for rund in linie]}
                for (t, k), linien in spuren_der_ebene.items()
            ]
        spuren_der_ebene = {}

    def punkt_anfuegen(nx: float, ny: float):
        nonlocal spur
        if spur is None:
            spur = [round(x, 2), round(y, 2)]
            spuren_der_ebene.setdefault((werkzeug, klasse), []).append(spur)
        dx, dy = nx - spur[-2], ny - spur[-1]
        if dx * dx + dy * dy >= min_abstand2:
            spur.extend((round(nx, 2), round(ny, 2)))

    with gcode.open(encoding="utf-8", errors="replace") as f:
        for zeile in f:
            if zeile.startswith(";"):
                if zeile.startswith(";TYPE:"):
                    klasse = _klasse(zeile[6:])
                    spur = None
                elif zeile.startswith(";Z:"):
                    try:
                        z_neu = float(zeile[3:])
                    except ValueError:
                        continue
                    ebene_abschliessen()
                    ebenen.append({"z": round(z_neu, 3), "spuren": []})
                    hoehe = max(hoehe, z_neu)
                elif zeile.startswith("; printable_area"):
                    ecken = re.findall(r"([\d.]+)x([\d.]+)", zeile)
                    if ecken:
                        bett = [max(float(a) for a, _ in ecken),
                                max(float(b) for _, b in ecken)]
                continue
            befehl = zeile.split(";", 1)[0].strip()
            if not befehl:
                continue
            wort = befehl.split(None, 1)[0]
            if wort in ("G1", "G0", "G2", "G3"):
                nx, ny, ne = x, y, None
                for teil in befehl.split()[1:]:
                    achse, wert = teil[0], teil[1:]
                    try:
                        w = float(wert)
                    except ValueError:
                        continue
                    if achse in ("X", "x"):
                        nx = w
                    elif achse in ("Y", "y"):
                        ny = w
                    elif achse in ("Z", "z"):
                        z = w
                    elif achse in ("E", "e"):
                        ne = w
            elif wort == "M83":
                e_relativ = True
                continue
            elif wort == "M82":
                e_relativ = False
                continue
            elif wort == "G92":
                m = re.search(r"E([-\d.]+)", befehl)
                if m:
                    e = float(m.group(1))
                continue
            elif re.fullmatch(r"T\d", wort):
                werkzeug = int(wort[1])
                werkzeuge.add(werkzeug)
                spur = None
                continue
            else:
                continue

            extrudiert = ne is not None and (ne > 0 if e_relativ else ne > e)
            if ne is not None and not e_relativ:
                e = ne
            if (nx != x or ny != y):
                if extrudiert and ebenen:
                    punkt_anfuegen(nx, ny)
                else:
                    spur = None  # Travel bricht die Polylinie
            x, y = nx, ny

    ebene_abschliessen()
    ebenen = [eb for eb in ebenen if eb["spuren"]]
    return {"bett": bett, "hoehe": round(hoehe, 2),
            "werkzeuge": sorted(werkzeuge), "ebenen": ebenen}


def vorschau_laden(gcode: Path) -> dict:
    """Vorschau aus dem Cache neben dem G-Code, bei Bedarf frisch gerechnet.
    (Suffix v2: Klassen-Schema mit 'fuellung' — alte Caches nicht wiederverwenden.)"""
    cache = gcode.with_suffix(".vorschau2.json")
    if cache.exists() and cache.stat().st_mtime >= gcode.stat().st_mtime:
        return json.loads(cache.read_text())
    daten = gcode_vorschau(gcode)
    cache.write_text(json.dumps(daten, separators=(",", ":")))
    return daten
