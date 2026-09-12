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


def filamente(timeout: float = 4.0) -> list[dict] | None:
    """Was aktuell in den 4 Slots geladen ist (U1: print_task_config) —
    Typ/Farbe/Hersteller pflegt man am Drucker-Touchscreen. None = offline."""
    try:
        r = httpx.get(f"{konfig.MOONRAKER_URL}/printer/objects/query",
                      params={"print_task_config": ""}, timeout=timeout)
        r.raise_for_status()
        cfg = r.json()["result"]["status"]["print_task_config"]
    except (httpx.HTTPError, KeyError):
        return None

    def feld(name, i, standard=""):
        werte = cfg.get(name) or []
        return werte[i] if i < len(werte) else standard

    def farbe(i):
        roh = str(feld("filament_color_rgba", i)).lstrip("#")
        return f"#{roh[:6]}" if len(roh) >= 6 else ""

    return [{"geladen": bool(feld("filament_exist", i, False)),
             "typ": str(feld("filament_type", i)),
             "farbe": farbe(i),
             "hersteller": str(feld("filament_vendor", i))}
            for i in range(4)]


def druckstand(timeout: float = 4.0) -> dict | None:
    """{zustand, datei, fortschritt} in einer Abfrage — auch fuer Drucke, die
    am Geraet gestartet wurden. None = nicht erreichbar."""
    try:
        r = httpx.get(f"{konfig.MOONRAKER_URL}/printer/objects/query",
                      params={"print_stats": "", "display_status": ""}, timeout=timeout)
        r.raise_for_status()
        status = r.json()["result"]["status"]
        return {"zustand": status["print_stats"].get("state", "standby"),
                "datei": status["print_stats"].get("filename") or "",
                "fortschritt": (status.get("display_status") or {}).get("progress")}
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def fortschritt(timeout: float = 5.0) -> float | None:
    """Druckfortschritt 0..1 (None wenn nicht ermittelbar)."""
    try:
        r = httpx.get(
            f"{konfig.MOONRAKER_URL}/printer/objects/query",
            params={"display_status": ""}, timeout=timeout)
        r.raise_for_status()
        return r.json()["result"]["status"]["display_status"].get("progress")
    except (httpx.HTTPError, KeyError):
        return None


def historie(limit: int = 40, timeout: float = 8.0) -> list[dict] | None:
    """Abgeschlossene Drucke aus der Moonraker-Historie (None = nicht erreichbar)."""
    try:
        r = httpx.get(f"{konfig.MOONRAKER_URL}/server/history/list",
                      params={"limit": limit, "order": "desc"}, timeout=timeout)
        r.raise_for_status()
        return r.json()["result"].get("jobs", [])
    except (httpx.HTTPError, KeyError):
        return None


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
