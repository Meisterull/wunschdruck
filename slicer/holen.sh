#!/bin/bash
# Snapmaker-Orca-AppImage holen und entpacken (landet NICHT im Git — zu gross).
# Aufruf: slicer/holen.sh [Version]   (Default: V2.3.6)
set -eu
VERSION="${1:-V2.3.6}"
HIER="$(dirname "$(readlink -f "$0")")"
URL="https://github.com/Snapmaker/OrcaSlicer/releases/download/${VERSION}/Snapmaker_Orca_Linux_AppImage_Ubuntu2404_${VERSION}.AppImage"

echo "Lade $URL …"
curl -L -o "$HIER/SnapmakerOrca.AppImage" "$URL"
chmod +x "$HIER/SnapmakerOrca.AppImage"
cd "$HIER"
rm -rf squashfs-root
./SnapmakerOrca.AppImage --appimage-extract >/dev/null
echo "Fertig: $HIER/squashfs-root (wird als /opt/orca in den Container gemountet)"
