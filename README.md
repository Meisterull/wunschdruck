# Wunschdruck

**Upload → Slice → Approve → Print.** A phone-friendly, self-hosted web app that
turns a **Snapmaker U1** into a "just send it to the printer" appliance: headless
slicing with the OrcaSlicer CLI and the vendor's own U1 profiles, one-tap
approval, and the printer switches itself on.

Deutsche Fassung: **[README.de.md](README.de.md)** · License: **AGPL-3.0**

<p align="center">
  <img src="docs/screenshot-phone.png" alt="Wunschdruck on a phone: upload form and a job ready for approval" width="300">
  <img src="docs/screenshot-3d.png" alt="3D preview of the sliced G-code on the U1 plate" width="560">
</p>

## Why

You find a model on your phone and want it printed. You do not want to boot the
PC, open the slicer, pick profiles, export, walk over to the printer. Wunschdruck
runs on a small home server next to the printer: share the STL/3MF from the
phone's share sheet, pick a preset, tap **Print**. It slices with Snapmaker's
own U1 profiles, switches the smart plug on, uploads the G-code through
Moonraker and starts the job. Home Assistant tells you when it is sliced,
started and done.

Built for one household with one U1 on stock firmware. Shared as-is, mainly
because getting the Orca CLI to slice headless for the U1 cost days — see
[Orca CLI pitfalls](#orca-cli-pitfalls) below.

## What you get

- **Upload STL or 3MF** in the browser, or share a file straight from the
  phone (PWA share target).
- **Presets**: PLA / PETG / TPU × Fine 0.12 / Standard 0.20 / Draft 0.24 mm —
  the U1 profiles that ship with Snapmaker Orca. Per job: supports on/off,
  tree or classic, support filament slot, infill %, wall loops, top/bottom
  layers, orientation (auto or as in the file), copies, plate number for
  multi-plate 3MFs.
- **Custom print profiles**: export a process preset from Snapmaker Orca on
  the PC, import it in Settings — it appears in the quality dropdown.
- **Multi-color 3MFs** (MakerWorld / Bambu / Orca projects with painted
  colors): the app reads the project's colors and lets you map each color to a
  physical slot 1–4. Prime tower stays on, as the U1 profiles intend.
- **Spool stock and slots**: every spool you own is an entry (material,
  color, brand, net weight, remaining grams, purchase date, note) with its own
  history; fill the printer's four slots from that list. Every finished
  Wunschdruck print is deducted from the spool in each slot automatically —
  whether started from the app or at the printer, cancelled prints
  proportionally (Moonraker history, booked exactly once). The home page
  shows the remaining grams per slot, and a job card warns when it needs more
  than the spool has left. When the printer is reachable, each job card also
  compares the required slots with what is *actually* loaded (Moonraker
  `print_task_config`) and warns before you approve.
- **3D preview of the sliced G-code**: real placement on the 270 × 270 plate,
  layer slider, per-slot colors, supports translucent, infill toggle, look
  under the plate to check first-layer text. three.js, runs on phones.
- **Approval workflow**: nothing prints on upload. Buttons: Print · Send only
  (upload the G-code, start at the machine) · Discard · Delete · Re-slice with
  changed settings.
- **Power chain via Home Assistant**: smart plug on → wait for Moonraker →
  upload → start. A maintenance lock (`input_boolean`) blocks the power-on,
  fail-closed.
- Live progress with remaining time, print history from Moonraker, filament
  usage per slot, automatic cleanup of old jobs.
- Notifications through any HA notify service (Telegram, mobile app, …):
  sliced, started, finished, failed.
- **Find models**: link tiles for MakerWorld and Thingiverse (download in the
  browser, then share the file into the app), and with a Thingiverse app token
  a search page inside Wunschdruck: results with images, model page with
  license and author, pick an STL/3MF, set the job options, done — the file
  is downloaded straight into the container and sliced.
- German / English UI, optional camera stream URL.

<p align="center">
  <img src="docs/screenshot-desktop.png" alt="Desktop view: upload form with all job settings and a job card" width="820">
</p>

## How it works

FastAPI + SQLite + two worker threads (slice queue, printer chain), one Docker
container. Two slicer engines run as plain CLI binaries inside it:

| Engine | Used for | Why |
|---|---|---|
| Snapmaker Orca `V2.3.6` (fork) | single-color jobs | ships the U1 machine, process and filament profiles |
| Vanilla OrcaSlicer `2.4.2` | painted multi-extruder projects | the fork's CLI segfaults on every painted multi-extruder 3MF |

Vendor profiles are flattened (`inherits` resolved) and stripped of keys that
crash the CLI. The app talks to the U1's built-in Moonraker (port 7125, no API
key on stock firmware) for upload, start, progress and history.

