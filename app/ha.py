"""Home-Assistant-Anbindung: Meldungen, Steckdose, Wartungsriegel. Alles optional
und best-effort — HA-Ausfaelle duerfen den Druck-Workflow nie mitreissen."""
import logging

import httpx

from . import konfig

log = logging.getLogger("wunschdruck.ha")


def _kopf():
    return {"Authorization": f"Bearer {konfig.HA_TOKEN}"}


def konfiguriert() -> bool:
    return bool(konfig.HA_URL and konfig.HA_TOKEN)


def melden(text: str) -> None:
    """Nachricht ueber den konfigurierten Notify-Dienst schicken (best-effort).

    Markdown-Sonderzeichen werden entschaerft: HA-Telegram parst die Nachricht
    als Markdown, und ein '_' aus einem Dateinamen (wuerfel_20mm.stl) liess
    den Versand am 04.09.2026 mit 'Can't parse entities' platzen."""
    if not (konfiguriert() and konfig.HA_NOTIFY):
        return
    domain, dienst = konfig.HA_NOTIFY.split(".", 1)
    text = text.translate(str.maketrans({z: " " for z in "_*`["}))
    try:
        httpx.post(f"{konfig.HA_URL}/api/services/{domain}/{dienst}",
                   headers=_kopf(), json={"message": text}, timeout=10)
    except httpx.HTTPError as fehler:
        log.warning("HA-Meldung fehlgeschlagen: %s", fehler)


def zustand(entity: str) -> str | None:
    try:
        r = httpx.get(f"{konfig.HA_URL}/api/states/{entity}", headers=_kopf(), timeout=10)
        r.raise_for_status()
        return r.json().get("state")
    except httpx.HTTPError as fehler:
        log.warning("HA-Zustand %s fehlgeschlagen: %s", entity, fehler)
        return None


def wartungsriegel_aktiv() -> bool:
    """True, wenn der Wartungsriegel gesetzt ist — dann NIE an der Steckdose schalten.
    Ist kein Riegel konfiguriert oder HA nicht erreichbar, gilt: nicht schalten
    verhindern koennen wir nicht pruefen → nur bei explizitem 'off' ist es sicher."""
    if not (konfiguriert() and konfig.HA_WARTUNGSRIEGEL):
        return False
    return zustand(konfig.HA_WARTUNGSRIEGEL) != "off"


def steckdose_einschalten() -> bool:
    if not (konfiguriert() and konfig.HA_SWITCH):
        return False
    try:
        r = httpx.post(f"{konfig.HA_URL}/api/services/switch/turn_on",
                       headers=_kopf(), json={"entity_id": konfig.HA_SWITCH}, timeout=10)
        r.raise_for_status()
        return True
    except httpx.HTTPError as fehler:
        log.warning("Steckdose einschalten fehlgeschlagen: %s", fehler)
        return False
