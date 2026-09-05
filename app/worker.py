"""Hintergrund-Arbeiter: Slice-Warteschlange und Drucker-Kette.

Zwei Threads:
- Slicer-Thread: arbeitet hochgeladene Jobs nacheinander ab.
- Drucker-Thread: schaut zyklisch nach freigegebenen Jobs (Strom-Kette,
  Upload, Start) und nach laufenden Drucken (fertig/Fehler erkennen).
"""
import json
import logging
import queue
import threading
import time
from pathlib import Path

from . import db, dreimf, ha, i18n, konfig, moonraker, slicer

log = logging.getLogger("wunschdruck.worker")

slice_schlange: "queue.Queue[str]" = queue.Queue()
_einschalt_versuch: dict[str, float] = {}  # job_id → Zeitpunkt des ersten Einschaltens


def _texte():
    return i18n.t(konfig.SPRACHE_STANDARD)


def dauer_text(sekunden) -> str:
    if not sekunden:
        return "?"
    h, rest = divmod(int(sekunden), 3600)
    m = rest // 60
    return f"{h} h {m:02d} min" if h else f"{m} min"


def job_dir(job_id: str) -> Path:
    return konfig.JOBS_DIR / job_id


def _slicer_lauf():
    while True:
        job_id = slice_schlange.get()
        zeile = db.job(job_id)
        if zeile is None or zeile["status"] != "neu":
            continue
        db.job_update(job_id, status="slicet")
        ordner = job_dir(job_id)
        quellen = [p for p in ordner.glob("quelle.*")]
        slots = db.slots()
        material = slots[zeile["slot"] - 1]["material"]
        try:
            quelle = quellen[0]
            # Mehrfarb-Erkennung: 3MFs einmalig analysieren; nutzt das Modell
            # mehrere Farben, wartet der Job auf die Slot-Zuordnung in der UI.
            farben_roh = zeile["farben"]
            if quelle.suffix.lower() == ".3mf" and farben_roh is None:
                analyse = dreimf.analysiere(quelle)
                farben_roh = json.dumps(analyse)
                db.job_update(job_id, farben=farben_roh)
            analyse = json.loads(farben_roh) if farben_roh else {"farben": [], "fein": False}
            zuordnung = json.loads(zeile["zuordnung"]) if zeile["zuordnung"] else None
            # Mehrfarbig ohne Zuordnung → erst der Nutzer (gilt auch beim
            # Neu-Slicen mit "Farben neu zuordnen").
            if len(analyse["farben"]) >= 2 and not zuordnung:
                db.job_update(job_id, status="farbwahl")
                continue

            # Stuetz-Filament nur wirksam, wenn Stuetzen ueberhaupt an sind.
            stuetz_slot = (zeile["stuetz_slot"] or 0) if zeile["stuetzen"] else 0
            ausrichtung = zeile["ausrichtung"] or "auto"
            anzahl = zeile["anzahl"] or 1

            if len(analyse["farben"]) >= 2 and zuordnung:
                # Mehrfarb-Projekt: Paint-Codes/Extruder auf die Ziel-Slots
                # ummappen und mit Vanilla-Orca slicen (der Fork crasht daran).
                # Bei feiner Bemalung erzwingt die UI Identitäts-Zuordnung —
                # die langen Baum-Codes bleiben dann unangetastet.
                zuordnung_int = {int(nr): slot for nr, slot in zuordnung.items()}
                quelle = dreimf.ummappen(
                    quelle, ordner / f"gemappt{quelle.suffix}", zuordnung_int)
                ergebnis = slicer.slicen(
                    quelle, ordner / "ausgabe", "PLA", zeile["qualitaet"],
                    bool(zeile["stuetzen"]), 1, zeile["platte"],
                    slot_materialien=[s["material"] for s in slots],
                    stuetz_slot=stuetz_slot, ausrichtung=ausrichtung,
                    anzahl=anzahl, mehrfarb_projekt=True)
            else:
                # Einfarbige 3MFs entgiften (CLI-giftige eingebettete Settings).
                if quelle.suffix.lower() == ".3mf":
                    quelle = dreimf.ummappen(quelle, ordner / f"gemappt{quelle.suffix}", {})
                # Stuetzen aus anderem Slot brauchen alle 4 Filament-Profile.
                slot_materialien = ([s["material"] for s in slots]
                                    if stuetz_slot and stuetz_slot != zeile["slot"] else None)
                ergebnis = slicer.slicen(
                    quelle, ordner / "ausgabe", material, zeile["qualitaet"],
                    bool(zeile["stuetzen"]), zeile["slot"], zeile["platte"],
                    slot_materialien=slot_materialien, stuetz_slot=stuetz_slot,
                    ausrichtung=ausrichtung, anzahl=anzahl)
            db.job_update(job_id, status="bereit",
                          dauer_s=ergebnis["dauer_s"], filament_g=ergebnis["filament_g"],
                          verbrauch=json.dumps(ergebnis.get("verbrauch")))
            ha.melden(_texte()["meldung_bereit"].format(
                name=zeile["name"], dauer=dauer_text(ergebnis["dauer_s"]),
                gewicht=ergebnis["filament_g"]))
        except Exception as fehler:  # Slicer-Fehler landen sichtbar am Job
            log.exception("Slicen fehlgeschlagen: %s", job_id)
            db.job_update(job_id, status="fehler", fehler_text=str(fehler)[:2000])
            ha.melden(_texte()["meldung_fehler"].format(name=zeile["name"], fehler=str(fehler)[:200]))


