# Samyuj — Technical Architecture & Control Flow

## 1. Layered System Architecture & SOLID Design Principles

Samyuj is organized into 6 strictly separated layers enforced by automated AST and compiler symbol-table tests (`tests/architecture/test_architecture.py`):

1. **Presentation Layer (`src/samyuj/ui/`)**: Native GTK 3 interface (`ui/gtk/app.py`, `pages/*.py`, `windows/*.py`, `theme.py`, `smooth_scroll.py`). Communicates exclusively through `ApplicationFacade` (`ui/facade.py`) and immutable models (`ui/models.py`) without importing `protocol` or `transport` internals.
2. **Session & Service Orchestration Layer (`src/samyuj/session/` & `src/samyuj/services/`)**: `SamyujRuntime` (`session/lifecycle.py`) composes lifecycle mixins (`RuntimeStartupMixin`, `RuntimeSettingsMixin`, `SessionRouterMixin`, `SessionTeardownMixin`) and 17 domain service controllers (`DOMAIN_CONTROLLER_CLASSES`) registered via `ServiceBus` (`services/base.py`). Inbound frames route in O(1) via `POST_HELLO_ROUTES` (`session/router.py`), and commands route via `LOCAL_COMMAND_ROUTES` and `ONLINE_COMMAND_ROUTES` (`services/dispatcher.py`).
3. **Protocol & Framing Layer (`src/samyuj/protocol/`)**: Stateless JSON envelope builders (`protocol/envelope.py`, `protocol/services/*.py`) and RFC 1057/1813/1832 ONC RPC / XDR / NFSv3 encoders (`protocol/nfs/xdr.py`).
4. **Transport & Discovery Layer (`src/samyuj/transport/`)**: Custom RFC 6455 WebSocket parser (`transport/websocket.py`), RFC 6762 mDNS engine (`transport/discovery/mdns.py`), BLE `0x5246` GATT advertiser/reassembler (`transport/discovery/ble.py`), and USB RNDIS/NCM tether detector (`transport/discovery/usb.py`).
5. **Media & Native Acceleration Layer (`src/samyuj/media/` & `sc_fast.cpp`)**: WebRTC peer manager (`media/runtime.py`), RTCP REMB bitrate controller (`media/remb.py`), frame hub (`media/frame_hub.py`), X11/Wayland screen capture (`media/capture.py`), V4L2 loopback writer (`media/v4l2/writer.py`), GStreamer presenter (`media/gst_present/presenter.py`), and C++17 native helper (`sc_fast.cpp` -> `libsc_fast.so`).
6. **Files & Linux Platform Layer (`src/samyuj/files/` & `src/samyuj/platform/linux/`)**: Embedded user-space TLS NFSv3 server (`files/nfs/`), FUSE3 mount manager (`files/nfs/mount.py`, `files/nfs/fuse_bridge.py`), HTTPS/WSS file transfer (`files/transfer.py`), virtual display manager (`platform/linux/display.py`), clipboard sync (`platform/linux/clipboard.py`), and USB/ADB helpers (`platform/linux/usb_network.py`, `platform/linux/adb.py`).

## 2. Technology Stack & Engineering Rationale

