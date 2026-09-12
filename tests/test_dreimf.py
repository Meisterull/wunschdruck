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


if __name__ == "__main__":
    unittest.main()
