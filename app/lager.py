"""Rollen-Lager: Verbrauch aus der Drucker-Historie auf die Rollen buchen.

Jeder abgeschlossene Moonraker-Historieneintrag einer Wunschdruck-Datei
(wunschdruck_<jobid>_…) wird genau einmal verbucht — egal ob der Druck ueber
die App-Freigabe oder am Geraet gestartet wurde, auch bei Wiederholungen.
Der Verbrauch je Slot stammt aus dem G-Code des Jobs; bei Abbruch zaehlt der
Anteil tatsaechlich extrudierter Laenge (Historie) an der Gesamtlaenge des
G-Codes, ersatzweise der zuletzt gesehene Fortschritt.
"""
import json
import logging
import re

from . import db

log = logging.getLogger("wunschdruck.lager")

_DATEI_RE = re.compile(r"^wunschdruck_([0-9a-f]{8})_")
_letzter_fortschritt: dict[str, float] = {}  # Dateiname → Fortschritt 0..1


def job_id_aus_datei(dateiname: str | None) -> str | None:
    m = _DATEI_RE.match(dateiname or "")
    return m.group(1) if m else None


def fortschritt_merken(dateiname: str | None, fortschritt: float | None) -> None:
    """Fallback fuer den Abbruch-Anteil, wenn die Historie keine Laenge liefert."""
    if dateiname and fortschritt is not None:
        _letzter_fortschritt[dateiname] = float(fortschritt)


def anteil(eintrag: dict, job) -> float:
    """0..1: wie viel des geplanten Verbrauchs wirklich gedruckt wurde."""
    if eintrag.get("status") == "completed":
        return 1.0
    try:
        mm = float(eintrag.get("filament_used") or 0)
        gesamt = float(job["filament_mm"] or 0)
    except (TypeError, ValueError):
        mm, gesamt = 0.0, 0.0
    if mm > 0 and gesamt > 0:
        return min(1.0, mm / gesamt)
    return min(1.0, max(0.0, _letzter_fortschritt.get(eintrag.get("filename") or "", 0.0)))


def eintrag_verbuchen(eintrag: dict, slots: list[dict]) -> bool:
    """Einen Historieneintrag buchen; False, wenn nichts zu tun war."""
    hid = str(eintrag.get("job_id") or "")
    job_id = job_id_aus_datei(eintrag.get("filename"))
    if not hid or not job_id or eintrag.get("status") == "in_progress" or db.verbucht(hid):
        return False
    job = db.job(job_id)
    if job is None:
        db.verbuchen(hid, job_id)  # Job schon geloescht — nichts mehr zuzuordnen
        return False
    verbrauch = json.loads(job["verbrauch"]) if job["verbrauch"] else []
    faktor = anteil(eintrag, job)
    art = "druck" if eintrag.get("status") == "completed" else "abbruch"
    gebucht = 0
    for i, gramm in enumerate(verbrauch[:4]):
        rolle_id = slots[i].get("rolle_id") if i < len(slots) else None
        if gramm > 0 and faktor > 0 and rolle_id:
            db.rolle_abziehen(int(rolle_id), gramm * faktor, art, faktor, job["name"])
            gebucht += 1
    db.verbuchen(hid, job_id)
    _letzter_fortschritt.pop(eintrag.get("filename") or "", None)
    log.info("Verbrauch verbucht: %s (%s, Anteil %.0f %%, %d Rollen)",
             job["name"], art, faktor * 100, gebucht)
    return True


def historie_verbuchen(eintraege: list[dict] | None) -> int:
    if not eintraege:
        return 0
    slots = db.slots()
    return sum(1 for e in eintraege if eintrag_verbuchen(e, slots))
