Name:           samyuj
Version:        1.0.0
Release:        1%{?dist}
Summary:        Native GTK 4 / GTK 3 Desktop Companion for Smart Connect Android Devices
License:        Apache-2.0
URL:            https://github.com/samyuj/samyuj
BuildArch:      noarch
Requires:       python3 >= 3.11, python3-gobject, python3-cairo, gtk3, python3-cryptography, python3-pillow

%description
Pair and interact with Motorola and Lenovo Smart Connect Android phones over
Wi-Fi, USB tethering, and BLE. Supports Mobile Desktop, Screen Share, Phone
App Streaming, V4L2 Webcam, Smart Clipboard, Cross Control, SMS, Gallery,
and NFSv3 File Sharing.

%install
mkdir -p %{buildroot}/usr/bin
mkdir -p %{buildroot}/usr/lib/samyuj/src
mkdir -p %{buildroot}/usr/share/applications
mkdir -p %{buildroot}/usr/share/icons/hicolor/scalable/apps
cp -r src/samyuj %{buildroot}/usr/lib/samyuj/src/
find %{buildroot}/usr/lib/samyuj/src -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
install -m 0755 samyuj %{buildroot}/usr/bin/samyuj
install -m 0755 samyuj-stop %{buildroot}/usr/bin/samyuj-stop
install -m 0644 packaging/linux/samyuj.svg %{buildroot}/usr/share/icons/hicolor/scalable/apps/samyuj.svg
install -m 0644 packaging/samyuj.desktop %{buildroot}/usr/share/applications/samyuj.desktop

%files
/usr/bin/samyuj
/usr/bin/samyuj-stop
/usr/lib/samyuj/
/usr/share/applications/samyuj.desktop
/usr/share/icons/hicolor/scalable/apps/samyuj.svg
