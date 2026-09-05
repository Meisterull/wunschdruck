#!/bin/bash
# Beide Slicer-AppImages holen und entpacken (landen NICHT im Git — zu gross):
# - Snapmaker Orca (U1-Profile, einfarbige Jobs)
# - Vanilla OrcaSlicer (Mehrfarb-Projekte; der Fork-CLI crasht daran)
# Aufruf: slicer/holen.sh [Fork-Version] [Vanilla-Version]
set -eu
VERSION="${1:-V2.3.6}"
VANILLA="${2:-v2.4.2}"
HIER="$(dirname "$(readlink -f "$0")")"

echo "Lade Snapmaker Orca $VERSION …"
curl -L -o "$HIER/SnapmakerOrca.AppImage" \
  "https://github.com/Snapmaker/OrcaSlicer/releases/download/${VERSION}/Snapmaker_Orca_Linux_AppImage_Ubuntu2404_${VERSION}.AppImage"
chmod +x "$HIER/SnapmakerOrca.AppImage"
cd "$HIER"
rm -rf squashfs-root
./SnapmakerOrca.AppImage --appimage-extract >/dev/null

echo "Lade Vanilla OrcaSlicer $VANILLA …"
VNUM="${VANILLA#v}"
curl -L -o "$HIER/VanillaOrca.AppImage" \
  "https://github.com/OrcaSlicer/OrcaSlicer/releases/download/${VANILLA}/OrcaSlicer_Linux_AppImage_Ubuntu2404_V${VNUM}.AppImage"
chmod +x "$HIER/VanillaOrca.AppImage"
# In eigenem Ordner extrahieren — direkt hier würde es squashfs-root vermischen!
rm -rf vanilla-root vtmp && mkdir vtmp && cd vtmp
../VanillaOrca.AppImage --appimage-extract >/dev/null
mv squashfs-root ../vanilla-root && cd .. && rmdir vtmp
echo "Fertig: squashfs-root (/opt/orca) + vanilla-root (/opt/vanilla)"
