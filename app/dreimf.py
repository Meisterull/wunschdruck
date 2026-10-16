"""3MF-Farbanalyse und Slot-Ummappung für Mehrfarb-Drucke.

Bambu-/Orca-Projekt-3MFs tragen die Farbinformation an zwei Stellen:
- Metadata/model_settings.config: <metadata key="extruder" value="N"/> je
  Objekt/Teil (Ganzteil-Zuweisung)
- 3D/3dmodel.model: paint_color="…" je Dreieck — der Bitstrom von
  PrusaSlicers TriangleSelector (siehe _code_lesen): ganz bemalte Dreiecke
  sind ein Blatt ("4"=Filament 1, "8"=2, "0C"=3, "1C"=4 …), fein gepinselte
  ein Teilungsbaum. Beides wird hier vollständig dekodiert und neu kodiert;
  nur unlesbare Codes erzwingen die feste 1:1-Zuordnung.

Die Werkzeugnummer im G-Code ist fest an den Filament-Index gekoppelt
(Filament i → T(i-1) → physischer Slot i). "Farbe → anderer Slot" heißt
darum: Indizes im 3MF umschreiben, nicht Filamentliste umsortieren.
"""
import json
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from werkzeuge.profil_flatten import CLI_GIFTIG

BETT_MITTE = (135.0, 135.0)  # Snapmaker U1: 270×270-Platte

_PAINT_RE = re.compile(r'paint_color="([0-9A-Fa-f]+)"')
_EXTRUDER_RE = re.compile(r'key="extruder"\s+value="(\d+)"')


# ---------------------------------------------------------------------------
# Paint-Codes: Bitstrom des TriangleSelector (PrusaSlicer/Orca/Bambu)
#
# Je Dreieck ein Hex-String; der 3MF-Leser liest ihn RÜCKWÄRTS (letzte Ziffer
# zuerst), jede Ziffer liefert 4 Bits, niederwertigstes zuerst. Knoten:
#   2 Bit Anzahl geteilter Seiten; 0 = Blatt, dann 2 Bit Zustand, wobei
#   Zustand 3 "erweitert" heißt: 4 weitere Bits, Zustand = 3 + Wert.
#   Sonst 2 Bit Sonderseite und (Seiten+1) Kind-Knoten rekursiv.
# Zustand n>0 = Filament n. Darum: "4" = Filament 1, "8" = 2, "0C" = 3,
# "1C" = 4 — ein naives Filament*4 ("10" für Slot 4) dekodiert der Slicer als
# Zustand 0 = unbemalt, und die Farbe fällt lautlos weg (Befund 12.09.2026).
# ---------------------------------------------------------------------------

def _bits_aus_code(code: str) -> list[int]:
    bits: list[int] = []
    for ziffer in reversed(code):
        wert = int(ziffer, 16)
        bits.extend((wert >> i) & 1 for i in range(4))
    return bits


def _code_aus_bits(bits: list[int]) -> str:
    bits = bits + [0] * (-len(bits) % 4)
    nibbles = [sum(bits[k + i] << i for i in range(4)) for k in range(0, len(bits), 4)]
    return "".join(f"{n:X}" for n in reversed(nibbles))


def _knoten_lesen(bits: list[int], pos: int):
    """Liefert (Knoten, nächste Position). Blatt = int (Zustand),
    Teilung = (sonderseite, [Kinder]). IndexError bei kaputtem Strom."""
    seiten = bits[pos] | (bits[pos + 1] << 1)
    pos += 2
    if seiten:
        sonder = bits[pos] | (bits[pos + 1] << 1)
        pos += 2
        kinder = []
        for _ in range(seiten + 1):
            kind, pos = _knoten_lesen(bits, pos)
            kinder.append(kind)
        return (sonder, kinder), pos
    zustand = bits[pos] | (bits[pos + 1] << 1)
    pos += 2
    if zustand == 3:
        zustand = 3 + sum(bits[pos + i] << i for i in range(4))
        pos += 4
    return zustand, pos


