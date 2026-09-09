"""Konfiguration aus Umgebungsvariablen (.env via docker-compose)."""
import os
from pathlib import Path

# Pflicht fuer den Druckbetrieb; ohne laeuft die App im reinen Slice-Modus.
MOONRAKER_URL = os.environ.get("MOONRAKER_URL", "").rstrip("/")

# Home Assistant — alles optional. Ohne HA gibt es keine Meldungen und keine
# automatische Steckdose, der Rest funktioniert unveraendert.
HA_URL = os.environ.get("HA_URL", "").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
HA_NOTIFY = os.environ.get("HA_NOTIFY", "")               # z.B. notify.mein_telegram
HA_SWITCH = os.environ.get("HA_SWITCH", "")               # z.B. switch.3d_drucker
HA_WARTUNGSRIEGEL = os.environ.get("HA_WARTUNGSRIEGEL", "")  # z.B. input_boolean.drucker_wartung

SPRACHE_STANDARD = os.environ.get("SPRACHE_STANDARD", "de")
DATEN_DIR = Path(os.environ.get("DATEN_DIR", "/data"))
ORCA_APPRUN = os.environ.get("ORCA_APPRUN", "/opt/orca/AppRun")
# Vanilla-OrcaSlicer für Mehrfarb-Projekte: der Snapmaker-Fork (V2.3.6) stürzt
# bei bemalten Multi-Extruder-3MFs in GUI_App::filaments_cnt ab, Vanilla nicht.
ORCA_VANILLA = os.environ.get("ORCA_VANILLA", "/opt/vanilla/AppRun")
PROFILDIR = Path(os.environ.get("PROFILDIR", "/opt/orca/resources/profiles"))

# Wie lange nach dem Einschalten der Steckdose auf Moonraker gewartet wird.
EINSCHALT_TIMEOUT_S = int(os.environ.get("EINSCHALT_TIMEOUT_S", "300"))
# Zeitlimit fuer einen Slice-Lauf.
SLICE_TIMEOUT_S = int(os.environ.get("SLICE_TIMEOUT_S", "900"))
# Fertige/verworfene/fehlgeschlagene Jobs nach so vielen Tagen automatisch
# loeschen (0 = nie aufraeumen).
AUFRAEUMEN_TAGE = int(os.environ.get("AUFRAEUMEN_TAGE", "14"))
# Optionaler Kamera-Stream des Druckers (MJPEG-/Snapshot-URL); leer = keine
# Kamera. Die Stock-U1-Firmware liefert keinen Stream, die Extended-Firmware
# z.B. http://<drucker>/webcam/stream.mjpg
KAMERA_URL = os.environ.get("KAMERA_URL", "")

JOBS_DIR = DATEN_DIR / "jobs"
PROFIL_CACHE = DATEN_DIR / "profil-cache"
DB_PFAD = DATEN_DIR / "wunschdruck.db"
