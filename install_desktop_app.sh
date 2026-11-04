#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS_DIR="$XDG_DATA_HOME/applications"
ICONS_DIR="$XDG_DATA_HOME/icons/hicolor/scalable/apps"
BIN_DIR="$HOME/.local/bin"

mkdir -p "$APPS_DIR" "$ICONS_DIR" "$BIN_DIR"
cp "$SCRIPT_DIR/packaging/linux/samyuj.svg" "$ICONS_DIR/samyuj.svg"
ln -sf "$SCRIPT_DIR/samyuj" "$BIN_DIR/samyuj"

cat > "$APPS_DIR/samyuj.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Samyuj
GenericName=Phone & PC Companion
Comment=Native Linux GTK 3 Desktop App for Motorola & Lenovo Smart Connect
Exec=$SCRIPT_DIR/samyuj
Path=$SCRIPT_DIR
Icon=$ICONS_DIR/samyuj.svg
Terminal=false
Categories=Utility;Network;FileTransfer;AudioVideo;
Keywords=Samyuj;Phone;Companion;Webcam;Desktop;AppStream;NFS;FileTransfer;
StartupNotify=true
StartupWMClass=samyuj
EOF

chmod +x "$APPS_DIR/samyuj.desktop" "$SCRIPT_DIR/samyuj"
echo "Installed Smart Connect desktop launcher to $APPS_DIR/samyuj.desktop"
