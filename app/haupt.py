"""Wunschdruck — Web-Oberflaeche (FastAPI + Jinja2, bewusst schlicht und handy-tauglich)."""
import contextlib
import json
import logging
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, i18n, konfig, moonraker, printables, slicer, thingiverse, vorschau, worker

logging.basicConfig(level=logging.INFO)

ERLAUBTE_ENDUNGEN = {".stl", ".3mf"}
AKTIVE_STATUS = {"neu", "slicet", "freigegeben", "sendet", "druckt"}
MIT_GCODE = {"bereit", "freigegeben", "sendet", "druckt", "fertig"}


@contextlib.asynccontextmanager
async def lebenslauf(app: FastAPI):
    konfig.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    worker.starten()
    yield


app = FastAPI(title="Wunschdruck", lifespan=lebenslauf)
app.mount("/statisch", StaticFiles(directory=Path(__file__).parent / "statisch"), name="statisch")
vorlagen = Jinja2Templates(directory=Path(__file__).parent / "vorlagen")
vorlagen.env.globals["dauer_text"] = worker.dauer_text
vorlagen.env.filters["datum"] = lambda ts: time.strftime("%d.%m.%Y %H:%M", time.localtime(ts or 0))


def sprache(request: Request) -> str:
    return request.cookies.get("sprache", konfig.SPRACHE_STANDARD)


def drucker_status() -> str:
    if not moonraker.konfiguriert():
        return "unkonfiguriert"
    zustand = moonraker.zustand(timeout=2)
    if zustand == "offline":
        return "offline"
    return "druckt" if zustand in ("printing", "paused") else "frei"


