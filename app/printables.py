"""Printables-Anbindung ueber die GraphQL-Schnittstelle des Web-Frontends.

Printables hat keine oeffentliche API (Stand 09/2026). Das Frontend spricht mit
api.printables.com/graphql — offen, ohne Token; die Abfragen hier sind die des
Browsers (Suche, Modell, Dateiliste, Download-Link-Mutation, die den Download
beim Designer genauso zaehlt wie der Knopf auf der Seite). Kein Vertrag: Prusa
kann Felder jederzeit umbenennen, dann bricht nur diese Quelle (ApiFehler),
der Rest der App nicht. Gate: PRINTABLES_SUCHE=1 in der .env.
"""
import html
import posixpath
import re
from pathlib import Path

import httpx

from . import konfig

API = "https://api.printables.com/graphql/"
MEDIEN = "https://media.printables.com/"
SEITE = "https://www.printables.com"
DRUCKBAR = (".stl", ".3mf")
KOPF = {"User-Agent": "Wunschdruck/1.0 (+https://github.com/Meisterull/wunschdruck)"}

_SUCHE = """query SearchModels($query: String!, $limit: Int, $offset: Int) {
  result: searchPrints2(query: $query, printType: print, limit: $limit, offset: $offset,
                        ordering: best_match) {
    items { id name slug user { publicUsername handle } image { filePath } }
  }
}"""
_MODELL = """query ModelDetail($id: ID!) {
  model: print(id: $id) {
    id name slug description summary
    license { name abbreviation }
    user { publicUsername handle }
    image { filePath }
    images { filePath }
  }
}"""
_DATEIEN = """query ModelFiles($id: ID!) {
  model: print(id: $id) {
    stls { id name fileSize }
    otherFiles { id name fileSize }
  }
}"""
_DOWNLOAD = """mutation GetDownloadLink($id: ID!, $modelId: ID!, $fileType: DownloadFileTypeEnum!,
                                       $source: DownloadSourceEnum!) {
  getDownloadLink(id: $id, printId: $modelId, fileType: $fileType, source: $source) {
    ok errors { field messages } output { link ttl }
  }
}"""


class ApiFehler(Exception):
    def __init__(self, meldung: str, ratelimit: bool = False):
        super().__init__(meldung)
        self.ratelimit = ratelimit


def konfiguriert() -> bool:
    return konfig.PRINTABLES_SUCHE


def _gql(operation: str, abfrage: str, variablen: dict, timeout: float = 15.0) -> dict:
    """Eine GraphQL-Operation; liefert den data-Teil. GraphQL-Fehler, HTTP-Fehler
    und Cloudflare-Bremsen (429) werden zu ApiFehler."""
    try:
        r = httpx.post(API, json={"operationName": operation, "query": abfrage, "variables": variablen},
                       headers=KOPF, timeout=timeout)
    except httpx.HTTPError as fehler:
        raise ApiFehler(str(fehler)) from fehler
    if r.status_code == 429:
        raise ApiFehler("Printables bremst (HTTP 429)", ratelimit=True)
    if r.status_code >= 400:
        raise ApiFehler(f"Printables HTTP {r.status_code}")
    try:
        daten = r.json()
    except ValueError as fehler:
        raise ApiFehler("keine JSON-Antwort") from fehler
    if not isinstance(daten, dict):
        raise ApiFehler("unerwartete Antwortform")
    if daten.get("errors"):
        meldungen = "; ".join(str(e.get("message") if isinstance(e, dict) else e) for e in daten["errors"])
        raise ApiFehler(f"Printables: {meldungen}"[:300])
    return daten.get("data") or {}


def _text(wert) -> str:
    """HTML-Beschreibung → schlichter Text."""
    roh = re.sub(r"<[^>]+>", " ", str(wert or ""))
    return " ".join(html.unescape(roh).split())


def _bild(pfad: str, groesse: str = "320x240") -> str:
    """Kleine WebP-Variante eines Medienpfads — das Frontend nutzt
    …/<ordner>/thumbs/inside/<BxH>/<endung>/<name>.webp (7 KB statt 2 MB)."""
    if not pfad:
        return ""
    ordner, datei = posixpath.split(pfad)
    stamm, endung = posixpath.splitext(datei)
    if not endung:
        return MEDIEN + pfad
    return f"{MEDIEN}{ordner}/thumbs/inside/{groesse}/{endung.lstrip('.')}/{stamm}.webp"


def _url(m: dict) -> str:
    slug = m.get("slug") or ""
    return f"{SEITE}/model/{m.get('id')}" + (f"-{slug}" if slug else "")


