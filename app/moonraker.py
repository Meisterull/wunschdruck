"""Moonraker-Anbindung (Upload + Druckstart + Zustand) — synchron via httpx."""
from pathlib import Path

import httpx

from . import konfig


def konfiguriert() -> bool:
    return bool(konfig.MOONRAKER_URL)


def erreichbar(timeout: float = 3.0) -> bool:
    if not konfiguriert():
        return False
    try:
        r = httpx.get(f"{konfig.MOONRAKER_URL}/printer/info", timeout=timeout)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def zustand(timeout: float = 5.0) -> str:
    """Klipper-Druckzustand: standby|printing|paused|complete|cancelled|error|offline."""
    try:
        r = httpx.get(
            f"{konfig.MOONRAKER_URL}/printer/objects/query",
            params={"print_stats": ""}, timeout=timeout)
        r.raise_for_status()
        return r.json()["result"]["status"]["print_stats"]["state"]
    except (httpx.HTTPError, KeyError):
        return "offline"


def frei() -> bool:
    return zustand() in ("standby", "complete", "cancelled")


def hochladen(gcode: Path, name: str, timeout: float = 120.0) -> None:
    """G-Code in den Drucker-Dateispeicher legen (ohne Druckstart)."""
    with gcode.open("rb") as f:
        r = httpx.post(
            f"{konfig.MOONRAKER_URL}/server/files/upload",
            files={"file": (name, f, "text/x-gcode")},
            data={"root": "gcodes"}, timeout=timeout)
    r.raise_for_status()


def druck_starten(name: str, timeout: float = 15.0) -> None:
    r = httpx.post(
        f"{konfig.MOONRAKER_URL}/printer/print/start",
        params={"filename": name}, timeout=timeout)
    r.raise_for_status()
