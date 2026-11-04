#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="1.0.0"
ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
STAGE_DIR="${REPO_ROOT}/dist/deb_stage/samyuj_${VERSION}_${ARCH}"
OUT_DEB="${REPO_ROOT}/dist/samyuj_${VERSION}_${ARCH}.deb"

echo "[1/4] Preparing staging directory: ${STAGE_DIR}"
rm -rf "${STAGE_DIR}"
mkdir -p "${STAGE_DIR}/DEBIAN"
mkdir -p "${STAGE_DIR}/usr/bin"
mkdir -p "${STAGE_DIR}/usr/lib/samyuj/src"
mkdir -p "${STAGE_DIR}/usr/share/applications"
mkdir -p "${STAGE_DIR}/usr/share/icons/hicolor/scalable/apps"

echo "[2/4] Copying Smart Connect Python package & native acceleration library"
cp -r "${REPO_ROOT}/src/samyuj" "${STAGE_DIR}/usr/lib/samyuj/src/"
find "${STAGE_DIR}/usr/lib/samyuj/src" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "${STAGE_DIR}/usr/lib/samyuj/src" -name "*.pyc" -delete 2>/dev/null || true

if command -v cmake >/dev/null 2>&1 && command -v c++ >/dev/null 2>&1; then
    echo "    building libsc_fast from source (cmake)..."
    if cmake -S "${REPO_ROOT}" -B "${REPO_ROOT}/build" -DCMAKE_BUILD_TYPE=Release >/dev/null \
        && cmake --build "${REPO_ROOT}/build" >/dev/null; then
        install -m 0644 "${REPO_ROOT}/build/libsc_fast.so" "${STAGE_DIR}/usr/lib/samyuj/libsc_fast.so"
    else
        echo "    warning: libsc_fast build failed; continuing without native acceleration"
    fi
else
    echo "    note: cmake/c++ not available; skipping optional libsc_fast (pure-Python fallbacks are used)"
fi

install -m 0755 "${REPO_ROOT}/samyuj-stop" "${STAGE_DIR}/usr/bin/samyuj-stop"

install -m 0755 "${REPO_ROOT}/samyuj" "${STAGE_DIR}/usr/bin/samyuj"

cp "${REPO_ROOT}/packaging/linux/samyuj.svg" "${STAGE_DIR}/usr/share/icons/hicolor/scalable/apps/samyuj.svg"
cat > "${STAGE_DIR}/usr/share/applications/samyuj.desktop" << 'DESK_EOF'
[Desktop Entry]
Name=Samyuj
Comment=Pair and interact with Motorola & Lenovo Smart Connect phones on Linux
Exec=/usr/bin/samyuj
Icon=samyuj
Terminal=false
Type=Application
Categories=Utility;Network;Telephony;
StartupNotify=true
DESK_EOF

echo "[3/4] Writing DEBIAN/control and postinst"
cat > "${STAGE_DIR}/DEBIAN/control" << CTRL_EOF
Package: samyuj
Version: ${VERSION}
Section: net
Priority: optional
Architecture: ${ARCH}
Maintainer: Samyuj Contributors <https://github.com/samyuj/samyuj/issues>
Depends: python3 (>= 3.11), python3-gi, python3-gi-cairo, gir1.2-gtk-3.0 | gir1.2-gtk-4.0, python3-cryptography, python3-pil
Recommends: python3-aiortc, python3-av, python3-mss, python3-bleak, python3-segno, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good, gstreamer1.0-libav, v4l2loopback-dkms, v4l2loopback-utils, wl-clipboard, xclip, libnotify-bin, adb, fuse3
Description: Native GTK 4 / GTK 3 Desktop Companion for Smart Connect Android Devices
 Pair and interact with Motorola and Lenovo Smart Connect Android phones over
 Wi-Fi, USB tethering, and BLE. Supports Mobile Desktop, Screen Share, Phone
 App Streaming, V4L2 Webcam, Smart Clipboard, Cross Control, SMS, Gallery,
 and NFSv3 File Sharing.
CTRL_EOF

cat > "${STAGE_DIR}/DEBIAN/postinst" << 'POST_EOF'
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
exit 0
POST_EOF
chmod 0755 "${STAGE_DIR}/DEBIAN/postinst"

echo "[4/4] Building .deb package: ${OUT_DEB}"
if command -v dpkg-deb >/dev/null 2>&1; then
    dpkg-deb --build --root-owner-group "${STAGE_DIR}" "${OUT_DEB}"
    echo "Successfully built: ${OUT_DEB}"
else
    echo "dpkg-deb not found on this host; staging tree is ready at ${STAGE_DIR}"
fi
