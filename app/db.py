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
        _lokal.conn = c
    return _lokal.conn


def job_anlegen(name: str, slot: int, qualitaet: str, stuetzen: bool, platte: int) -> str:
    job_id = uuid.uuid4().hex[:8]
    with _conn() as c:
        c.execute(
            "INSERT INTO jobs (id,name,status,slot,qualitaet,stuetzen,platte,erstellt) VALUES (?,?,?,?,?,?,?,?)",
            (job_id, name, "neu", slot, qualitaet, int(stuetzen), platte, time.time()),
        )
    return job_id


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