| Technology | Where Used | Why We Chose It |
| :--- | :--- | :--- |
| **Python 3.11+ & `asyncio`** | Core runtime, TLS WebSocket, mDNS, NFSv3 RPC, HTTPS file transfer | Multiplexes 20+ concurrent network services on a single event-loop thread without thread-per-socket memory overhead or lock deadlocks. |
| **C++17 (`sc_fast.cpp` -> `libsc_fast.so`) via `ctypes`** | `frame_hub.py`, `capture.py`, `v4l2/`, `websocket.py`, `xdr.py` | Accelerates 1080p60 pixel manipulation (`sc_rgb24_to_bgrx32`, `sc_i420_pack_contiguous`, `sc_i420_to_yuyv`, `sc_copy_swap_uv`, `sc_ws_xor_mask`) using 64-bit word operations and `__restrict` pointers for SIMD auto-vectorization while maintaining 100% pure-Python fallbacks. |
| **`aiortc` & `PyAV` (`FFmpeg`)** | `media/runtime.py`, `services/media_pump.py` | Integrates WebRTC SDP offer/answer negotiation, DTLS-SRTP, ICE candidate injection, and RTCP REMB bandwidth estimation (`remb.py`) with C-level `libavcodec` H.264/VP8 decoding. |
| **Linux V4L2 (`v4l2loopback`)** | `media/v4l2/setup.py`, `media/v4l2/writer.py`, `sc_fast.cpp` | Exposes the phone camera as `/dev/video*` (`Samyuj Webcam`) with `exclusive_caps=1`, udev hotplug priming (`samyuj-v4l2-prime`), and a 5 FPS keepalive writer thread so Chrome, Zoom, Meet, and OBS never stall. |
| **User-Space RFC 1057/1813/1832 NFSv3-over-TLS** | `files/nfs/`, `protocol/nfs/xdr.py` | Implements `portmap` (100000), `mountd` (100005), and `nfsd` (100003) wrapped in TLS 1.2/1.3 (`dt2type=2`) as an unprivileged user without root `nfs-kernel-server` dependencies. |
| **FUSE3 (`fuse3` / `fusermount3`)** | `files/nfs/mount.py`, `samyuj-stop` | Mounts phone storage in user space at `~/.local/share/samyuj/mounts/<device>/live` for native Linux file manager browsing. |
| **GTK 3 (`PyGObject`), Cairo & Pango** | `ui/gtk/` | Native Linux desktop UI with Cairo vector icons, CSS themes, 60 Hz kinetic scrolling, and low memory footprint. |

## 3. End-to-End Program Control Flow

1. **Bootstrap (`samyuj` -> `bootstrap.py:main()` -> `run_gui_application()`)**:
   - Sets process name (`GLib.set_prgname("samyuj")`), installs `SIGTERM` handler, configures JSONL logging with secret redaction, and ensures `.desktop` integration.
   - Initializes `SamyujRuntime`, sets REMB bounds (6000/1500/8000 kbps), wraps in `ApplicationFacade`, starts `WedgeWatchdog(stall_limit_s=20.0)`, and launches `SamyujGtkDesktopApp`.
2. **Identity & Listener Startup (`RuntimeStartupMixin`)**:
   - Generates/loads RSA-2048 `identity.key.pem` (`0600`), `identity.cert.pem` (`0600`), `device_id.txt`, and `token.txt` (`0600`).
   - Starts the TLS control server on an ephemeral port (`TCP_NODELAY` enabled), starts `UsbMdnsAdvertiser` on UDP `5353` (`_readyfor._tcp.local.` & `_readyformdns._tcp.local.`), starts `BleAdvertiser` (`0x5246`), and polls USB RNDIS/NCM tether interfaces.
3. **Handshake & Authentication (`SessionRouterMixin`)**:
   - Upgrades `/readyfor/` to a control WebSocket (or `/readyfor/rdp` and `/readyfor/mdns` to a USB GATT bootstrap tunnel).
   - Drops any pre-`HELLO` frame with `serviceId != 0` (`UNAUTH_PRE_HELLO`).
   - Verifies `svc-0 cmd:0 HELLO` token via `hmac.compare_digest`, sends `REPLY_SENT(ok)`, completes `svc-0 cmd:1 EXCHANGE_KEY` (`shared_key.txt`), migrates state on Wi-Fi/USB switch (`_migrate_session_state`), runs `_connect_heal()`, and announces PC telemetry, display resolution, NFSv3-over-TLS ports, notification gates, and clipboard sync.
4. **Media & V4L2 Pipeline (`MediaRuntime` -> `FrameHub` -> `V4L2LoopbackWriter` / GTK)**:
   - Negotiates WebRTC SDP (preferring hardware H.264), decodes frames via `PyAV`, applies optional UV chroma swap and low-light luma boost, and routes frames to GTK/GStreamer windows and `/dev/video*`.
5. **File Transfer & NFSv3 Pipeline (`FileSharingServiceMixin` & `NfsRpcServer`)**:
   - Handles `svc-2` HTTPS/WSS file push/pull with fail-closed SHA-256 certificate pinning and 256 KB streaming chunks, and serves `svc-168` NFSv3-over-TLS exports with symlink escape prevention and SUID/SGID stripping.

