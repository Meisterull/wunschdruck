"""Thingiverse-Anbindung ueber die offizielle REST-API.

Braucht ein App-Token (thingiverse.com/developers → App anlegen → "App Token")
in THINGIVERSE_TOKEN. Suche, Modellseite, Dateiliste und Download laufen
damit sauber und ohne Browser-Login; die API begrenzt auf ~300 Anfragen je
5 Minuten (HTTP 429 → ApiFehler.ratelimit).
"""
import html
import re
from pathlib import Path
from urllib.parse import quote

import httpx

from . import konfig

BASIS = "https://api.thingiverse.com"
DRUCKBAR = (".stl", ".3mf")


class ApiFehler(Exception):
    def __init__(self, meldung: str, ratelimit: bool = False):
        super().__init__(meldung)
        self.ratelimit = ratelimit


def konfiguriert() -> bool:
    return bool(konfig.THINGIVERSE_TOKEN)


def _kopf() -> dict:
    return {"Authorization": f"Bearer {konfig.THINGIVERSE_TOKEN}"}


def _get(pfad: str, params: dict | None = None, timeout: float = 15.0):
    try:
        r = httpx.get(f"{BASIS}{pfad}", params=params, headers=_kopf(), timeout=timeout)
    except httpx.HTTPError as fehler:
        raise ApiFehler(str(fehler)) from fehler
    if r.status_code == 429:
        raise ApiFehler("Rate-Limit der Thingiverse-API erreicht", ratelimit=True)
    if r.status_code >= 400:
        raise ApiFehler(f"Thingiverse HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as fehler:
        raise ApiFehler("keine JSON-Antwort") from fehler


def _text(wert) -> str:
    """HTML/Markdown-Beschreibung → schlichter Text."""
    roh = re.sub(r"<[^>]+>", " ", str(wert or ""))
    return " ".join(html.unescape(roh).split())


def _treffer(h: dict) -> dict:
    return {"id": h.get("id"), "name": h.get("name") or "",
            "bild": h.get("thumbnail") or "",
            "autor": (h.get("creator") or {}).get("name") or "",
            "url": h.get("public_url") or ""}


def suchen(begriff: str, seite: int = 1, pro_seite: int = 24) -> dict:
    """{treffer: [{id,name,bild,autor,url}], gesamt} — die API liefert je nach
    Version {"total","hits":[…]} oder eine nackte Liste."""
    daten = _get(f"/search/{quote(begriff.strip())}",
                 {"type": "things", "page": max(1, seite), "per_page": pro_seite,
                  "sort": "relevant"})
    roh = daten.get("hits", []) if isinstance(daten, dict) else daten
    gesamt = daten.get("total") if isinstance(daten, dict) else None
    return {"treffer": [_treffer(h) for h in (roh or []) if isinstance(h, dict) and h.get("id")],
            "gesamt": gesamt}


def thing(thing_id: int) -> dict:
    d = _get(f"/things/{int(thing_id)}")
    return {**_treffer(d),
            "lizenz": d.get("license") or "",
            "beschreibung": _text(d.get("description"))[:700],
            "autor_url": (d.get("creator") or {}).get("public_url") or "",
            "bild_gross": ((d.get("default_image") or {}).get("url")
                           or d.get("preview_image") or d.get("thumbnail") or "")}


def bilder(thing_id: int, maximal: int = 6) -> list[str]:
    roh = _get(f"/things/{int(thing_id)}/images")
    urls = []
    for b in roh if isinstance(roh, list) else []:
        groessen = [g for g in (b.get("sizes") or []) if g.get("url")]
        wahl = (next((g["url"] for g in groessen
                      if g.get("type") == "display" and g.get("size") == "medium"), None)
                or next((g["url"] for g in groessen if g.get("type") == "preview"), None)
                or (groessen[0]["url"] if groessen else None))
        if wahl:
            urls.append(wahl)
    return urls[:maximal]


def dateien(thing_id: int) -> list[dict]:
    roh = _get(f"/things/{int(thing_id)}/files")
    aus = []
    for f in roh if isinstance(roh, list) else []:
        if not isinstance(f, dict) or not f.get("id"):
            continue
        name = Path(str(f.get("name") or "")).name
        aus.append({"id": f["id"], "name": name, "groesse": f.get("size") or 0,
                    "druckbar": name.lower().endswith(DRUCKBAR),
                    "download_url": f.get("download_url") or "",
                    "bild": f.get("thumbnail") or ""})
    return aus


def herunterladen(thing_id: int, datei_id: int, ziel_dir: Path) -> tuple[Path, str]:
    """STL/3MF in ziel_dir/quelle.<endung> laden; liefert (Pfad, Originalname).
    Der Download-Endpunkt leitet auf das CDN weiter (httpx folgt und laesst
    das Bearer-Token bei Host-Wechsel weg) — antwortet er stattdessen mit
    JSON, steckt die eigentliche URL darin."""
    eintrag = next((d for d in dateien(thing_id) if str(d["id"]) == str(datei_id)), None)
    if not eintrag or not eintrag["druckbar"]:
        raise ApiFehler("Datei unbekannt oder kein STL/3MF")
    url = eintrag["download_url"] or f"{BASIS}/things/{int(thing_id)}/files/{int(datei_id)}/download"
    ziel = ziel_dir / f"quelle{Path(eintrag['name']).suffix.lower()}"
    _datei_laden(url, ziel)
    return ziel, eintrag["name"]


def _datei_laden(url: str, ziel: Path, tiefe: int = 0) -> None:
    try:
        with httpx.stream("GET", url, headers=_kopf(), follow_redirects=True,
                          timeout=120.0) as r:
            if r.status_code == 429:
                raise ApiFehler("Rate-Limit der Thingiverse-API erreicht", ratelimit=True)
            if r.status_code >= 400:
                raise ApiFehler(f"Download HTTP {r.status_code}")
            if "json" in (r.headers.get("content-type") or "") and tiefe == 0:
                r.read()
                daten = r.json()
                weiter = (daten.get("public_url") or daten.get("download_url")
                          or daten.get("url")) if isinstance(daten, dict) else None
                if not weiter:
                    raise ApiFehler("Download-Antwort ohne URL")
                return _datei_laden(weiter, ziel, tiefe + 1)
            with ziel.open("wb") as f:
                for stueck in r.iter_bytes():
                    f.write(stueck)
    except httpx.HTTPError as fehler:
        raise ApiFehler(str(fehler)) from fehler