def _treffer(m: dict) -> dict:
    nutzer = m.get("user") or {}
    return {"id": str(m.get("id")), "name": m.get("name") or "",
            "bild": _bild((m.get("image") or {}).get("filePath") or ""),
            "autor": nutzer.get("publicUsername") or nutzer.get("handle") or "",
            "url": _url(m)}


def suchen(begriff: str, seite: int = 1, pro_seite: int = 24) -> dict:
    """{treffer: [{id,name,bild,autor,url}], gesamt} — gesamt liefert die
    Schnittstelle nicht (None); Blaettern ueber offset."""
    daten = _gql("SearchModels", _SUCHE, {"query": begriff.strip(), "limit": pro_seite,
                                           "offset": (max(1, seite) - 1) * pro_seite})
    roh = (daten.get("result") or {}).get("items") or []
    return {"treffer": [_treffer(m) for m in roh if isinstance(m, dict) and m.get("id")],
            "gesamt": None}


def modell(modell_id) -> dict:
    """Modellseite inkl. Bildern (eine Abfrage — bilder() braucht es hier nicht)."""
    d = _gql("ModelDetail", _MODELL, {"id": str(modell_id)}).get("model") or {}
    if not d.get("id"):
        raise ApiFehler("Modell nicht gefunden")
    nutzer, lizenz = d.get("user") or {}, d.get("license") or {}
    return {**_treffer(d),
            "lizenz": lizenz.get("abbreviation") or lizenz.get("name") or "",
            "beschreibung": _text(d.get("description") or d.get("summary"))[:700],
            "autor_url": f"{SEITE}/@{nutzer['handle']}" if nutzer.get("handle") else "",
            "bild_gross": _bild((d.get("image") or {}).get("filePath") or "", "1280x960"),
            "bilder": [_bild(b.get("filePath"), "1280x960")
                       for b in (d.get("images") or []) if isinstance(b, dict) and b.get("filePath")][:6]}


def dateien(modell_id) -> list[dict]:
    """Dateiliste: 'stls' sind bei Printables alle Modelldateien (STL, 3MF, OBJ,
    STEP, F3D …) — druckbar ist davon nur STL/3MF. otherFiles (PDF, Bilder,
    Anleitungen) werden gezeigt, aber nicht geladen."""
    m = _gql("ModelFiles", _DATEIEN, {"id": str(modell_id)}).get("model") or {}
    aus = []
    for gruppe, ladbar in (("stls", True), ("otherFiles", False)):
        for f in m.get(gruppe) or []:
            if not isinstance(f, dict) or not f.get("id"):
                continue
            name = Path(str(f.get("name") or "")).name
            aus.append({"id": str(f["id"]), "name": name, "groesse": f.get("fileSize") or 0,
                        "druckbar": ladbar and name.lower().endswith(DRUCKBAR),
                        "download_url": "", "bild": ""})
    return aus


def herunterladen(modell_id, datei_id, ziel_dir: Path) -> tuple[Path, str]:
    """STL/3MF in ziel_dir/quelle.<endung> laden; liefert (Pfad, Originalname).
    Der Download-Link kommt aus der Mutation (24 h gueltig, zaehlt den Download
    beim Designer)."""
    eintrag = next((d for d in dateien(modell_id) if d["id"] == str(datei_id)), None)
    if not eintrag or not eintrag["druckbar"]:
        raise ApiFehler("Datei unbekannt oder kein STL/3MF")
    antwort = _gql("GetDownloadLink", _DOWNLOAD,
                   {"id": str(datei_id), "modelId": str(modell_id),
                    "fileType": "stl", "source": "model_detail"}).get("getDownloadLink") or {}
    link = (antwort.get("output") or {}).get("link")
    if not antwort.get("ok") or not link:
        gruende = "; ".join(", ".join(e.get("messages") or []) for e in (antwort.get("errors") or [])
                            if isinstance(e, dict))
        raise ApiFehler(f"Download-Link verweigert: {gruende or 'ohne Begruendung'}")
    ziel = ziel_dir / f"quelle{Path(eintrag['name']).suffix.lower()}"
    try:
        with httpx.stream("GET", link, headers=KOPF, follow_redirects=True, timeout=120.0) as r:
            if r.status_code == 429:
                raise ApiFehler("Printables bremst (HTTP 429)", ratelimit=True)
            if r.status_code >= 400:
                raise ApiFehler(f"Download HTTP {r.status_code}")
            with ziel.open("wb") as f:
                for stueck in r.iter_bytes():
                    f.write(stueck)
    except httpx.HTTPError as fehler:
        raise ApiFehler(str(fehler)) from fehler
    return ziel, eintrag["name"]
