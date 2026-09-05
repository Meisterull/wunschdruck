"""SQLite-Ablage fuer Auftraege und Einstellungen (ueberlebt Neustarts)."""
import json
import sqlite3
import threading
import time
import uuid

from . import konfig

_lokal = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    slot INTEGER NOT NULL DEFAULT 1,
    qualitaet TEXT NOT NULL DEFAULT 'standard',
    stuetzen INTEGER NOT NULL DEFAULT 1,
    platte INTEGER NOT NULL DEFAULT 1,
    dauer_s INTEGER,
    filament_g REAL,
    fehler_text TEXT,
    erstellt REAL NOT NULL,
    gestartet REAL
);
CREATE TABLE IF NOT EXISTS einstellungen (
    schluessel TEXT PRIMARY KEY,
    wert TEXT NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    if not hasattr(_lokal, "conn"):
        konfig.DATEN_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(konfig.DB_PFAD)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        # Nachrüst-Spalten für Bestands-Datenbanken
        for spalte in ("farben TEXT", "zuordnung TEXT",
                       "stuetz_slot INTEGER NOT NULL DEFAULT 0",
                       "ausrichtung TEXT NOT NULL DEFAULT 'auto'",
                       "anzahl INTEGER NOT NULL DEFAULT 1",
                       "fortschritt REAL", "verbrauch TEXT",
                       "fuellung INTEGER",
                       "stuetz_art TEXT NOT NULL DEFAULT 'profil'",
                       "waende INTEGER", "deckschichten INTEGER",
                       "bodenschichten INTEGER"):
            try:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {spalte}")
            except sqlite3.OperationalError:
                pass  # Spalte existiert schon
        _lokal.conn = c
    return _lokal.conn


def job_anlegen(name: str, slot: int, qualitaet: str, stuetzen: bool, platte: int,
                stuetz_slot: int = 0, ausrichtung: str = "auto", anzahl: int = 1,
                fuellung: int | None = None, stuetz_art: str = "profil",
                waende: int | None = None, deckschichten: int | None = None,
                bodenschichten: int | None = None) -> str:
    job_id = uuid.uuid4().hex[:8]
    with _conn() as c:
        c.execute(
            "INSERT INTO jobs (id,name,status,slot,qualitaet,stuetzen,platte,erstellt,"
            "stuetz_slot,ausrichtung,anzahl,fuellung,stuetz_art,waende,deckschichten,"
            "bodenschichten) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, name, "neu", slot, qualitaet, int(stuetzen), platte, time.time(),
             stuetz_slot, ausrichtung, anzahl, fuellung, stuetz_art,
             waende, deckschichten, bodenschichten),
        )
    return job_id


def jobs_abgelaufen(tage: int):
    """Abgeschlossene Jobs, deren letzter Stand älter als `tage` ist."""
    schwelle = time.time() - tage * 86400
    return _conn().execute(
        "SELECT id FROM jobs WHERE status IN ('fertig','verworfen','fehler')"
        " AND COALESCE(gestartet, erstellt) < ?", (schwelle,)).fetchall()


def job(job_id: str):
    return _conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def jobs_alle():
    return _conn().execute("SELECT * FROM jobs ORDER BY erstellt DESC LIMIT 200").fetchall()


def jobs_mit_status(*status: str):
    frage = ",".join("?" * len(status))
    return _conn().execute(
        f"SELECT * FROM jobs WHERE status IN ({frage}) ORDER BY erstellt", status
    ).fetchall()


def job_update(job_id: str, **felder):
    zuweisung = ",".join(f"{k}=?" for k in felder)
    with _conn() as c:
        c.execute(f"UPDATE jobs SET {zuweisung} WHERE id=?", (*felder.values(), job_id))


def job_loeschen(job_id: str):
    with _conn() as c:
        c.execute("DELETE FROM jobs WHERE id=?", (job_id,))


def einstellung(schluessel: str, standard):
    zeile = _conn().execute(
        "SELECT wert FROM einstellungen WHERE schluessel=?", (schluessel,)
    ).fetchone()
    return json.loads(zeile["wert"]) if zeile else standard


def einstellung_setzen(schluessel: str, wert):
    with _conn() as c:
        c.execute(
            "INSERT INTO einstellungen (schluessel,wert) VALUES (?,?) "
            "ON CONFLICT(schluessel) DO UPDATE SET wert=excluded.wert",
            (schluessel, json.dumps(wert)),
        )


SLOTS_STANDARD = [
    {"material": "PLA", "farbe": ""},
    {"material": "leer", "farbe": ""},
    {"material": "leer", "farbe": ""},
    {"material": "leer", "farbe": ""},
]


def slots() -> list[dict]:
    return einstellung("slots", SLOTS_STANDARD)


def lager() -> list[dict]:
    """Filament-Lager: was zuhause vorrätig ist ({material, name, hex})."""
    return einstellung("lager", [])
