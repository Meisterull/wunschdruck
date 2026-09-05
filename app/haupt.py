"""Wunschdruck — Web-Oberflaeche (FastAPI + Jinja2, bewusst schlicht und handy-tauglich)."""
import contextlib
import json
import logging
import re
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, i18n, konfig, moonraker, slicer, vorschau, worker

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
    verbrauch_jobs = {}
    mehrfarb_jobs = set()
    for z in jobs:
        if z["verbrauch"]:
            werte = json.loads(z["verbrauch"])
            if werte and sum(1 for w in werte if w > 0) > 1:
                verbrauch_jobs[z["id"]] = werte
        if z["farben"] and len(json.loads(z["farben"]).get("farben", [])) >= 2:
            mehrfarb_jobs.add(z["id"])
    return vorlagen.TemplateResponse(request, "index.html", {
        "t": i18n.t(sp), "sprache": sp,
        "jobs": jobs, "slots": db.slots(),
        "drucker": drucker_status(),
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
                    deckschichten: str = Form(""), bodenschichten: str = Form("")):
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
                            bodenschichten=_opt_int(bodenschichten, 0, 15))
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
def einstellungen(request: Request, gespeichert: bool = False, profilfehler: bool = False):
    sp = sprache(request)
    return vorlagen.TemplateResponse(request, "einstellungen.html", {
        "t": i18n.t(sp), "sprache": sp, "slots": db.slots(),
        "lager": db.lager(), "gespeichert": gespeichert,
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