## Requirements

- Snapmaker U1 on stock firmware, same LAN as the server.
- x86_64 Linux host with Docker (the slicer AppImages are x86_64). One-time
  download of both slicers, about 1.3 GB, kept outside git.
- Optional: Home Assistant with a notify service, a smart-plug switch and an
  `input_boolean` as maintenance lock. Pairs well with
  [snapmaker-u1-homeassistant](https://github.com/Meisterull/snapmaker-u1-homeassistant),
  which brings the U1-specific sensors and a safe power-*off* chain.

## Install

```bash
git clone https://github.com/Meisterull/wunschdruck.git
cd wunschdruck
slicer/holen.sh              # downloads and extracts both slicer AppImages
cp .env.example .env         # set MOONRAKER_URL, optionally the HA_* keys
docker compose up -d --build
```

Open `http://<host>:8446`. Jobs, the profile cache and imported profiles live
in the named volume `wunschdruck-daten`.

**HTTPS** is needed for installing the PWA and for the share target (browsers
only offer both in a secure context). Add a `docker-compose.override.yml`
that mounts your certificate and hands it to uvicorn, then open the app via
the hostname on the certificate:

```yaml
services:
  wunschdruck:
    volumes:
      - /path/to/fullchain.pem:/ssl/fullchain.pem:ro
      - /path/to/privkey.pem:/ssl/privkey.pem:ro
    command: ["/venv/bin/uvicorn", "app.haupt:app", "--host", "0.0.0.0", "--port", "8446",
              "--ssl-certfile", "/ssl/fullchain.pem", "--ssl-keyfile", "/ssl/privkey.pem"]
```

### Security

There is **no login**. The app is meant for a trusted home LAN: anyone who can
reach port 8446 can start prints and power the printer on. Do not expose it to
the internet; if you must, put it behind a reverse proxy with authentication.

## Home Assistant

Everything HA-related is optional and best-effort — an HA outage never blocks
slicing. Keys in `.env`:

| Key | Meaning |
|---|---|
| `HA_URL`, `HA_TOKEN` | HA base URL and a long-lived access token |
| `THINGIVERSE_TOKEN` | optional: app token from thingiverse.com/developers — enables the in-app Thingiverse search and download |
| `HA_NOTIFY` | notify service for messages, e.g. `notify.mobile_app_phone` |
| `HA_SWITCH` | smart plug of the printer, e.g. `switch.printer_plug` |
| `HA_WARTUNGSRIEGEL` | `input_boolean` maintenance lock; when on, the app refuses to power the printer up |
| `EINSCHALT_TIMEOUT_S` | how long to wait for Moonraker after power-on (default 300) |

Without HA you switch the printer on yourself; the rest works unchanged.

## Orca CLI pitfalls

Everything below was learned the hard way and is handled in the code. If you
drive the Orca CLI headless for a U1 yourself, this list may save you a week.

- **Vendor profiles use `inherits`, the CLI does not resolve it** and aborts.
  Flatten first — `werkzeuge/profil_flatten.py` builds an index over the
  `name` field across the Snapmaker and OrcaFilamentLibrary folders.
- **`wipe_tower_filament` in a process profile segfaults the CLI.** Found by
  delta debugging (`proben/prozess_bisect.py`); the flattener strips it, also
  from settings embedded in uploaded 3MFs (`CLI_GIFTIG`).
- `--export-3mf` wants a name **relative** to `--outputdir`; an absolute path
  yields a doubled path.
- Estimated time and filament usage may sit in the G-code **footer**, not the
  header — scan the whole file.
- Slot *N*: pass *N* entries in `--load-filaments` plus `--load-filament-ids N`;
  the G-code then uses `T(N-1)`.
- **Orca deduplicates `--load-filaments` by the profile's `name`.** Four
  identical "Generic PLA" entries make the printer look single-extruder and
  the prime tower silently disappears. Give every slot a uniquely named copy.
- **Snapmaker Orca V2.3.6 CLI crashes on every painted multi-extruder
  project** (`GUI_App::filaments_cnt()` is null in the CLI, reached via
  `expand_plate_extruders`; verified with gdb, no input works around it).
  Vanilla OrcaSlicer 2.4.2 slices them with the same flattened U1 profiles;
  the only fork-specific G-code variable, `chamber_cooling_mode`, has to be
  replaced by a literal.
- Painted triangles carry `paint_color` codes: the bit stream of PrusaSlicer's
  `TriangleSelector`, hex digits read **backwards**, 4 bits each. A fully
  painted triangle is `4` = filament 1, `8` = 2, `0C` = 3, `1C` = 4 — it is
  *not* "filament × 4": writing `10` for filament 4 decodes as *unpainted*
  and the colour silently vanishes. `app/dreimf.py` decodes and re-encodes
  the whole tree, so fine-brushed areas can be remapped too.
- **The CLI writes no thumbnails headless** (no GL context), and the U1
  touchscreen then shows a placeholder for the job. The app renders its own
  PNGs from the sliced paths (`app/miniatur.py`, Pillow) and inserts an
  Orca-style `; thumbnail begin WxH` block after the header — Moonraker
  picks it up like a desktop-sliced file.
- **The G-code's `filament_colour` never reflects your slots.** The fork
  writes its profile default (`#F2754E`) for STLs, and a project 3MF keeps its
  original colour list (a Bambu PVA slot became `#00000000`). The U1 matches
  G-code filaments to loaded slots by type *and colour*, so it proposed slot 1
  for both colours of a two-colour print. The app writes the slot colours from
  its settings into the slot profiles and, as a safety net, rewrites
  `filament_colour`/`extruder_colour` in the G-code config block.
- Project 3MFs written by a newer slicer need `--allow-newer-file`.
- `--clone-objects N` is the **total** count; `--repetitions` refuses plate
  mode.
- `--orient 1` may flip a model (a mushroom test print ended upside down and
  lost its supports). `--arrange` must stay on, otherwise the file's origin
  coordinates land outside the build volume.
- Vanilla ships with arc fitting on (G2/G3); the app disables
  `enable_arc_fitting` because the U1's Klipper arc support is unclear.
- `raft_first_layer_expansion` below 0 (Bambu profiles use −1) fails CLI
  validation — clamp to 0.

## Project layout

```
app/            FastAPI app: haupt.py (routes), worker.py (slice queue + printer chain),
                slicer.py (Orca CLI), dreimf.py (3MF color analysis), vorschau.py (G-code → preview),
                moonraker.py, ha.py, db.py, i18n.py, konfig.py, vorlagen/ (Jinja2), statisch/ (assets)
werkzeuge/      profil_flatten.py (profile flattener), symbol_bauen.py (PWA icons)
slicer/holen.sh downloads + extracts both slicer AppImages (binaries are gitignored)
proben/         the probe scripts that isolated the CLI crashes
tests/          tiny STL fixtures (20 mm cube, mushroom with overhang)
```

Code, comments and commit messages are in German; the UI is bilingual.

## Status

Young project (September 2026), one printer, one household. Verified end to
end: upload, single- and multi-color slicing against real MakerWorld projects,
G-code inspection, 3D preview, profile import, re-slicing. Not yet exercised at
the time of publishing: an actual print started through the app (the
Moonraker upload/start chain mirrors the calls the U1's own HA package uses in
production) and the live filament comparison against a powered-on printer.
The "FRONT" marker in the 3D view assumes Y = 0 is the door side. Issues and
reports from other U1 owners are welcome.

## Credits

- [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer) and
  [Snapmaker Orca](https://github.com/Snapmaker/OrcaSlicer) (AGPL-3.0) — used
  as external binaries, downloaded by `slicer/holen.sh`, not bundled.
- [three.js](https://threejs.org/) r147 and OrbitControls (MIT) — bundled in
  `app/statisch/`.
- [Moonraker](https://github.com/Arksine/moonraker) — the API the U1 speaks.

## License

AGPL-3.0 — see [LICENSE](LICENSE). Snapmaker and OrcaSlicer are trademarks of
their respective owners; this project is not affiliated with either.
