"""Printables-Client: GraphQL-Antworten parsen — python3 -m unittest discover tests"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import printables  # noqa: E402

BILD = "media/prints/1/images/2_ab/hero.png"


class Printables(unittest.TestCase):
    def test_suche_treffer_und_blaettern(self):
        item = {"id": 7, "name": "Clip", "slug": "clip", "user": {"publicUsername": "Anna", "handle": "Anna_1"},
                "image": {"filePath": BILD}}
        with mock.patch.object(printables, "_gql", return_value={"result": {"items": [item, {"kaputt": 1}]}}) as g:
            e = printables.suchen("clip", seite=2)
        self.assertEqual(g.call_args.args[2]["offset"], 24)
        self.assertEqual(e["treffer"], [{
            "id": "7", "name": "Clip", "autor": "Anna", "url": "https://www.printables.com/model/7-clip",
            "bild": "https://media.printables.com/media/prints/1/images/2_ab/thumbs/inside/320x240/png/hero.webp"}])

    def test_dateien_nur_stl_3mf_aus_modelldateien(self):
        roh = {"model": {"stls": [{"id": 1, "name": "ordner/Teil.STL", "fileSize": 10},
                                  {"id": 2, "name": "projekt.3mf"}, {"id": 3, "name": "quelle.f3d"}, {"name": "ohne-id.stl"}],
                         "otherFiles": [{"id": 4, "name": "anleitung.pdf"}, {"id": 5, "name": "alt.3mf"}]}}
        with mock.patch.object(printables, "_gql", return_value=roh):
            d = printables.dateien(5)
        self.assertEqual([(x["id"], x["name"], x["druckbar"]) for x in d],
                         [("1", "Teil.STL", True), ("2", "projekt.3mf", True), ("3", "quelle.f3d", False),
                          ("4", "anleitung.pdf", False), ("5", "alt.3mf", False)])

    def test_modell_beschreibung_lizenz_bilder(self):
        roh = {"model": {"id": 5, "name": "X", "slug": "x", "description": "<p>Hallo &amp; <b>Welt</b></p>",
                         "license": {"name": "Creative Commons — Attribution", "abbreviation": "CC-BY"},
                         "user": {"publicUsername": "B", "handle": "B_9"},
                         "image": {"filePath": BILD}, "images": [{"filePath": BILD}, {"kaputt": 1}]}}
        with mock.patch.object(printables, "_gql", return_value=roh):
            info = printables.modell(5)
        self.assertEqual(info["beschreibung"], "Hallo & Welt")
        self.assertEqual((info["lizenz"], info["autor_url"]), ("CC-BY", "https://www.printables.com/@B_9"))
        self.assertEqual(info["bilder"], [
            "https://media.printables.com/media/prints/1/images/2_ab/thumbs/inside/1280x960/png/hero.webp"])
        with mock.patch.object(printables, "_gql", return_value={"model": None}):
            with self.assertRaises(printables.ApiFehler):
                printables.modell(6)

    def test_herunterladen_lehnt_nicht_druckbares_ab(self):
        with mock.patch.object(printables, "_gql", return_value={"model": {"stls": [{"id": 2, "name": "a.f3d"}]}}):
            with self.assertRaises(printables.ApiFehler):
                printables.herunterladen(5, 2, Path("/tmp"))

    def test_herunterladen_ohne_link_ist_fehler(self):
        antworten = [{"model": {"stls": [{"id": 2, "name": "a.stl"}]}},
                     {"getDownloadLink": {"ok": False, "errors": [{"field": "id", "messages": ["nope"]}], "output": None}}]
        with mock.patch.object(printables, "_gql", side_effect=antworten):
            with self.assertRaises(printables.ApiFehler) as cm:
                printables.herunterladen(5, 2, Path("/tmp"))
        self.assertIn("nope", str(cm.exception))

    def test_gql_fehler_und_ratelimit(self):
        antwort = mock.Mock(status_code=200)
        antwort.json.return_value = {"errors": [{"message": "Feld weg"}], "data": None}
        with mock.patch.object(printables.httpx, "post", return_value=antwort):
            with self.assertRaises(printables.ApiFehler) as cm:
                printables._gql("X", "query", {})
        self.assertIn("Feld weg", str(cm.exception))
        with mock.patch.object(printables.httpx, "post", return_value=mock.Mock(status_code=429)):
            with self.assertRaises(printables.ApiFehler) as cm:
                printables._gql("X", "query", {})
        self.assertTrue(cm.exception.ratelimit)

    def test_bild_ohne_endung_bleibt_roh(self):
        self.assertEqual(printables._bild("media/x/hero"), "https://media.printables.com/media/x/hero")
        self.assertEqual(printables._bild(""), "")


if __name__ == "__main__":
    unittest.main()
