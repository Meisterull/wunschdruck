"""Vorschaubilder für den Drucker: PNG-Thumbnails aus den Vorschau-Daten
rendern und als Orca-Thumbnail-Block in den G-Code einbetten.

Die Orca-CLI rendert headless KEINE Thumbnails (kein GL-Kontext) — ohne
Bild zeigt der U1-Touchscreen zum Job nur einen Platzhalter (Befund
12.09.2026). Gezeichnet wird der gesliceste G-Code selbst (Ebenen von unten
nach oben, Maler-Algorithmus), also die echte Lage auf der Platte in den
echten Slot-Farben. Blockformat wie Orca/Snapmaker-Orca:
    ; THUMBNAIL_BLOCK_START
    ; thumbnail begin 96x96 <Base64-Länge>
    ; <Base64, 78 Zeichen je Zeile>
    ; thumbnail end
    ; THUMBNAIL_BLOCK_END
Moonraker liest die Blöcke aus den ersten 512 KB der Datei.
"""
import base64
import io
import logging
import math
from pathlib import Path

from PIL import Image, ImageDraw

from . import vorschau

log = logging.getLogger("wunschdruck.miniatur")

GROESSEN = ((96, 96), (48, 48), (300, 300))  # Reihenfolge wie Snapmaker-Orca
UEBERABTASTUNG = 3          # Rendern in 3-facher Größe, dann glatt verkleinern
DREHUNG = math.radians(32)  # Blick schräg von vorne links …
NEIGUNG = math.radians(38)  # … und von oben
RAND = 0.06                 # freier Rand je Seite (Anteil der Kantenlänge)
BAHN_BREITE_MM = 0.45
STUETZ_FARBE = (170, 170, 170)
STANDARD_FARBE = (120, 160, 200)
_KLASSEN = ("teil", "stuetze")  # Füllung/Rand tragen zum Bild nichts bei


def _farbe(hexwert: str | None) -> tuple[int, int, int]:
    """Slot-Farbe → RGB; sehr dunkle Farben aufhellen, damit schwarzes
    Filament auf dem dunklen Drucker-Display nicht verschwindet."""
    try:
        h = (hexwert or "").lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return STANDARD_FARBE
    leuchte = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
    if leuchte < 0.25:
        t = (0.25 - leuchte) / 0.25 * 0.4
        r, g, b = (int(c + (150 - c) * t) for c in (r, g, b))
    return r, g, b


def _schattieren(rgb, faktor: float) -> tuple[int, int, int, int]:
    return tuple(max(0, min(255, int(c * faktor))) for c in rgb) + (255,)


def rendern(daten: dict, slot_farben: list[str], kante: int) -> Image.Image:
    """Quadratisches RGBA-Bild (transparenter Hintergrund) der Vorschau-Daten."""
    ebenen = daten.get("ebenen") or []
    cos_d, sin_d = math.cos(DREHUNG), math.sin(DREHUNG)
    cos_n, sin_n = math.cos(NEIGUNG), math.sin(NEIGUNG)

    # Bounding-Box des Modells (ohne Rand-Bahnen) für Zentrierung und Maßstab.
    xs, ys = [], []
    for ebene in ebenen:
        for spur in ebene["spuren"]:
            if spur["k"] not in _KLASSEN:
                continue
            for linie in spur["l"]:
                xs.extend(linie[0::2])
                ys.extend(linie[1::2])
    bild = Image.new("RGBA", (kante, kante), (0, 0, 0, 0))
    if not xs:
        return bild
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    hoehe = float(daten.get("hoehe") or 0.0)
    cz = hoehe / 2

    def projizieren(x, y, z):
        x, y, z = x - cx, y - cy, z - cz
        x1 = x * cos_d - y * sin_d
        y1 = x * sin_d + y * cos_d
        return x1, z * cos_n - y1 * sin_n, y1 * cos_n + z * sin_n  # u, v(hoch), tiefe

    ecken = [projizieren(x, y, z) for x in (min(xs), max(xs))
             for y in (min(ys), max(ys)) for z in (0.0, hoehe)]
    breite = max(e[0] for e in ecken) - min(e[0] for e in ecken)
    hoch = max(e[1] for e in ecken) - min(e[1] for e in ecken)
    massstab = kante * (1 - 2 * RAND) / max(breite, hoch, 1e-6)
    u0 = (max(e[0] for e in ecken) + min(e[0] for e in ecken)) / 2
    v0 = (max(e[1] for e in ecken) + min(e[1] for e in ecken)) / 2
    strich = max(1, round(BAHN_BREITE_MM * massstab))

    zeichner = ImageDraw.Draw(bild)
    farben = {t: _farbe(slot_farben[t] if t < len(slot_farben) else None)
              for t in daten.get("werkzeuge") or [0]}
    n = max(len(ebenen) - 1, 1)
    for i, ebene in enumerate(ebenen):
        z = float(ebene["z"])
        faktor = 0.62 + 0.43 * (i / n)  # unten dunkler, oben heller = Licht von oben
        bahnen = []
        for spur in ebene["spuren"]:
            if spur["k"] not in _KLASSEN:
                continue
            basis = STUETZ_FARBE if spur["k"] == "stuetze" else farben.get(spur["t"], STANDARD_FARBE)
            fill = _schattieren(basis, faktor)
            for linie in spur["l"]:
                punkte, tiefe = [], 0.0
                for x, y in zip(linie[0::2], linie[1::2]):
                    u, v, d = projizieren(x, y, z)
                    punkte.append((kante / 2 + (u - u0) * massstab,
                                   kante / 2 - (v - v0) * massstab))
                    tiefe += d
                bahnen.append((tiefe / max(len(punkte), 1), punkte, fill))
        # Innerhalb der Ebene: hinten zuerst, damit vordere Bahnen darüberliegen.
        bahnen.sort(key=lambda b: -b[0])
        for _, punkte, fill in bahnen:
            if len(punkte) == 1:
                punkte = punkte * 2
            zeichner.line(punkte, fill=fill, width=strich, joint="curve")
    return bild