def _farbnah(a: str, b: str) -> bool:
    """Zwei Hex-Farben grob vergleichbar? (Touchscreen- vs. Lager-Farbton)"""
    try:
        ra, ga, ba = (int(a.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
        rb, gb, bb = (int(b.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
        return abs(ra - rb) + abs(ga - gb) + abs(ba - bb) <= 180
    except (ValueError, IndexError):
        return True  # ohne saubere Farben nur Material vergleichen


def _job_slots(zeile) -> set[int]:
    """Welche physischen Slots ein Job benutzt."""
    slots = set()
    if zeile["zuordnung"]:
        slots.update(json.loads(zeile["zuordnung"]).values())
    else:
        slots.add(zeile["slot"])
    if zeile["stuetzen"] and (zeile["stuetz_slot"] or 0) > 0:
        slots.add(zeile["stuetz_slot"])
    return {min(max(int(s), 1), 4) for s in slots}


@app.get("/")
def index(request: Request):
    sp = sprache(request)
    jobs = db.jobs_alle()
    farben_jobs = {z["id"]: json.loads(z["farben"])
                   for z in jobs if z["status"] == "farbwahl" and z["farben"]}
    verbrauch_jobs = {}
    mehrfarb_jobs = set()
    for z in jobs:
        if z["verbrauch"]:
            werte = json.loads(z["verbrauch"])
            if werte and sum(1 for w in werte if w > 0) > 1:
                verbrauch_jobs[z["id"]] = werte
        if z["farben"] and len(json.loads(z["farben"]).get("farben", [])) >= 2:
            mehrfarb_jobs.add(z["id"])

    # Filament-Abgleich: Was ist im Drucker geladen vs. was braucht der Job?
    drucker = drucker_status()
    drucker_filamente = moonraker.filamente() if drucker in ("frei", "druckt") else None
    slots_cfg = db.slots()
    abgleich = {}
    if drucker_filamente:
        for z in jobs:
            if z["status"] != "bereit":
                continue
            zeilen, alles_ok = [], True
            for s in sorted(_job_slots(z)):
                konf, ist = slots_cfg[s - 1], drucker_filamente[s - 1]
                passt = (ist["geladen"] and konf["material"] != "leer"
                         and konf["material"].lower() in ist["typ"].lower()
                         and _farbnah(konf.get("hex", ""), ist["farbe"]))
                alles_ok = alles_ok and passt
                zeilen.append({"slot": s, "konf": konf, "ist": ist, "passt": passt})
            abgleich[z["id"]] = {"zeilen": zeilen, "ok": alles_ok}
    # Rollen-Lager: Rest je Slot und Warnung, wenn ein Job mehr braucht.
    rollen = db.rollen_map()
    slot_rest = [rollen[s["rolle_id"]]["rest_g"] if s.get("rolle_id") in rollen else None
                 for s in slots_cfg]
    rest_warnungen = {}
    for z in jobs:
        if z["status"] != "bereit" or not z["verbrauch"]:
            continue
        for i, g in enumerate(json.loads(z["verbrauch"])[:4]):
            if g > 0 and slot_rest[i] is not None and g > slot_rest[i]:
                rest_warnungen.setdefault(z["id"], []).append(
                    {"slot": i + 1, "bedarf": g, "rest": slot_rest[i]})
    return vorlagen.TemplateResponse(request, "index.html", {
        "t": i18n.t(sp), "sprache": sp,
        "jobs": jobs, "slots": slots_cfg,
        "slot_rest": slot_rest, "rest_warnungen": rest_warnungen,
        "drucker": drucker,
        "abgleich": abgleich,
        "gesendet": request.query_params.get("gesendet"),
        "sendefehler": request.query_params.get("sendefehler"),
        "aktive_status": AKTIVE_STATUS,
        "farben_jobs": farben_jobs,
        "verbrauch_jobs": verbrauch_jobs,
        "mehrfarb_jobs": mehrfarb_jobs,
        "eigene_profile": slicer.eigene_profile(),
        "kamera_url": konfig.KAMERA_URL,
    })


@app.post("/hochladen")
async def hochladen(request: Request, datei: UploadFile = File(...),
                    slot: int = Form(1), qualitaet: str = Form("standard"),
                    stuetzen: str = Form("auto"), platte: int = Form(1),
                    stuetz_slot: int = Form(0), ausrichtung: str = Form("auto"),
                    anzahl: int = Form(1), fuellung: str = Form(""),
                    stuetz_art: str = Form("profil"), waende: str = Form(""),
                    deckschichten: str = Form(""), bodenschichten: str = Form(""),
                    naht: str = Form("")):
    endung = Path(datei.filename or "").suffix.lower()
    qualitaet_ok = (qualitaet in ("fein", "standard", "schnell")
                    or (qualitaet.startswith("eigen:")
                        and qualitaet[6:] in slicer.eigene_profile()))
    if endung not in ERLAUBTE_ENDUNGEN or not qualitaet_ok:
        return RedirectResponse("/?fehler=format", status_code=303)
    slot = min(max(slot, 1), 4)
    job_id = db.job_anlegen(Path(datei.filename).name, slot, qualitaet,
                            stuetzen == "auto", max(platte, 1),
                            stuetz_slot=min(max(stuetz_slot, 0), 4),
                            ausrichtung="datei" if ausrichtung == "datei" else "auto",
                            anzahl=min(max(anzahl, 1), 25),
                            fuellung=_fuellung_wert(fuellung),
                            stuetz_art=stuetz_art if stuetz_art in ("profil", "baum", "normal") else "profil",
                            waende=_opt_int(waende, 1, 10),
                            deckschichten=_opt_int(deckschichten, 0, 15),
                            bodenschichten=_opt_int(bodenschichten, 0, 15),
                            naht=_naht_wert(naht))
    ordner = worker.job_dir(job_id)
    ordner.mkdir(parents=True, exist_ok=True)
    with (ordner / f"quelle{endung}").open("wb") as ziel:
        shutil.copyfileobj(datei.file, ziel)
    worker.slice_schlange.put(job_id)
    return RedirectResponse("/", status_code=303)


@app.post("/jobs/{job_id}/drucken")
def drucken(job_id: str):
    zeile = db.job(job_id)
    if zeile and zeile["status"] == "bereit":
        db.job_update(job_id, status="freigegeben")
    return RedirectResponse("/", status_code=303)


@app.post("/jobs/{job_id}/neu_slicen")
async def neu_slicen(request: Request, job_id: str):
    """Vorhandenen Job mit (geänderten) Einstellungen erneut slicen — die
    Quelldatei liegt ja noch im Job-Ordner."""
    zeile = db.job(job_id)
    if not zeile or zeile["status"] in AKTIVE_STATUS:
        return RedirectResponse("/", status_code=303)
    f = await request.form()

    def ganzzahl(name, standard, lo, hi):
        try:
            return min(max(int(str(f.get(name, standard))), lo), hi)
        except ValueError:
            return standard

    qualitaet = str(f.get("qualitaet", zeile["qualitaet"]))
    if not (qualitaet in ("fein", "standard", "schnell")
            or (qualitaet.startswith("eigen:")
                and qualitaet[6:] in slicer.eigene_profile())):
        qualitaet = "standard"
    felder = dict(
        status="neu", slot=ganzzahl("slot", zeile["slot"], 1, 4),
        qualitaet=qualitaet,
        stuetzen=int(str(f.get("stuetzen", "auto")) == "auto"),
        stuetz_slot=ganzzahl("stuetz_slot", zeile["stuetz_slot"] or 0, 0, 4),
        ausrichtung="datei" if str(f.get("ausrichtung", zeile["ausrichtung"])) == "datei" else "auto",
        anzahl=ganzzahl("anzahl", zeile["anzahl"] or 1, 1, 25),
        platte=ganzzahl("platte", zeile["platte"], 1, 16),
        fuellung=_fuellung_wert(str(f.get("fuellung", ""))),
        stuetz_art=(str(f.get("stuetz_art", "profil"))
                    if str(f.get("stuetz_art")) in ("profil", "baum", "normal") else "profil"),
        waende=_opt_int(str(f.get("waende", "")), 1, 10),
        deckschichten=_opt_int(str(f.get("deckschichten", "")), 0, 15),
        bodenschichten=_opt_int(str(f.get("bodenschichten", "")), 0, 15),
        naht=_naht_wert(str(f.get("naht", ""))),
        dauer_s=None, filament_g=None, verbrauch=None, fortschritt=None,
        fehler_text=None, gestartet=None,
    )
    if f.get("farbwahl_neu"):
        felder["zuordnung"] = None  # Mehrfarb-Job: Zuordnung neu abfragen
    db.job_update(job_id, **felder)
    # Alte Slicing-Ergebnisse weg, sonst greift der Upload spaeter zu stalem G-Code.
    shutil.rmtree(worker.job_dir(job_id) / "ausgabe", ignore_errors=True)
    worker.slice_schlange.put(job_id)
    return RedirectResponse("/", status_code=303)


@app.post("/jobs/{job_id}/senden")
def nur_senden(job_id: str):
    """G-Code nur auf den Drucker legen — Start dann manuell am Gerät
    (z. B. wenn erst noch Filament umgeladen werden soll)."""
    zeile = db.job(job_id)
    if not zeile or zeile["status"] != "bereit":
        return RedirectResponse("/", status_code=303)
    gcode = _job_gcode(job_id)
    if not gcode or not moonraker.erreichbar():
        return RedirectResponse("/?sendefehler=1", status_code=303)
    try:
        moonraker.hochladen(gcode, f"wunschdruck_{zeile['id']}_{zeile['name']}.gcode")
    except Exception:
        logging.getLogger("wunschdruck").exception("Nur-Senden fehlgeschlagen")
        return RedirectResponse("/?sendefehler=1", status_code=303)
    return RedirectResponse("/?gesendet=1", status_code=303)


@app.post("/jobs/{job_id}/verwerfen")
def verwerfen(job_id: str):
    zeile = db.job(job_id)
    if zeile and zeile["status"] in ("bereit", "freigegeben", "fehler", "farbwahl"):
        db.job_update(job_id, status="verworfen")
    return RedirectResponse("/", status_code=303)


@app.post("/jobs/{job_id}/farben")
async def farben_zuordnen(request: Request, job_id: str):
    """Farb-Zuordnung (Modell-Farbe → Slot) speichern und den Job slicen lassen."""
    zeile = db.job(job_id)
    if not zeile or zeile["status"] != "farbwahl" or not zeile["farben"]:
        return RedirectResponse("/", status_code=303)
    analyse = json.loads(zeile["farben"])
    formular = await request.form()
    zuordnung = {}
    for f in analyse["farben"]:
        nr = f["nr"]
        if analyse.get("fein"):
            slot = min(nr, 4)  # unlesbare Paint-Codes: Zuordnung bleibt fest 1:1
        else:
            try:
                slot = int(str(formular.get(f"farbe_{nr}", nr)))
            except ValueError:
                slot = nr
        zuordnung[nr] = min(max(slot, 1), 4)
    db.job_update(job_id, zuordnung=json.dumps(zuordnung), status="neu")
    worker.slice_schlange.put(job_id)
    return RedirectResponse("/", status_code=303)


@app.post("/jobs/{job_id}/loeschen")
def loeschen(job_id: str):
    zeile = db.job(job_id)
    if zeile and zeile["status"] not in AKTIVE_STATUS:
        db.job_loeschen(job_id)
        shutil.rmtree(worker.job_dir(job_id), ignore_errors=True)
    return RedirectResponse("/", status_code=303)


def _job_gcode(job_id: str) -> Path | None:
    gcodes = sorted((worker.job_dir(job_id) / "ausgabe").glob("plate_*.gcode"))
    return gcodes[0] if gcodes else None


@app.get("/jobs/{job_id}/ansicht")
def ansicht(request: Request, job_id: str):
    sp = sprache(request)
    zeile = db.job(job_id)
    if not zeile or zeile["status"] not in MIT_GCODE or not _job_gcode(job_id):
        return RedirectResponse("/", status_code=303)
    return vorlagen.TemplateResponse(request, "ansicht.html", {
        "t": i18n.t(sp), "sprache": sp, "job": zeile, "slots": db.slots(),
    })


@app.get("/jobs/{job_id}/vorschau")
def vorschau_daten(job_id: str):
    gcode = _job_gcode(job_id)
    if not gcode:
        return JSONResponse({"fehler": "kein G-Code"}, status_code=404)
    return JSONResponse(vorschau.vorschau_laden(gcode))


@app.get("/einstellungen")
def einstellungen(request: Request, gespeichert: bool = False, profilfehler: bool = False):
    sp = sprache(request)
    slots_cfg = db.slots()
    rollen = db.rollen()
    return vorlagen.TemplateResponse(request, "einstellungen.html", {
        "t": i18n.t(sp), "sprache": sp, "slots": slots_cfg,
        "rollen": rollen, "archiv": db.rollen(archiv=True),
        "im_slot": {s["rolle_id"]: i + 1 for i, s in enumerate(slots_cfg) if s.get("rolle_id")},
        "verlauf": {r["id"]: db.verlauf(r["id"]) for r in rollen},
        "heute": time.strftime("%Y-%m-%d"),
        "gespeichert": gespeichert,
        "eigene_profile": slicer.eigene_profile(), "profilfehler": profilfehler,
    })


@app.post("/einstellungen/profil")
async def profil_importieren(datei: UploadFile = File(...)):
    """Aus dem Desktop-Orca exportiertes Druckprofil (JSON) übernehmen."""
    try:
        slicer.eigenes_profil_importieren(await datei.read())
    except Exception:
        logging.getLogger("wunschdruck").exception("Profil-Import fehlgeschlagen")
        return RedirectResponse("/einstellungen?profilfehler=1", status_code=303)
    return RedirectResponse("/einstellungen?gespeichert=1", status_code=303)


@app.post("/einstellungen/profil/{name}/loeschen")
def profil_loeschen(name: str):
    pfad = slicer.EIGENE_PROFILE / f"{Path(name).name}.json"
    if pfad.exists():
        pfad.unlink()
    return RedirectResponse("/einstellungen", status_code=303)


def _opt_int(roh: str, lo: int, hi: int) -> int | None:
    """Optionales Zahlenfeld: leer = Profil-Standard."""
    roh = str(roh).strip().rstrip("%")
    if not roh:
        return None
    try:
        return min(max(int(roh), lo), hi)
    except ValueError:
        return None


def _fuellung_wert(roh: str) -> int | None:
    return _opt_int(roh, 0, 100)


def _hex_sicher(wert: str) -> str:
    wert = str(wert).strip()
    return wert if re.fullmatch(r"#[0-9a-fA-F]{6}", wert) else "#9e9e9e"


def _gramm(wert) -> float | None:
    """Gramm-Feld: leer = unbekannt, Komma erlaubt."""
    roh = str(wert or "").strip().replace(",", ".")
    if not roh:
        return None
    try:
        return max(0.0, round(float(roh), 1))
    except ValueError:
        return None


# Orca-Nahtposition; "back" = hinten auf dem Bett (+Y), also Teil mit der
# Schauseite nach vorn (-Y) aufs Bett legen.
NAHT_WERTE = ("back", "aligned", "nearest", "random")


def _naht_wert(roh: str) -> str | None:
    return roh if roh in NAHT_WERTE else None


def _rollen_felder(formular) -> dict:
    material = str(formular.get("material", "PLA"))
    if material not in ("PLA", "PETG", "TPU"):
        material = "PLA"
    return {"material": material,
            "farbe": str(formular.get("farbe", "")).strip()[:40],
            "hex": _hex_sicher(formular.get("hex", "")),
            "hersteller": str(formular.get("hersteller", "")).strip()[:60],
            "netto_g": _gramm(formular.get("netto_g")),
            "rest_g": _gramm(formular.get("rest_g")),
            "kaufdatum": str(formular.get("kaufdatum", "")).strip()[:10],
            "notiz": str(formular.get("notiz", "")).strip()[:200]}


@app.post("/einstellungen/rollen")
async def rolle_anlegen(request: Request):
    """Neue Filamentrolle ins Lager (Rest leer = unbekannt)."""
    db.rolle_anlegen(**_rollen_felder(await request.form()))
    return RedirectResponse("/einstellungen", status_code=303)


@app.post("/einstellungen/rollen/{rolle_id}")
async def rolle_aendern(request: Request, rolle_id: int):
    """Stammdaten/Rest einer Rolle aendern (Rest-Aenderung = Korrektur im Verlauf)."""
    db.rolle_update(rolle_id, **_rollen_felder(await request.form()))
    return RedirectResponse("/einstellungen", status_code=303)


@app.post("/einstellungen/rollen/{rolle_id}/archiv")
def rolle_archivieren(rolle_id: int):
    db.rolle_update(rolle_id, archiviert=1)
    return RedirectResponse("/einstellungen", status_code=303)


@app.post("/einstellungen/rollen/{rolle_id}/zurueck")
def rolle_zurueckholen(rolle_id: int):
    db.rolle_update(rolle_id, archiviert=0)
    return RedirectResponse("/einstellungen", status_code=303)


@app.post("/einstellungen/rollen/{rolle_id}/loeschen")
def rolle_loeschen(rolle_id: int):
    db.rolle_loeschen(rolle_id)
    return RedirectResponse("/einstellungen", status_code=303)


@app.post("/einstellungen")
async def einstellungen_speichern(request: Request):
    """Slots mit Rollen bestücken. Die Rollendaten werden in den Slot KOPIERT
    (plus rolle_id fuer den Verbrauchsabzug) — wird die Rolle spaeter
    archiviert oder geloescht, bleibt der Slot gueltig."""
    formular = await request.form()
    rollen = db.rollen_map()
    slots = []
    for i in range(1, 5):
        wahl = str(formular.get(f"slot{i}", "leer"))
        r = rollen.get(int(wahl)) if wahl.isdigit() else None
        if r is not None and not r["archiviert"]:
            slots.append({"material": r["material"], "farbe": r["farbe"],
                          "hex": r["hex"], "rolle_id": r["id"]})
        else:
            slots.append({"material": "leer", "farbe": "", "hex": "", "rolle_id": None})
    db.einstellung_setzen("slots", slots)
    return RedirectResponse("/einstellungen?gespeichert=1", status_code=303)


@app.post("/teilen")
async def teilen(datei: UploadFile = File(...)):
    """PWA-Share-Target: vom Handy geteilte 3MF/STL wird mit Standard-Werten
    als Job angelegt (Slot 1, Standard-Qualität, Stützen auto)."""
    endung = Path(datei.filename or "").suffix.lower()
    if endung not in ERLAUBTE_ENDUNGEN:
        return RedirectResponse("/?fehler=format", status_code=303)
    job_id = db.job_anlegen(Path(datei.filename).name, 1, "standard", True, 1)
    ordner = worker.job_dir(job_id)
    ordner.mkdir(parents=True, exist_ok=True)
    with (ordner / f"quelle{endung}").open("wb") as ziel:
        shutil.copyfileobj(datei.file, ziel)
    worker.slice_schlange.put(job_id)
    return RedirectResponse("/", status_code=303)


def _job_einstellungen(f) -> dict:
    """Job-Felder eines Formulars (jobfelder.html) → Argumente fuer db.job_anlegen."""
    def ganz(name, standard, lo, hi):
        try:
            return min(max(int(str(f.get(name, standard))), lo), hi)
        except ValueError:
            return standard
    qualitaet = str(f.get("qualitaet", "standard"))
    if not (qualitaet in ("fein", "standard", "schnell")
            or (qualitaet.startswith("eigen:") and qualitaet[6:] in slicer.eigene_profile())):
        qualitaet = "standard"
    stuetz_art = str(f.get("stuetz_art", "profil"))
    return dict(
        slot=ganz("slot", 1, 1, 4), qualitaet=qualitaet,
        stuetzen=str(f.get("stuetzen", "auto")) == "auto", platte=ganz("platte", 1, 1, 16),
        stuetz_slot=ganz("stuetz_slot", 0, 0, 4),
        ausrichtung="datei" if str(f.get("ausrichtung", "auto")) == "datei" else "auto",
        anzahl=ganz("anzahl", 1, 1, 25), fuellung=_fuellung_wert(str(f.get("fuellung", ""))),
        stuetz_art=stuetz_art if stuetz_art in ("profil", "baum", "normal") else "profil",
        waende=_opt_int(str(f.get("waende", "")), 1, 10),
        deckschichten=_opt_int(str(f.get("deckschichten", "")), 0, 15),
        bodenschichten=_opt_int(str(f.get("bodenschichten", "")), 0, 15),
        naht=_naht_wert(str(f.get("naht", ""))))


# Modellportale mit Suche in der App — gleiche Modul-Schnittstelle:
# konfiguriert / suchen / modell / dateien / herunterladen (+ bilder, wenn
# modell() keine liefert). Thingiverse braucht ein App-Token, Printables ein Flag.
QUELLEN = {"thingiverse": thingiverse, "printables": printables}
QUELLEN_NAMEN = {"thingiverse": "Thingiverse", "printables": "Printables"}
API_FEHLER = (thingiverse.ApiFehler, printables.ApiFehler)


def _quellen() -> list[str]:
    return [name for name, modul in QUELLEN.items() if modul.konfiguriert()]


@app.get("/suche")
def suche(request: Request, q: str = "", seite: int = 1, quelle: str = ""):
    """Modelle finden: Link-Kacheln (MakerWorld/Thingiverse/Printables im
    Browser) und, je nach Konfiguration, die Suche direkt in der App."""
    sp = sprache(request)
    verfuegbar = _quellen()
    if quelle not in verfuegbar:
        quelle = verfuegbar[0] if verfuegbar else ""
    ergebnis, fehler = None, request.query_params.get("fehler")
    if q.strip() and quelle:
        try:
            ergebnis = QUELLEN[quelle].suchen(q, max(1, seite))
        except API_FEHLER as e:
            logging.getLogger("wunschdruck").warning("%s-Suche: %s", quelle, e)
            fehler = "ratelimit" if e.ratelimit else "api"
    return vorlagen.TemplateResponse(request, "suche.html", {
        "t": i18n.t(sp), "sprache": sp, "q": q.strip(), "seite": max(1, seite),
        "ergebnis": ergebnis, "fehler": fehler,
        "quelle": quelle, "quellen": verfuegbar, "quellen_namen": QUELLEN_NAMEN,
    })


@app.get("/suche/{quelle}/{modell_id}")
def modell_seite(request: Request, quelle: str, modell_id: str, q: str = ""):
    sp = sprache(request)
    modul = QUELLEN.get(quelle)
    if not modul or not modul.konfiguriert() or not modell_id.isdigit():
        return RedirectResponse("/suche", status_code=303)
    try:
        info = modul.modell(modell_id)
        dateien = modul.dateien(modell_id)
        bilder = info["bilder"] if "bilder" in info else modul.bilder(modell_id)
    except API_FEHLER as e:
        logging.getLogger("wunschdruck").warning("%s-Modell %s: %s", quelle, modell_id, e)
        return RedirectResponse(f"/suche?quelle={quelle}&q={quote(q)}&fehler={'ratelimit' if e.ratelimit else 'api'}",
                                status_code=303)
    return vorlagen.TemplateResponse(request, "thing.html", {
        "t": i18n.t(sp), "sprache": sp, "q": q, "info": info, "dateien": dateien,
        "bilder": bilder, "quelle": quelle, "quelle_name": QUELLEN_NAMEN[quelle],
        "slots": db.slots(), "eigene_profile": slicer.eigene_profile(),
    })


@app.post("/suche/{quelle}/{modell_id}/datei/{datei_id}")
async def modell_datei_job(request: Request, quelle: str, modell_id: str, datei_id: str):
    """Datei vom Portal in einen neuen Job laden und slicen. Der Job entsteht
    vor dem Download, damit ein Fehler sichtbar an der Karte landet."""
    modul = QUELLEN.get(quelle)
    if not modul or not modul.konfiguriert() or not (modell_id.isdigit() and datei_id.isdigit()):
        return RedirectResponse("/suche", status_code=303)
    felder = _job_einstellungen(await request.form())
    job_id = db.job_anlegen(f"{quelle}-{modell_id}-{datei_id}", **felder)
    ordner = worker.job_dir(job_id)
    ordner.mkdir(parents=True, exist_ok=True)
    try:
        _, name = modul.herunterladen(modell_id, datei_id, ordner)
    except Exception as e:
        logging.getLogger("wunschdruck").exception("%s-Download fehlgeschlagen", quelle)
        db.job_update(job_id, status="fehler", fehler_text=f"{QUELLEN_NAMEN[quelle]}: {e}"[:2000])
        return RedirectResponse("/", status_code=303)
    db.job_update(job_id, name=Path(name).name)
    worker.slice_schlange.put(job_id)
    return RedirectResponse("/", status_code=303)


@app.get("/historie")
def historie(request: Request):
    sp = sprache(request)
    eintraege = moonraker.historie() if moonraker.konfiguriert() else None
    return vorlagen.TemplateResponse(request, "historie.html", {
        "t": i18n.t(sp), "sprache": sp, "eintraege": eintraege,
    })


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(Path(__file__).parent / "statisch" / "manifest.webmanifest",
                        media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(Path(__file__).parent / "statisch" / "sw.js",
                        media_type="text/javascript")


@app.get("/sprache/{neue}")
def sprache_setzen(neue: str):
    antwort = RedirectResponse("/", status_code=303)
    if neue in ("de", "en"):
        antwort.set_cookie("sprache", neue, max_age=86400 * 365)
    return antwort


@app.get("/api/status")
def api_status():
    """Kompakter Zustands-Hash fuers Auto-Neuladen der Seite."""
    jobs = [(z["id"], z["status"]) for z in db.jobs_alle()]
    return JSONResponse({"stand": hash(tuple(jobs)) & 0xFFFFFFFF,
                         "aktiv": any(s in AKTIVE_STATUS for _, s in jobs)})