def _knoten_schreiben(knoten, bits: list[int]) -> None:
    if isinstance(knoten, tuple):
        sonder, kinder = knoten
        seiten = len(kinder) - 1
        bits.extend((seiten & 1, seiten >> 1, sonder & 1, sonder >> 1))
        for kind in kinder:
            _knoten_schreiben(kind, bits)
        return
    bits.extend((0, 0))
    if knoten >= 3:
        rest = knoten - 3
        bits.extend((1, 1, rest & 1, (rest >> 1) & 1, (rest >> 2) & 1, (rest >> 3) & 1))
    else:
        bits.extend((knoten & 1, knoten >> 1))


def _code_lesen(code: str):
    """Paint-Code → Baum, None wenn unlesbar."""
    try:
        knoten, _ = _knoten_lesen(_bits_aus_code(code), 0)
    except (IndexError, ValueError):
        return None
    return knoten


def _code_schreiben(knoten) -> str:
    bits: list[int] = []
    _knoten_schreiben(knoten, bits)
    return _code_aus_bits(bits)


def _zustaende(knoten, ziel: set[int]) -> None:
    if isinstance(knoten, tuple):
        for kind in knoten[1]:
            _zustaende(kind, ziel)
    elif knoten > 0:
        ziel.add(knoten)


def _ummappen_baum(knoten, zuordnung: dict[int, int]):
    if isinstance(knoten, tuple):
        return (knoten[0], [_ummappen_baum(k, zuordnung) for k in knoten[1]])
    return zuordnung.get(knoten, knoten) if knoten > 0 else knoten


def _uniform_index(code: str) -> int | None:
    """Ganz bemaltes Dreieck → Filament-Index (1-basiert), sonst None."""
    knoten = _code_lesen(code)
    return knoten if isinstance(knoten, int) and knoten > 0 else None


def filamente_im_code(code: str) -> set[int] | None:
    """Alle Filament-Indizes eines Paint-Codes (auch Teilungsbäume); None = unlesbar."""
    knoten = _code_lesen(code)
    if knoten is None:
        return None
    benutzt: set[int] = set()
    _zustaende(knoten, benutzt)
    return benutzt


def code_ummappen(code: str, zuordnung: dict[int, int]) -> str:
    """Paint-Code mit umgemappten Filament-Indizes; unlesbare Codes unverändert."""
    knoten = _code_lesen(code)
    if knoten is None:
        return code
    return _code_schreiben(_ummappen_baum(knoten, zuordnung))


def analysiere(pfad: Path) -> dict:
    """Liefert {"farben": [{"nr": i, "hex": "#rrggbb"}...], "fein": bool}.

    farben enthält alle vom Modell benutzten Filament-Indizes (leer bzw. ein
    Eintrag = einfarbig). Nicht-Projekt-3MFs und STLs → {"farben": [], "fein": False}.
    """
    ergebnis = {"farben": [], "fein": False}
    if pfad.suffix.lower() != ".3mf":
        return ergebnis
    try:
        with zipfile.ZipFile(pfad) as z:
            namen = set(z.namelist())
            benutzt: set[int] = set()
            fein = False

            if "Metadata/model_settings.config" in namen:
                text = z.read("Metadata/model_settings.config").decode("utf-8", "replace")
                benutzt.update(int(w) for w in _EXTRUDER_RE.findall(text) if int(w) > 0)

            codes: set[str] = set()
            # Bemalung kann in 3D/3dmodel.model ODER in 3D/Objects/*.model liegen
            for modellname in (n for n in namen if n.endswith(".model")):
                with z.open(modellname) as f:
                    rest = ""
                    while True:
                        stueck = f.read(4 << 20).decode("utf-8", "replace")
                        if not stueck:
                            break
                        codes.update(_PAINT_RE.findall(rest + stueck))
                        rest = stueck[-2000:]  # lange Baum-Codes können Chunk-Grenzen kreuzen
            for code in codes:
                indizes = filamente_im_code(code)
                if indizes is None:
                    fein = True  # unlesbar → Zuordnung bleibt sicherheitshalber 1:1
                else:
                    benutzt.update(indizes)

            farbliste = []
            if "Metadata/project_settings.config" in namen:
                try:
                    projekt = json.loads(z.read("Metadata/project_settings.config"))
                    farbliste = projekt.get("filament_colour") or []
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

            if benutzt or fein:
                benutzt.add(1)  # unbemalte Flächen drucken mit Filament 1
            if fein:
                # Unlesbare Codes: genaue Indizes unbekannt → alle Projekt-Filamente zeigen.
                benutzt.update(range(1, max(len(farbliste), max(benutzt, default=1)) + 1))

            ergebnis["fein"] = fein
            ergebnis["farben"] = [
                {"nr": i, "hex": (farbliste[i - 1] if i <= len(farbliste) else "#9e9e9e")}
                for i in sorted(benutzt)
            ]
    except (zipfile.BadZipFile, KeyError, ValueError):
        return {"farben": [], "fein": False}
    return ergebnis


