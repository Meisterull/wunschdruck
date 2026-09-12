"""Thingiverse-Client: Antwortformen parsen — python3 -m unittest discover tests"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import thingiverse  # noqa: E402


class Thingiverse(unittest.TestCase):
    def test_suche_beide_antwortformen(self):
        hit = {"id": 7, "name": "Clip", "thumbnail": "t.jpg", "creator": {"name": "Anna"}, "public_url": "u"}
        with mock.patch.object(thingiverse, "_get", return_value={"total": 1, "hits": [hit, {"kaputt": 1}]}):
            e = thingiverse.suchen("clip")
        self.assertEqual(e["gesamt"], 1)
        self.assertEqual(e["treffer"], [{"id": 7, "name": "Clip", "bild": "t.jpg", "autor": "Anna", "url": "u"}])
        with mock.patch.object(thingiverse, "_get", return_value=[hit]):
            self.assertEqual(len(thingiverse.suchen("clip")["treffer"]), 1)

    def test_dateien_druckbar_erkennen(self):
        roh = [{"id": 1, "name": "files/Teil.STL", "size": 10, "download_url": "d"},
               {"id": 2, "name": "anleitung.pdf"}, {"name": "ohne-id.stl"}]
        with mock.patch.object(thingiverse, "_get", return_value=roh):
            d = thingiverse.dateien(5)
        self.assertEqual([(x["name"], x["druckbar"]) for x in d], [("Teil.STL", True), ("anleitung.pdf", False)])

    def test_bilder_bevorzugen_display_medium(self):
        roh = [{"sizes": [{"type": "thumb", "size": "small", "url": "s"},
                          {"type": "display", "size": "medium", "url": "m"}]},
               {"sizes": [{"type": "preview", "size": "large", "url": "p"}]},
               {"sizes": []}]
        with mock.patch.object(thingiverse, "_get", return_value=roh):
            self.assertEqual(thingiverse.bilder(5), ["m", "p"])

    def test_thing_beschreibung_ohne_html(self):
        roh = {"id": 5, "name": "X", "description": "<p>Hallo &amp; <b>Welt</b></p>", "license": "CC-BY",
               "creator": {"name": "B", "public_url": "cu"}, "default_image": {"url": "big"}}
        with mock.patch.object(thingiverse, "_get", return_value=roh):
            info = thingiverse.thing(5)
        self.assertEqual(info["beschreibung"], "Hallo & Welt")
        self.assertEqual((info["lizenz"], info["autor_url"], info["bild_gross"]), ("CC-BY", "cu", "big"))

    def test_herunterladen_lehnt_pdf_ab(self):
        with mock.patch.object(thingiverse, "_get", return_value=[{"id": 2, "name": "a.pdf"}]):
            with self.assertRaises(thingiverse.ApiFehler):
                thingiverse.herunterladen(5, 2, Path("/tmp"))


if __name__ == "__main__":
    unittest.main()