def _gcode_name(zeile) -> str:
    return f"wunschdruck_{zeile['id']}_{zeile['name']}.gcode"


def _freigegebene_abarbeiten():
    wartende = db.jobs_mit_status("freigegeben")
    if not wartende:
        _einschalt_versuch.clear()
        return
    if not moonraker.konfiguriert():
        for zeile in wartende:
            db.job_update(zeile["id"], status="fehler",
                          fehler_text=_texte()["fehler_kein_moonraker"])
        return

    zeile = wartende[0]  # aelteste Freigabe zuerst
    if not moonraker.erreichbar():
        # Strom-Kette: Steckdose einschalten und auf Moonraker warten.
        start = _einschalt_versuch.setdefault(zeile["id"], time.time())
        if time.time() - start > konfig.EINSCHALT_TIMEOUT_S:
            db.job_update(zeile["id"], status="fehler",
                          fehler_text="Drucker kam nicht online (Einschalt-Timeout).")
            _einschalt_versuch.pop(zeile["id"], None)
            return
        if ha.wartungsriegel_aktiv():
            db.job_update(zeile["id"], status="fehler",
                          fehler_text="Wartungsriegel ist gesetzt — Steckdose bleibt unangetastet.")
            _einschalt_versuch.pop(zeile["id"], None)
            return
        ha.steckdose_einschalten()
        return
    if not moonraker.frei():
        return  # Drucker beschaeftigt — naechste Runde wieder pruefen

    _einschalt_versuch.pop(zeile["id"], None)
    db.job_update(zeile["id"], status="sendet")
    try:
        gcode = job_dir(zeile["id"]) / "ausgabe"
        datei = sorted(gcode.glob("plate_*.gcode"))[0]
        name = _gcode_name(zeile)
        moonraker.hochladen(datei, name)
        moonraker.druck_starten(name)
        db.job_update(zeile["id"], status="druckt", gestartet=time.time())
        ha.melden(_texte()["meldung_gestartet"].format(
            name=zeile["name"], dauer=dauer_text(zeile["dauer_s"])))
    except Exception as fehler:
        log.exception("Druckstart fehlgeschlagen: %s", zeile["id"])
        db.job_update(zeile["id"], status="fehler", fehler_text=str(fehler)[:2000])
        ha.melden(_texte()["meldung_fehler"].format(name=zeile["name"], fehler=str(fehler)[:200]))


def _druckende_pruefen():
    for zeile in db.jobs_mit_status("druckt"):
        zustand = moonraker.zustand()
        if zustand == "complete":
            db.job_update(zeile["id"], status="fertig", fortschritt=100.0)
            ha.melden(_texte()["meldung_fertig"].format(name=zeile["name"]))
        elif zustand == "cancelled":
            db.job_update(zeile["id"], status="verworfen")
        elif zustand == "error":
            db.job_update(zeile["id"], status="fehler", fehler_text="Drucker meldet Fehler.")
        elif zustand in ("printing", "paused"):
            anteil = moonraker.fortschritt()
            if anteil is not None:
                db.job_update(zeile["id"], fortschritt=round(anteil * 100, 1))
        # offline/standby: nichts tun — naechste Runde


_letzte_aufraeumung = 0.0


def _aufraeumen():
    """Abgeschlossene Jobs nach AUFRAEUMEN_TAGE loeschen (1x pro 6 h geprueft)."""
    global _letzte_aufraeumung
    if not konfig.AUFRAEUMEN_TAGE or time.time() - _letzte_aufraeumung < 6 * 3600:
        return
    _letzte_aufraeumung = time.time()
    for zeile in db.jobs_abgelaufen(konfig.AUFRAEUMEN_TAGE):
        db.job_loeschen(zeile["id"])
        slicer.aufraeumen(job_dir(zeile["id"]))
        log.info("Job %s automatisch aufgeraeumt (> %d Tage)",
                 zeile["id"], konfig.AUFRAEUMEN_TAGE)


def _drucker_lauf():
    while True:
        try:
            _freigegebene_abarbeiten()
            _druckende_pruefen()
            _aufraeumen()
        except Exception:
            log.exception("Drucker-Runde fehlgeschlagen")
        time.sleep(20)


def starten():
    """Beim App-Start: liegengebliebene Jobs wieder einreihen, Threads starten."""
    for zeile in db.jobs_mit_status("neu", "slicet"):
        db.job_update(zeile["id"], status="neu")
        slice_schlange.put(zeile["id"])
    for zeile in db.jobs_mit_status("sendet"):
        db.job_update(zeile["id"], status="freigegeben")
    threading.Thread(target=_slicer_lauf, daemon=True, name="slicer").start()
    threading.Thread(target=_drucker_lauf, daemon=True, name="drucker").start()