def ummappen(quelle: Path, ziel: Path, zuordnung: dict[int, int]) -> Path:
    """Schreibt ein 3MF mit umgemappten Filament-Indizes (Modell-Nr → Slot) und
    entgiftet dabei IMMER die eingebetteten Projekt-Settings (CLI_GIFTIG-Keys
    wie wipe_tower_filament crashen die CLI auch aus dem 3MF heraus — darum
    laufen auch einfarbige 3MFs mit leerer Zuordnung hier durch).

    Ganzteil-Zuweisungen und Paint-Codes (auch fein gepinselte Teilungsbäume)
    werden umgeschrieben; unlesbare Codes bleiben unverändert — der Aufrufer
    erzwingt in dem Fall Identität.
    """

    def paint_ersetzen(m: re.Match) -> str:
        neu = code_ummappen(m.group(1), zuordnung)
        return m.group(0) if neu == m.group(1) else f'paint_color="{neu}"'

    def extruder_ersetzen(m: re.Match) -> str:
        slot = zuordnung.get(int(m.group(1)))
        return f'key="extruder" value="{slot}"' if slot else m.group(0)

    with zipfile.ZipFile(quelle) as alt, zipfile.ZipFile(
            ziel, "w", zipfile.ZIP_DEFLATED) as neu:
        for eintrag in alt.infolist():
            daten = alt.read(eintrag.filename)
            if eintrag.filename.endswith(".model"):
                text = daten.decode("utf-8", "replace")
                text = _PAINT_RE.sub(paint_ersetzen, text)
                daten = text.encode("utf-8")
            elif eintrag.filename == "Metadata/model_settings.config":
                text = daten.decode("utf-8", "replace")
                text = _EXTRUDER_RE.sub(extruder_ersetzen, text)
                daten = text.encode("utf-8")
            elif eintrag.filename == "Metadata/project_settings.config":
                try:
                    projekt = json.loads(daten)
                    for schluessel in CLI_GIFTIG:
                        projekt.pop(schluessel, None)
                    # Werte, an denen die CLI-Validierung scheitert (Bambu
                    # erlaubt -1 = auto, Orca-CLI verlangt >= 0).
                    exp = projekt.get("raft_first_layer_expansion")
                    if exp is not None and float(str(exp).replace("%", "") or 0) < 0:
                        projekt["raft_first_layer_expansion"] = "0"
                    daten = json.dumps(projekt, ensure_ascii=False).encode("utf-8")
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                    pass
            neu.writestr(eintrag, daten)
    return ziel


