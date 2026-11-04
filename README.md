# Samyuj — Native Linux Desktop Companion for Android Smart Connect / Ready For

Samyuj is a native GTK 3 / GTK 4 Linux desktop application that implements the Smart Connect / Ready For protocol suite over Wi-Fi LAN, USB RNDIS/NCM tethering, and BLE discovery.

## Key Capabilities

- **Mobile Desktop (`svc-1`, `svc-10`)**: Stream the phone's desktop workspace in a low-latency hardware-accelerated window with full keyboard, mouse, touch, and smooth-scroll forwarding (`svc-16`).
- **Phone App Streaming (`svc-15`, `svc-17`)**: Browse and launch individual Android apps in dedicated Linux windows with concurrent multi-app streaming.
- **PC Display Extend & Mirror (`svc-202`)**: Mirror your Linux desktop to your phone or create a virtual extended monitor (`xrandr` / Wayland / dummy output) with aspect-ratio presets (`PHONE_EXTEND_PRESETS`).
- **Phone Webcam (`svc-3`)**: Use the phone's cameras as a Linux `v4l2loopback` virtual webcam (`/dev/video*`) with automatic U/V chroma correction and low-light adaptive gamma enhancement.
- **Bidirectional File Sharing & FUSE/NFSv3 (`svc-2`, `svc-168`, `svc-205`)**: Multi-file TLS drag-and-drop transfers (`svc-2`), embedded ONC-RPC / NFSv3 over TLS server for PC folder sharing, and automatic user-space FUSE mounting of phone storage (`PhoneNfsClient`).
- **Clipboard, Notifications, SMS/MMS & Cross Control (`svc-5`, `svc-9`, `svc-26`, `svc-204`)**: Seamless edge cursor/keyboard sharing, two-way clipboard sync, live notification mirroring/reply, and SMS/MMS conversation threading.

## Installation on Linux

### System Dependencies (Debian / Ubuntu / Fedora / Arch)

```bash
# Debian / Ubuntu
sudo apt update && sudo apt install -y \
  python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
  ffmpeg libavcodec-dev libavformat-dev libavutil-dev libswscale-dev \
  fuse3 libfuse2 iproute2 network-manager bluez adb openssl \
  v4l2loopback-dkms v4l2loopback-utils

# Fedora
sudo dnf install -y python3-gobject gtk3 ffmpeg-free fuse3 iproute NetworkManager bluez android-tools openssl v4l2loopback

# Arch Linux
sudo pacman -S --needed python-gobject gtk3 ffmpeg fuse3 iproute2 networkmanager bluez android-tools openssl v4l2loopback-dkms
```

### Option 1: Install as a Native Linux Desktop Application (`pipx` + Desktop Entry)

```bash
pipx install .
samyuj --help
```

Or run the bundled user-local installer script to create `~/.local/bin/samyuj`, the `.desktop` launcher, and icon integration:

```bash
./install_desktop_app.sh
```

### Option 2: Virtual Environment Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
samyuj
```

## Command-Line Options

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--lan-ip <IP>` | Auto-detected | Override the LAN IPv4 address advertised to the phone on multi-homed hosts |
| `--usb-addr-order <LIST>` | `dhcp,static-p2p` | Comma-separated USB address assignment ladder (`dhcp`, `link-local`, `static-p2p`) |
| `--demo` | `False` | Populate UI views with sample demonstration data when no phone is connected |
| `--debug` | `False` | Enable verbose diagnostic logging (`events.csv`, `events.jsonl`) in `$XDG_CACHE_HOME/samyuj/runs/` |
| `--log-level <LEVEL>` | `info` | Set console logging verbosity (`error`, `warning`, `info`, `debug`) |
| `--log-file <PATH>` | `None` | Write structured logs to the specified file path |
| `--version` | — | Print version and exit |

## Advanced User Settings (`~/.local/share/samyuj/user_settings.json`)

- `"usb_addr_order"`: `"dhcp,static-p2p"` — controls the USB interface IPv4 assignment ladder (`platform/linux/usb_network.py`).
- `"extra_phone_vids"`: `["0x1234"]` — additional USB Vendor IDs to treat as candidate Android phones during USB hotplug detection.
- `"extra_smartconnect_pkgs"`: `["com.example.companion"]` — additional Android package names to include in ADB battery-saver whitelisting (`platform/linux/power.py`).
- `"demo_mode"`: `false` — when `false`, all UI surfaces render live phone data or empty states.

## Running the Test Suite

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py" -v
```

## License

Released under the [Apache License 2.0](LICENSE).
