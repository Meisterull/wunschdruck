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
CREATE TABLE IF NOT EXISTS rollen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material TEXT NOT NULL,
    farbe TEXT NOT NULL DEFAULT '',
    hex TEXT NOT NULL DEFAULT '#9e9e9e',
    hersteller TEXT NOT NULL DEFAULT '',
    netto_g REAL,
    rest_g REAL,
    kaufdatum TEXT NOT NULL DEFAULT '',
    notiz TEXT NOT NULL DEFAULT '',
    archiviert INTEGER NOT NULL DEFAULT 0,
    erstellt REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rollen_verlauf (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rolle_id INTEGER NOT NULL,
    zeit REAL NOT NULL,
    art TEXT NOT NULL,
    gramm REAL NOT NULL,
    anteil REAL,
    job_name TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS verbuchungen (
    moonraker_job TEXT PRIMARY KEY,
    zeit REAL NOT NULL,
    job_id TEXT NOT NULL DEFAULT ''
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
                       "bodenschichten INTEGER", "filament_mm REAL"):
            try:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {spalte}")
            except sqlite3.OperationalError:
                pass  # Spalte existiert schon
        _lokal.conn = c
        _lager_migrieren()
    return _lokal.conn


def _lager_migrieren() -> None:
    """Altes Farb-Lager ({material,name,hex}) einmalig in Rollen ueberfuehren
    (Rest unbekannt) und die Slots auf die passenden Rollen zeigen lassen."""
    if einstellung("lager_migriert", False):
        return
    alt = einstellung("lager", [])
    slots_alt = einstellung("slots", SLOTS_STANDARD)
    with _conn() as c:
        for eintrag in alt:
            cur = c.execute(
                "INSERT INTO rollen (material,farbe,hex,erstellt) VALUES (?,?,?,?)",
                (eintrag.get("material", "PLA"), eintrag.get("name", ""),
                 eintrag.get("hex") or "#9e9e9e", time.time()))
            for s in slots_alt:
                if (s.get("material") == eintrag.get("material")
                        and s.get("farbe") == eintrag.get("name")
                        and s.get("hex") == eintrag.get("hex")
                        and not s.get("rolle_id")):
                    s["rolle_id"] = cur.lastrowid
    einstellung_setzen("slots", slots_alt)
    einstellung_setzen("lager_migriert", True)


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
    """Die 4 Drucker-Slots: Kopie der Rollendaten (material, farbe, hex) plus
    rolle_id — die Kopie haelt den Slot gueltig, auch wenn die Rolle spaeter
    archiviert oder geloescht wird."""
    return [{"material": s.get("material", "leer"), "farbe": s.get("farbe", ""),
             "hex": s.get("hex", ""), "rolle_id": s.get("rolle_id")}
            for s in einstellung("slots", SLOTS_STANDARD)]


# ---------------------------------------------------------------------------
# Rollen-Lager: jede Filamentrolle ein Eintrag mit Restmenge und Verlauf
# ---------------------------------------------------------------------------

ROLLEN_FELDER = ("material", "farbe", "hex", "hersteller", "netto_g", "rest_g",
                 "kaufdatum", "notiz")


def rollen(archiv: bool = False):
    return _conn().execute(
        "SELECT * FROM rollen WHERE archiviert=? ORDER BY material, farbe, erstellt",
        (int(archiv),)).fetchall()


def rolle(rolle_id: int):
    return _conn().execute("SELECT * FROM rollen WHERE id=?", (rolle_id,)).fetchone()


def rollen_map() -> dict[int, sqlite3.Row]:
    return {r["id"]: r for r in _conn().execute("SELECT * FROM rollen")}


def rolle_anlegen(**felder) -> int:
    werte = {k: felder.get(k) for k in ROLLEN_FELDER}
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO rollen (material,farbe,hex,hersteller,netto_g,rest_g,kaufdatum,"
            "notiz,erstellt) VALUES (?,?,?,?,?,?,?,?,?)",
            (werte["material"], werte["farbe"] or "", werte["hex"] or "#9e9e9e",
             werte["hersteller"] or "", werte["netto_g"], werte["rest_g"],
             werte["kaufdatum"] or "", werte["notiz"] or "", time.time()))
        rolle_id = cur.lastrowid
        if werte["rest_g"] is not None:
            c.execute("INSERT INTO rollen_verlauf (rolle_id,zeit,art,gramm) VALUES (?,?,?,?)",
                      (rolle_id, time.time(), "neu", werte["rest_g"]))
    return rolle_id


def rolle_update(rolle_id: int, **felder) -> None:
    """Stammdaten aendern; ein geaenderter Rest wird als Korrektur verbucht,
    Slot-Kopien dieser Rolle werden nachgezogen."""
    alt = rolle(rolle_id)
    if alt is None:
        return
    felder = {k: v for k, v in felder.items() if k in ROLLEN_FELDER or k == "archiviert"}
    with _conn() as c:
        if (felder.get("rest_g") is not None
                and abs(felder["rest_g"] - (alt["rest_g"] or 0)) >= 0.05):
            delta = felder["rest_g"] - (alt["rest_g"] or 0)
            c.execute("INSERT INTO rollen_verlauf (rolle_id,zeit,art,gramm) VALUES (?,?,?,?)",
                      (rolle_id, time.time(), "korrektur", delta))
        if felder:
            zuweisung = ",".join(f"{k}=?" for k in felder)
            c.execute(f"UPDATE rollen SET {zuweisung} WHERE id=?", (*felder.values(), rolle_id))
    neu = rolle(rolle_id)
    slots_alt = einstellung("slots", SLOTS_STANDARD)
    geaendert = False
    for s in slots_alt:
        if s.get("rolle_id") == rolle_id:
            s.update({"material": neu["material"], "farbe": neu["farbe"], "hex": neu["hex"]})
            geaendert = True
    if geaendert:
        einstellung_setzen("slots", slots_alt)


def rolle_loeschen(rolle_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM rollen_verlauf WHERE rolle_id=?", (rolle_id,))
        c.execute("DELETE FROM rollen WHERE id=?", (rolle_id,))


def rolle_abziehen(rolle_id: int, gramm: float, art: str, anteil: float | None,
                   job_name: str) -> None:
    """Verbrauch buchen. Rest unbekannt (NULL) bleibt unbekannt, der Verlauf
    wird trotzdem geschrieben; bei Rest <= 0 wandert die Rolle ins Archiv."""
    zeile = rolle(rolle_id)
    if zeile is None or gramm <= 0:
        return
    with _conn() as c:
        c.execute("INSERT INTO rollen_verlauf (rolle_id,zeit,art,gramm,anteil,job_name)"
                  " VALUES (?,?,?,?,?,?)",
                  (rolle_id, time.time(), art, -gramm, anteil, job_name))
        if zeile["rest_g"] is not None:
            rest = max(0.0, zeile["rest_g"] - gramm)
            c.execute("UPDATE rollen SET rest_g=?, archiviert=? WHERE id=?",
                      (rest, int(rest <= 0), rolle_id))


def verlauf(rolle_id: int, limit: int = 30):
    return _conn().execute(
        "SELECT * FROM rollen_verlauf WHERE rolle_id=? ORDER BY zeit DESC LIMIT ?",
        (rolle_id, limit)).fetchall()


def verbucht(moonraker_job: str) -> bool:
    return _conn().execute("SELECT 1 FROM verbuchungen WHERE moonraker_job=?",
                           (moonraker_job,)).fetchone() is not None


def verbuchen(moonraker_job: str, job_id: str) -> None:
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO verbuchungen (moonraker_job,zeit,job_id) VALUES (?,?,?)",
                  (moonraker_job, time.time(), job_id))