# ---------------------------------------------------------------------------
# Einfarbige 3MFs auf Slot > 1 / mit Stückzahl > 1
#
# Die Orca-CLI lehnt 3MFs ab, sobald --load-filament-ids oder --clone-objects
# gesetzt sind ("can not load 3mf when set loaded_filament_ids or clone_objects",
# Befund 16.10.2026). Darum: Slot direkt ins 3MF schreiben (Projekt-3MF mit
# model_settings.config), sonst Geometrie als STL herauslösen.
# ---------------------------------------------------------------------------

_OBJEKT_RE = re.compile(r'(<object\s+id="[^"]*"\s*>)(.*?)(</object>)', re.S)


def _hat_orca_config(quelle: Path) -> bool:
    with zipfile.ZipFile(quelle) as z:
        return "Metadata/model_settings.config" in z.namelist()


def _plattenzahl(quelle: Path) -> int:
    with zipfile.ZipFile(quelle) as z:
        if "Metadata/model_settings.config" not in z.namelist():
            return 1
        text = z.read("Metadata/model_settings.config").decode("utf-8", "replace")
    return max(1, len(re.findall(r"<plate\b", text)))


def _hat_sonderteile(quelle: Path) -> bool:
    """Modifier-, Negativ- oder Stützen-Teile – die würden beim Herauslösen als STL
    zu echter Geometrie."""
    with zipfile.ZipFile(quelle) as z:
        if "Metadata/model_settings.config" not in z.namelist():
            return False
        text = z.read("Metadata/model_settings.config").decode("utf-8", "replace")
    return any(t != "normal_part" for t in re.findall(r'subtype="([^"]+)"', text))


def slot_einbetten(quelle: Path, ziel: Path, slot: int) -> Path:
    """Einfarbiges Projekt-3MF komplett auf Filament `slot` stellen: Teil-/Objekt-
    Extruder und Paint-Codes umschreiben, fehlende Objekt-Extruder ergänzen
    (Orca nähme sonst Filament 1). Entgiftet wie ummappen()."""
    ummappen(quelle, ziel, {i: slot for i in range(1, 17)})
    with zipfile.ZipFile(ziel) as alt:
        eintraege = [(e, alt.read(e.filename)) for e in alt.infolist()]

    def ergaenzen(m: re.Match) -> str:
        kopf_bis_part = m.group(2).split("<part", 1)[0]
        if 'key="extruder"' in kopf_bis_part:
            return m.group(0)
        return f'{m.group(1)}<metadata key="extruder" value="{slot}"/>{m.group(2)}{m.group(3)}'

    with zipfile.ZipFile(ziel, "w", zipfile.ZIP_DEFLATED) as neu:
        for eintrag, daten in eintraege:
            if eintrag.filename == "Metadata/model_settings.config":
                daten = _OBJEKT_RE.sub(ergaenzen, daten.decode("utf-8", "replace")).encode("utf-8")
            neu.writestr(eintrag, daten)
    return ziel


def einfarbig_vorbereiten(quelle: Path, ordner: Path, slot: int,
                          anzahl: int) -> tuple[Path, bool]:
    """Einfarbiges 3MF slicefertig machen. Liefert (pfad, slot_eingebettet):
    slot_eingebettet=True heißt, der Slot steht schon im 3MF → der Slicer darf
    KEIN --load-filament-ids setzen. Bei Stückzahl > 1 oder 3MF ohne Orca-
    Metadaten wird ein STL daraus (darauf greifen beide CLI-Optionen)."""
    if anzahl > 1 or (slot > 1 and not _hat_orca_config(quelle)):
        if _plattenzahl(quelle) > 1 or _hat_sonderteile(quelle):
            raise ValueError("Stückzahl > 1 geht bei diesem 3MF nicht (mehrere Platten oder "
                             "Modifier-/Negativteile) – bitte im Slicer vervielfältigen oder Stückzahl 1 wählen.")
        stl = ordner / "einfarbig.stl"
        dreiecke = [tri for pfad, _ in zerlegen(quelle, ordner / "einfarbig", {})
                    for tri in _stl_lesen(pfad)]
        _stl_schreiben(stl, dreiecke)
        return stl, False
    if slot > 1:
        return slot_einbetten(quelle, ordner / f"gemappt{quelle.suffix}", slot), True
    return ummappen(quelle, ordner / f"gemappt{quelle.suffix}", {}), False


