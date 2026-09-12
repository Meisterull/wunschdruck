"""Slot-Farben im G-Code-Konfigblock — python3 -m unittest discover tests"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import slicer  # noqa: E402

KOPF = (b"; HEADER_BLOCK_START\n; HEADER_BLOCK_END\n\nG1 X1 Y1 E1\n; CONFIG_BLOCK_START\n"
        b"; extruder_colour = #F2754E\n; filament_colour = #F2754E\n; filament_colour_type = 0\n"
        b"; filament_type = PETG\n; CONFIG_BLOCK_END\n")
MEHR = KOPF.replace(b"#F2754E\n; filament_colour = #F2754E",
                    b"#000000;#FFFFFF;#9B9EA0;#00000000;#56B7E6\n"
                    b"; filament_colour = #000000;#FFFFFF;#9B9EA0;#00000000;#56B7E6")


class SlotFarben(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.gcode = Path(self.tmp.name) / "plate_1.gcode"

    def tearDown(self):
        self.tmp.cleanup()

    def test_hex_normalisierung(self):
        self.assertEqual(slicer.hex_farbe("#ffffff"), "#FFFFFF")
        self.assertEqual(slicer.hex_farbe("00ff00"), "#00FF00")
        self.assertEqual(slicer.hex_farbe(""), slicer.STANDARD_SLOT_FARBE)
        self.assertEqual(slicer.hex_farbe("#00000000"), slicer.STANDARD_SLOT_FARBE)

    def test_einfarbig_behaelt_eintragszahl(self):
        self.gcode.write_bytes(KOPF)
        self.assertTrue(slicer.gcode_farben_setzen(self.gcode, ["#000000", "#ffffff", "", "#ffffff"]))
        text = self.gcode.read_bytes()
        self.assertIn(b"; filament_colour = #000000\n", text)
        self.assertIn(b"; extruder_colour = #000000\n", text)
        self.assertIn(b"; filament_colour_type = 0\n", text)  # nicht angefasst
        self.assertIn(b"G1 X1 Y1 E1\n", text)

    def test_mehrfarbig_erste_vier_rest_bleibt(self):
        self.gcode.write_bytes(MEHR)
        slicer.gcode_farben_setzen(self.gcode, ["#000000", "#ffffff", "", "#ffffff"])
        self.assertIn(b"; filament_colour = #000000;#FFFFFF;#808080;#FFFFFF;#56B7E6\n",
                      self.gcode.read_bytes())

    def test_idempotent(self):
        self.gcode.write_bytes(MEHR)
        farben = ["#000000", "#ffffff", "", "#ffffff"]
        self.assertTrue(slicer.gcode_farben_setzen(self.gcode, farben))
        self.assertFalse(slicer.gcode_farben_setzen(self.gcode, farben))


if __name__ == "__main__":
    unittest.main()
