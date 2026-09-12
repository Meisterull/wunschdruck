"""Rollen-Lager: Migration, Abzug, Verbuchung — python3 -m unittest discover tests"""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import konfig  # noqa: E402

_tmp = tempfile.TemporaryDirectory()
konfig.DATEN_DIR = Path(_tmp.name)
konfig.DB_PFAD = konfig.DATEN_DIR / "test.db"
from app import db, lager  # noqa: E402


class RollenLager(unittest.TestCase):
    def setUp(self):
        db._lokal = threading.local()
        konfig.DB_PFAD.unlink(missing_ok=True)
        for endung in ("-wal", "-shm"):
            Path(str(konfig.DB_PFAD) + endung).unlink(missing_ok=True)

    def test_migration_alt_lager_zu_rollen(self):
        with db._conn() as c:  # Schema ohne Migration ueberspringen: Flag zuruecksetzen
            c.execute("DELETE FROM einstellungen")
        db.einstellung_setzen("lager", [{"material": "PETG", "name": "Schwarz", "hex": "#000000"},
                                        {"material": "PLA", "name": "Weiß", "hex": "#ffffff"}])
        db.einstellung_setzen("slots", [{"material": "PETG", "farbe": "Schwarz", "hex": "#000000"},
                                        {"material": "leer", "farbe": "", "hex": ""},
                                        {"material": "leer", "farbe": "", "hex": ""},
                                        {"material": "PLA", "farbe": "Weiß", "hex": "#ffffff"}])
        db._lager_migrieren()
        rollen = db.rollen()
        self.assertEqual([(r["material"], r["farbe"], r["rest_g"]) for r in rollen],
                         [("PETG", "Schwarz", None), ("PLA", "Weiß", None)])
        slots = db.slots()
        self.assertEqual(slots[0]["rolle_id"], rollen[0]["id"])
        self.assertEqual(slots[3]["rolle_id"], rollen[1]["id"])
        self.assertIsNone(slots[1]["rolle_id"])
        db._lager_migrieren()  # zweiter Lauf: keine Duplikate
        self.assertEqual(len(db.rollen()), 2)

    def test_abziehen_und_archiv(self):
        rid = db.rolle_anlegen(material="PLA", farbe="Rot", hex="#ff0000", netto_g=1000, rest_g=50)
        db.rolle_abziehen(rid, 20, "druck", 1.0, "a.stl")
        self.assertAlmostEqual(db.rolle(rid)["rest_g"], 30)
        self.assertEqual(db.rolle(rid)["archiviert"], 0)
        db.rolle_abziehen(rid, 45, "druck", 1.0, "b.stl")
        self.assertEqual(db.rolle(rid)["rest_g"], 0)
        self.assertEqual(db.rolle(rid)["archiviert"], 1)  # leer → Archiv
        arten = [e["art"] for e in db.verlauf(rid)]
        self.assertEqual(arten, ["druck", "druck", "neu"])

    def test_rest_unbekannt_bleibt_unbekannt(self):
        rid = db.rolle_anlegen(material="PLA", farbe="Blau", hex="#0000ff")
        db.rolle_abziehen(rid, 12.5, "druck", 1.0, "x.stl")
        self.assertIsNone(db.rolle(rid)["rest_g"])
        self.assertEqual(db.verlauf(rid)[0]["gramm"], -12.5)

    def test_korrektur_und_slotkopie(self):
        rid = db.rolle_anlegen(material="PLA", farbe="Grün", hex="#00ff00", rest_g=500)
        db.einstellung_setzen("slots", [{"material": "PLA", "farbe": "Grün", "hex": "#00ff00", "rolle_id": rid}] +
                              [{"material": "leer", "farbe": "", "hex": ""}] * 3)
        db.rolle_update(rid, rest_g=480.0, farbe="Grasgrün")
        self.assertEqual(db.verlauf(rid)[0]["art"], "korrektur")
        self.assertAlmostEqual(db.verlauf(rid)[0]["gramm"], -20)
        self.assertEqual(db.slots()[0]["farbe"], "Grasgrün")
        db.rolle_update(rid, rest_g=480.0, notiz="x")  # unveraenderter Rest → keine Korrektur
        self.assertEqual(db.verlauf(rid)[0]["art"], "korrektur")
        self.assertEqual(len(db.verlauf(rid)), 2)

    def _job_mit_verbrauch(self, verbrauch, mm):
        job_id = db.job_anlegen("Caliper.3mf", 1, "standard", True, 1)
        db.job_update(job_id, status="bereit", verbrauch=json.dumps(verbrauch), filament_mm=mm)
        return job_id

    def test_historie_verbuchen_einmalig_und_anteilig(self):
        r1 = db.rolle_anlegen(material="PETG", farbe="Schwarz", hex="#000000", rest_g=500)
        r4 = db.rolle_anlegen(material="PETG", farbe="Weiß", hex="#ffffff", rest_g=100)
        db.einstellung_setzen("slots", [
            {"material": "PETG", "farbe": "Schwarz", "hex": "#000000", "rolle_id": r1},
            {"material": "leer", "farbe": "", "hex": ""}, {"material": "leer", "farbe": "", "hex": ""},
            {"material": "PETG", "farbe": "Weiß", "hex": "#ffffff", "rolle_id": r4}])
        job_id = self._job_mit_verbrauch([40.0, 0, 0, 1.0, 0, 0, 0], 13000)
        datei = f"wunschdruck_{job_id}_Caliper.3mf.gcode"
        eintraege = [
            {"job_id": "0001AA", "filename": datei, "status": "completed", "filament_used": 13000},
            {"job_id": "0002AA", "filename": datei, "status": "cancelled", "filament_used": 6500},
            {"job_id": "0003AA", "filename": "fremd.gcode", "status": "completed", "filament_used": 1},
            {"job_id": "0004AA", "filename": datei, "status": "in_progress", "filament_used": 10},
        ]
        self.assertEqual(lager.historie_verbuchen(eintraege), 2)
        self.assertAlmostEqual(db.rolle(r1)["rest_g"], 500 - 40 - 20)
        self.assertAlmostEqual(db.rolle(r4)["rest_g"], 100 - 1 - 0.5)
        self.assertEqual(lager.historie_verbuchen(eintraege), 0)  # idempotent
        self.assertAlmostEqual(db.rolle(r1)["rest_g"], 440)

    def test_abbruch_fallback_auf_fortschritt(self):
        r1 = db.rolle_anlegen(material="PLA", farbe="Rot", hex="#ff0000", rest_g=100)
        db.einstellung_setzen("slots", [{"material": "PLA", "farbe": "Rot", "hex": "#ff0000", "rolle_id": r1}] +
                              [{"material": "leer", "farbe": "", "hex": ""}] * 3)
        job_id = self._job_mit_verbrauch([10.0], None)  # keine Gesamtlaenge bekannt
        datei = f"wunschdruck_{job_id}_x.stl.gcode"
        lager.fortschritt_merken(datei, 0.25)
        lager.historie_verbuchen([{"job_id": "0009", "filename": datei, "status": "cancelled"}])
        self.assertAlmostEqual(db.rolle(r1)["rest_g"], 97.5)

    def test_geloeschter_job_wird_markiert(self):
        eintrag = {"job_id": "0042", "filename": "wunschdruck_deadbeef_weg.stl.gcode", "status": "completed"}
        self.assertFalse(lager.eintrag_verbuchen(eintrag, db.slots()))
        self.assertTrue(db.verbucht("0042"))


if __name__ == "__main__":
    unittest.main()