def _stl_lesen(pfad: Path) -> list:
    daten = pfad.read_bytes()
    n = struct.unpack_from("<I", daten, 80)[0]
    return [tuple(struct.unpack_from("<3f", daten, 84 + i * 50 + 12 + k * 12) for k in range(3))
            for i in range(n)]


# ---------------------------------------------------------------------------
# Zerlegen: 3MF → ein STL je Ziel-Slot (der robuste Mehrfarb-Weg)
#
# Die CLI stürzt beim Laden von Projekt-3MFs mit mehreren Extrudern im
# Wipe-Tower-Code ab (V2.3.6, calc_exclude_triangles → Segfault). STL-Gruppen
# mit --load-filament-ids und --arrange 0 funktionieren dagegen nachweislich —
# also extrahieren wir die Geometrie selbst: Transformationen anwenden,
# Dreiecke nach Farbe gruppieren, gemeinsam zur Plattenmitte schieben.
# ---------------------------------------------------------------------------

def _matrix(text: str | None):
    """3MF-Transform (12 Werte, Zeilenvektor-Konvention) → Tupel oder None."""
    if not text:
        return None
    werte = [float(w) for w in text.split()]
    return tuple(werte) if len(werte) == 12 else None


def _anwenden(m, p):
    if m is None:
        return p
    x, y, z = p
    return (x * m[0] + y * m[3] + z * m[6] + m[9],
            x * m[1] + y * m[4] + z * m[7] + m[10],
            x * m[2] + y * m[5] + z * m[8] + m[11])


def _verketten(a, b):
    """Erst b, dann a anwenden (a∘b) als neue 12er-Matrix."""
    if a is None:
        return b
    if b is None:
        return a
    neu = []
    for zeile in range(3):
        for spalte in range(3):
            neu.append(sum(b[zeile * 3 + k] * a[k * 3 + spalte] for k in range(3)))
    for spalte in range(3):
        neu.append(sum(b[9 + k] * a[k * 3 + spalte] for k in range(3)) + a[9 + spalte])
    return tuple(neu)


def _lokal(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _modelldateien_parsen(z: zipfile.ZipFile) -> dict:
    """Alle Objekte aus allen .model-Dateien: {(datei, id): {mesh|komponenten}}."""
    objekte: dict = {}
    for name in z.namelist():
        if not name.endswith(".model"):
            continue
        with z.open(name) as f:
            aktuelles = None
            for ereignis, el in ET.iterparse(f, events=("start", "end")):
                tag = _lokal(el.tag)
                if ereignis == "start":
                    if tag == "object":
                        aktuelles = {"vertices": [], "triangles": [], "komponenten": []}
                        objekte[(name, el.get("id"))] = aktuelles
                    continue
                if aktuelles is None:
                    el.clear()
                    continue
                if tag == "vertex":
                    aktuelles["vertices"].append(
                        (float(el.get("x")), float(el.get("y")), float(el.get("z"))))
                elif tag == "triangle":
                    aktuelles["triangles"].append(
                        (int(el.get("v1")), int(el.get("v2")), int(el.get("v3")),
                         _uniform_index(el.get("paint_color") or "0")))
                elif tag == "component":
                    pfad = (el.get("{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path")
                            or name)
                    aktuelles["komponenten"].append(
                        (pfad.lstrip("/"), el.get("objectid"), _matrix(el.get("transform"))))
                elif tag == "object":
                    aktuelles = None
                el.clear()
    return objekte


def _bauliste(z: zipfile.ZipFile) -> list[tuple[str, str, tuple | None]]:
    haupt = next((n for n in z.namelist() if n.endswith("3dmodel.model")), None)
    items = []
    if not haupt:
        return items
    with z.open(haupt) as f:
        for _, el in ET.iterparse(f, events=("end",)):
            if _lokal(el.tag) == "item":
                pfad = (el.get("{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path")
                        or haupt)
                items.append((pfad.lstrip("/"), el.get("objectid"), _matrix(el.get("transform"))))
            el.clear()
    return items


def _extruder_je_objekt(z: zipfile.ZipFile) -> dict[str, int]:
    """model_settings.config: Build-Objekt-ID → Standard-Extruder."""
    zuordnung: dict[str, int] = {}
    if "Metadata/model_settings.config" not in z.namelist():
        return zuordnung
    try:
        wurzel = ET.fromstring(z.read("Metadata/model_settings.config"))
    except ET.ParseError:
        return zuordnung
    for obj in wurzel.iter():
        if _lokal(obj.tag) != "object":
            continue
        oid = obj.get("id")
        for md in obj.iter():
            if _lokal(md.tag) == "metadata" and md.get("key") == "extruder":
                try:
                    zuordnung[oid] = int(md.get("value"))
                except (TypeError, ValueError):
                    pass
                break
    return zuordnung


def _stl_schreiben(pfad: Path, dreiecke: list) -> None:
    with pfad.open("wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", len(dreiecke)))
        for a, b, c in dreiecke:
            f.write(struct.pack("<12fH", 0, 0, 0, *a, *b, *c, 0))


