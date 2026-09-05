"""Wunschdruck — Web-Oberflaeche (FastAPI + Jinja2, bewusst schlicht und handy-tauglich)."""
import contextlib
import json
import logging
import re
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, i18n, konfig, moonraker, vorschau, worker

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


def sprache(request: Request) -> str:
    return request.cookies.get("sprache", konfig.SPRACHE_STANDARD)


def drucker_status() -> str:
    if not moonraker.konfiguriert():
        return "unkonfiguriert"
    zustand = moonraker.zustand(timeout=2)
    if zustand == "offline":
        return "offline"
    return "druckt" if zustand in ("printing", "paused") else "frei"


@app.get("/")
def index(request: Request):
    sp = sprache(request)
    jobs = db.jobs_alle()
    farben_jobs = {z["id"]: json.loads(z["farben"])
                   for z in jobs if z["status"] == "farbwahl" and z["farben"]}
    return vorlagen.TemplateResponse(request, "index.html", {
        "t": i18n.t(sp), "sprache": sp,
        "jobs": jobs, "slots": db.slots(),
        "drucker": drucker_status(),
        "aktive_status": AKTIVE_STATUS,
        "farben_jobs": farben_jobs,
    })


@app.post("/hochladen")
async def hochladen(request: Request, datei: UploadFile = File(...),
                    slot: int = Form(1), qualitaet: str = Form("standard"),
                    stuetzen: str = Form("auto"), platte: int = Form(1)):
    endung = Path(datei.filename or "").suffix.lower()
    if endung not in ERLAUBTE_ENDUNGEN or qualitaet not in ("fein", "standard", "schnell"):
        return RedirectResponse("/?fehler=format", status_code=303)
    slot = min(max(slot, 1), 4)
    job_id = db.job_anlegen(Path(datei.filename).name, slot, qualitaet,
                            stuetzen == "auto", max(platte, 1))
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
            slot = min(nr, 4)  # feine Bemalung: Zuordnung bleibt fest 1:1
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
def einstellungen(request: Request, gespeichert: bool = False):
    sp = sprache(request)
    return vorlagen.TemplateResponse(request, "einstellungen.html", {
        "t": i18n.t(sp), "sprache": sp, "slots": db.slots(),
        "lager": db.lager(), "gespeichert": gespeichert,
    })


def _hex_sicher(wert: str) -> str:
    wert = str(wert).strip()
    return wert if re.fullmatch(r"#[0-9a-fA-F]{6}", wert) else "#9e9e9e"


@app.post("/einstellungen/lager")
async def lager_anlegen(request: Request):
    """Filament ins Lager legen (Material + Farbname + Farbe)."""
    formular = await request.form()
    material = str(formular.get("material", "PLA"))
    if material not in ("PLA", "PETG", "TPU"):
        material = "PLA"
    name = str(formular.get("name", "")).strip()[:40]
    eintrag = {"material": material, "name": name,
               "hex": _hex_sicher(formular.get("hex", ""))}
    lager = db.lager()
    lager.append(eintrag)
    db.einstellung_setzen("lager", lager)
    return RedirectResponse("/einstellungen", status_code=303)


@app.post("/einstellungen/lager/{index}/loeschen")
def lager_loeschen(index: int):
    lager = db.lager()
    if 0 <= index < len(lager):
        lager.pop(index)
        db.einstellung_setzen("lager", lager)
    return RedirectResponse("/einstellungen", status_code=303)


@app.post("/einstellungen")
async def einstellungen_speichern(request: Request):
    """Slots aus dem Lager bestücken. Der gewählte Lager-Eintrag wird in den
    Slot KOPIERT — löscht man ihn später aus dem Lager, bleibt der Slot gültig."""
    formular = await request.form()
    lager = db.lager()
    slots = []
    for i in range(1, 5):
        wahl = str(formular.get(f"slot{i}", "leer"))
        if wahl.isdigit() and int(wahl) < len(lager):
            eintrag = lager[int(wahl)]
            slots.append({"material": eintrag["material"],
                          "farbe": eintrag["name"], "hex": eintrag["hex"]})
        else:
            slots.append({"material": "leer", "farbe": "", "hex": ""})
    db.einstellung_setzen("slots", slots)
    return RedirectResponse("/einstellungen?gespeichert=1", status_code=303)


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
