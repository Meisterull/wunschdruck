#!/usr/bin/env python3
"""App-Symbole (PNG) ohne Abhängigkeiten rechnen: Druckdüse über Schichten.

Aufruf: symbol_bauen.py  →  app/statisch/symbol-{192,512}.png
"""
import struct
import zlib
from pathlib import Path

GRUND = (179, 89, 31)      # Terrakotta wie die Akzentfarbe der App
WEISS = (255, 250, 244)


def zeichnen(s: int) -> bytes:
    f = s / 16.0
    zeilen = bytearray()
    for py in range(s):
        zeilen.append(0)  # PNG-Filter: none
        y = py / f
        for px in range(s):
            x = px / f
            farbe = GRUND
            # Düse: Trichter von y=3 bis y=8, Spitze unten mittig
            if 3.0 <= y < 8.0 and abs(x - 8.0) <= (8.0 - y) * 0.55 + 0.35:
                farbe = WEISS
            # Frisch gelegte Schichten: drei Balken, nach unten breiter
            elif 9.5 <= y < 10.5 and 6.0 <= x < 10.0:
                farbe = WEISS
            elif 11.0 <= y < 12.0 and 4.5 <= x < 11.5:
                farbe = WEISS
            elif 12.5 <= y < 13.5 and 3.0 <= x < 13.0:
                farbe = WEISS
            zeilen.extend(farbe)
    return bytes(zeilen)


def png_schreiben(pfad: Path, s: int) -> None:
    def block(typ: bytes, daten: bytes) -> bytes:
        return (struct.pack(">I", len(daten)) + typ + daten
                + struct.pack(">I", zlib.crc32(typ + daten)))

    ihdr = struct.pack(">IIBBBBB", s, s, 8, 2, 0, 0, 0)
    body = zlib.compress(zeichnen(s), 9)
    pfad.write_bytes(b"\x89PNG\r\n\x1a\n" + block(b"IHDR", ihdr)
                     + block(b"IDAT", body) + block(b"IEND", b""))
    print(f"{pfad} ({s}x{s})")


if __name__ == "__main__":
    ziel = Path(__file__).resolve().parent.parent / "app" / "statisch"
    for groesse in (192, 512):
        png_schreiben(ziel / f"symbol-{groesse}.png", groesse)