def zerlegen(quelle: Path, ziel_dir: Path,
             zuordnung: dict[int, int]) -> list[tuple[Path, int]]:
    """3MF → [(stl_pfad, slot)] mit angewandten Transformationen; alle Gruppen
    gemeinsam zur Plattenmitte verschoben (Z-Minimum auf 0). Ausrichtung des
    Modells bleibt exakt wie in der Datei."""
    ziel_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(quelle) as z:
        objekte = _modelldateien_parsen(z)
        extruder = _extruder_je_objekt(z)
        gruppen: dict[int, list] = {}

        def sammeln(datei: str, oid: str, matrix, std_filament: int):
            obj = objekte.get((datei, oid))
            if obj is None:
                # Pfad-Varianten (mit/ohne führenden Slash) tolerieren
                obj = next((o for (d, i), o in objekte.items()
                            if i == oid and d.endswith(datei.rsplit("/", 1)[-1])), None)
            if obj is None:
                return
            v = obj["vertices"]
            for v1, v2, v3, paint in obj["triangles"]:
                filament = paint or std_filament
                slot = zuordnung.get(filament, min(filament, 4))
                gruppen.setdefault(slot, []).append(
                    (_anwenden(matrix, v[v1]), _anwenden(matrix, v[v2]),
                     _anwenden(matrix, v[v3])))
            for k_datei, k_oid, k_matrix in obj["komponenten"]:
                sammeln(k_datei, k_oid, _verketten(matrix, k_matrix), std_filament)

        for datei, oid, matrix in _bauliste(z):
            sammeln(datei, oid, matrix, extruder.get(oid, 1))

    if not gruppen:
        raise ValueError("3MF enthält keine Geometrie zum Zerlegen")

    alle = [p for dreiecke in gruppen.values() for tri in dreiecke for p in tri]
    xs, ys, zs = (sorted(p[i] for p in alle) for i in range(3))
    dx = BETT_MITTE[0] - (xs[0] + xs[-1]) / 2
    dy = BETT_MITTE[1] - (ys[0] + ys[-1]) / 2
    dz = -zs[0]

    ergebnis = []
    for slot in sorted(gruppen):
        verschoben = [tuple((x + dx, y + dy, z + dz) for x, y, z in tri)
                      for tri in gruppen[slot]]
        pfad = ziel_dir / f"gruppe_slot{slot}.stl"
        _stl_schreiben(pfad, verschoben)
        ergebnis.append((pfad, slot))
    return ergebnis