def bilder_rendern(daten: dict, slot_farben: list[str]) -> list[tuple[int, int, bytes]]:
    """[(Breite, Höhe, PNG-Bytes)] für alle GROESSEN — einmal groß rendern,
    dann glatt herunterskalieren."""
    gross = max(g[0] for g in GROESSEN) * UEBERABTASTUNG
    bild = rendern(daten, slot_farben, gross)
    ergebnis = []
    for breite, hoehe in GROESSEN:
        klein = bild.resize((breite, hoehe), Image.LANCZOS)
        puffer = io.BytesIO()
        klein.save(puffer, format="PNG", optimize=True)
        ergebnis.append((breite, hoehe, puffer.getvalue()))
    return ergebnis


def einbetten(gcode: Path, bilder: list[tuple[int, int, bytes]]) -> bool:
    """Thumbnail-Block hinter den Orca-Header schreiben. False, wenn die
    Datei schon Thumbnails trägt (idempotent)."""
    daten = gcode.read_bytes()
    if b"; thumbnail begin" in daten[:1 << 20]:
        return False
    zeilen = ["; THUMBNAIL_BLOCK_START"]
    for breite, hoehe, png in bilder:
        b64 = base64.b64encode(png).decode("ascii")
        zeilen.append(f"; thumbnail begin {breite}x{hoehe} {len(b64)}")
        zeilen.extend("; " + b64[i:i + 78] for i in range(0, len(b64), 78))
        zeilen.append("; thumbnail end")
    zeilen.append("; THUMBNAIL_BLOCK_END")
    block = ("\n".join(zeilen) + "\n\n").encode("ascii")
    marke = b"; HEADER_BLOCK_END\n"
    pos = daten.find(marke)
    einfuegen = pos + len(marke) + (1 if daten[pos + len(marke):pos + len(marke) + 1] == b"\n" else 0) \
        if pos >= 0 else 0
    tmp = gcode.with_suffix(gcode.suffix + ".tmp")
    tmp.write_bytes(daten[:einfuegen] + block + daten[einfuegen:])
    tmp.replace(gcode)
    return True


def gcode_bebildern(gcode: Path, slot_farben: list[str]) -> bool:
    """Vorschau rechnen, Thumbnails einbetten, Vorschau-Cache passend dazu
    ablegen (die Einbettung ändert die G-Code-mtime, die Geometrie nicht)."""
    daten = vorschau.gcode_vorschau(gcode)
    bilder = bilder_rendern(daten, slot_farben)
    neu = einbetten(gcode, bilder)
    vorschau.cache_schreiben(gcode, daten)
    if neu:
        log.info("Thumbnails eingebettet: %s (%s)", gcode.name,
                 ", ".join(f"{b}x{h}={len(p)}B" for b, h, p in bilder))
    return neu
