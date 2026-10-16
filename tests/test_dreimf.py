"""Paint-Code-Codec (TriangleSelector-Bitstrom) — python3 -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import dreimf  # noqa: E402


class PaintCodes(unittest.TestCase):
    def test_blatt_codes_wie_im_slicer(self):
        # Rückwärts gelesene Nibbles: Zustand 1/2 direkt, ab 3 erweitert ("0C", "1C" …)
        for filament, code in [(1, "4"), (2, "8"), (3, "0C"), (4, "1C"), (5, "2C"), (16, "DC")]:
            self.assertEqual(dreimf._code_schreiben(filament), code)
            self.assertEqual(dreimf._uniform_index(code), filament)

    def test_naives_filament_mal_vier_ist_unbemalt(self):
        # "10" (= 4*4) war der alte Fehler: der Slicer liest Zustand 0 → Farbe weg.
        self.assertIsNone(dreimf._uniform_index("10"))
        self.assertEqual(dreimf.filamente_im_code("10"), set())

    def test_ummappen_blatt(self):
        self.assertEqual(dreimf.code_ummappen("8", {2: 4}), "1C")
        self.assertEqual(dreimf.code_ummappen("8", {1: 3}), "8")  # nicht betroffen

    def test_baum_rundreise_und_ummappen(self):
        baum = (2, [2, (0, [1, 0, 0, 0]), 0])  # geteilt, ein Kind selbst geteilt
        code = dreimf._code_schreiben(baum)
        self.assertEqual(dreimf._code_lesen(code), baum)
        self.assertEqual(dreimf.filamente_im_code(code), {1, 2})
        neu = dreimf.code_ummappen(code, {1: 3, 2: 4})
        self.assertEqual(dreimf._code_lesen(neu), (2, [4, (0, [3, 0, 0, 0]), 0]))

    def test_unlesbar_bleibt_unveraendert(self):
        self.assertIsNone(dreimf.filamente_im_code("C"))  # erweitert ohne Folgebits
        self.assertEqual(dreimf.code_ummappen("C", {3: 1}), "C")


def _projekt_3mf(pfad, config, objekt_xml=None, items='<item objectid="1"/>'):
    """Mini-3MF: ein Dreieck als Objekt 1, optional model_settings.config."""
    import zipfile
    objekt_xml = objekt_xml or ('<object id="1" type="model"><mesh><vertices>'
                                '<vertex x="0" y="0" z="0"/><vertex x="10" y="0" z="0"/>'
                                '<vertex x="0" y="10" z="5"/></vertices><triangles>'
                                '<triangle v1="0" v2="1" v3="2"/></triangles></mesh></object>')
    modell = ('<?xml version="1.0"?><model unit="millimeter" '
              'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
              f'<resources>{objekt_xml}</resources><build>{items}</build></model>')
    with zipfile.ZipFile(pfad, "w") as z:
        z.writestr("3D/3dmodel.model", modell)
        if config is not None:
            z.writestr("Metadata/model_settings.config", config)
    return pfad


class Einfarbig(unittest.TestCase):
    """CLI lehnt 3MF + --load-filament-ids/--clone-objects ab (16.10.2026)."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())

    def _config(self, pfad):
        import zipfile
        return zipfile.ZipFile(pfad).read("Metadata/model_settings.config").decode()

    def test_slot_wird_eingebettet(self):
        cfg = ('<config><object id="1"><metadata key="name" value="a"/>'
               '<metadata key="extruder" value="1"/><part id="1" subtype="normal_part">'
               '<metadata key="extruder" value="1"/></part></object></config>')
        q = _projekt_3mf(self.tmp / "q.3mf", cfg)
        pfad, eingebettet = dreimf.einfarbig_vorbereiten(q, self.tmp, 3, 1)
        self.assertTrue(eingebettet)
        self.assertEqual(pfad.suffix, ".3mf")
        self.assertEqual(dreimf._EXTRUDER_RE.findall(self._config(pfad)), ["3", "3"])

    def test_fehlender_objekt_extruder_wird_ergaenzt(self):
        cfg = ('<config><object id="1"><metadata key="name" value="a"/>'
               '<part id="1" subtype="normal_part"><metadata key="name" value="t"/></part></object></config>')
        q = _projekt_3mf(self.tmp / "q.3mf", cfg)
        pfad, _ = dreimf.einfarbig_vorbereiten(q, self.tmp, 2, 1)
        self.assertIn('<object id="1"><metadata key="extruder" value="2"/>', self._config(pfad))

    def test_slot1_bleibt_3mf_ohne_einbetten(self):
        q = _projekt_3mf(self.tmp / "q.3mf", "<config></config>")
        pfad, eingebettet = dreimf.einfarbig_vorbereiten(q, self.tmp, 1, 1)
        self.assertEqual((pfad.suffix, eingebettet), (".3mf", False))

    def test_ohne_orca_config_wird_stl(self):
        q = _projekt_3mf(self.tmp / "q.3mf", None)
        pfad, eingebettet = dreimf.einfarbig_vorbereiten(q, self.tmp, 2, 1)
        self.assertEqual((pfad.suffix, eingebettet), (".stl", False))
        self.assertEqual(len(dreimf._stl_lesen(pfad)), 1)

    def test_stueckzahl_wird_stl(self):
        q = _projekt_3mf(self.tmp / "q.3mf", "<config></config>")
        pfad, _ = dreimf.einfarbig_vorbereiten(q, self.tmp, 1, 3)
        self.assertEqual(pfad.suffix, ".stl")

    def test_stueckzahl_mit_modifier_klare_meldung(self):
        cfg = ('<config><object id="1"><part id="1" subtype="normal_part"/>'
               '<part id="2" subtype="modifier_part"/></object></config>')
        q = _projekt_3mf(self.tmp / "q.3mf", cfg)
        with self.assertRaises(ValueError):
            dreimf.einfarbig_vorbereiten(q, self.tmp, 1, 2)


if __name__ == "__main__":
    unittest.main()
