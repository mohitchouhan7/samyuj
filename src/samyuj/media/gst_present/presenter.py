from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time

DECODER_CHAIN = ("vah264dec", "vaapih264dec", "nvh264dec", "avdec_h264")
SINK_CHAINS = {
    "auto": ("gtk4paintablesink", "gtkglsink", "glimagesink", "xvimagesink", "ximagesink"),
    "gtk4": ("gtk4paintablesink",),
    "gtk": ("gtk4paintablesink", "gtkglsink"),
    "gl": ("glimagesink",),
    "x11": ("xvimagesink", "ximagesink"),
    "wayland": ("waylandsink",),
}

def _say(obj: dict) -> None:
    try:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()
    except Exception:
        pass

def _selfcheck() -> int:
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
    except Exception as exc:
        _say({"t": "error", "msg": f"GStreamer bindings unavailable: {exc!r} "
                                   "(sudo apt install python3-gi gstreamer1.0-plugins-base "
                                   "gstreamer1.0-plugins-good gstreamer1.0-gtk4 gstreamer1.0-libav)"})
        return 2
    Gst.init(None)
    have = {}
    for name in DECODER_CHAIN + tuple({s for chain in SINK_CHAINS.values() for s in chain}) + (
            "h264parse", "fdsrc", "identity"):
        have[name] = Gst.ElementFactory.find(name) is not None
    dec = next((d for d in DECODER_CHAIN if have.get(d)), None)
    sink = next((s for s in SINK_CHAINS["auto"] if have.get(s)), None)
    try:
        import gi as _gi
        _gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        have["gtk4"] = True
    except Exception:
        have["gtk4"] = False
    try:
        import gi as _gi
        _gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk as _Gtk3
        have["gtk3"] = True
    except Exception:
        have["gtk3"] = False
    _say({"t": "selfcheck", "elements": have, "chosen_decoder": dec,
          "chosen_sink": sink, "display": bool(os.environ.get("DISPLAY")
                                               or os.environ.get("WAYLAND_DISPLAY")),
          "ok": bool(dec and sink)})
    return 0 if (dec and sink) else 1

def _build(dec_name: str, sink_name: str, title: str, width: int, height: int):
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    window = None
    src = Gst.ElementFactory.make("fdsrc", "src")
    src.set_property("fd", 0)
    for k, v in (("blocksize", 65536), ("do-timestamp", True)):
        try:
            src.set_property(k, v)
        except Exception:
            pass
    parse = Gst.ElementFactory.make("h264parse", "parse")
    parse.set_property("config-interval", -1)
    dec = Gst.ElementFactory.make(dec_name, "dec")
    for k, v in (
        ("max-threads", 1),
        ("thread-type", 2),
        ("output-corrupt", False),
        ("low-latency", True),
        ("base-only", True),
    ):
        try:
            dec.set_property(k, v)
        except Exception:
            pass
    ident = Gst.ElementFactory.make("identity", "ident")
    ident.set_property("signal-handoffs", True)
    sink = Gst.ElementFactory.make(sink_name, "sink")
    if None in (src, parse, dec, ident, sink):
        raise RuntimeError(f"could not create elements ({sink_name}/{dec_name})")
    for k, v in (("sync", False), ("async", False), ("max-lateness", 20_000_000), ("qos", False)):
        try:
            sink.set_property(k, v)
        except Exception:
            pass

    if sink_name == "gtk4paintablesink":
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        paintable = sink.props.paintable
        if paintable is None:
            raise RuntimeError("gtk4paintablesink produced no Gdk.Paintable")
        window = Gtk.Window(title=title)
        window.set_default_size(max(320, width), max(240, height))
        picture = Gtk.Picture.new_for_paintable(paintable)
        window.set_child(picture)
        window.connect("close-request", lambda *_: _quit_now(window))
        window.present()
    elif sink_name == "gtkglsink":
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        window = Gtk.Window()
        window.set_title(title)
        window.set_default_size(max(320, width), max(240, height))
        widget = sink.props.widget
        if widget is None:
            raise RuntimeError("gtkglsink produced no widget")
        window.add(widget)
        window.connect("delete-event", lambda *_: _quit_now(window))
        window.show_all()

    pipe = Gst.Pipeline("sc-present")
    for el in (src, parse, dec, ident, sink):
        pipe.add(el)
    src.link(parse)
    parse.link(dec)
    dec.link(ident)
    ident.link(sink)
    return pipe, window, ident

def _quit_now(window=None) -> None:
    try:
        if window is not None:
            from gi.repository import Gtk
            if hasattr(Gtk, "main_quit"):
                Gtk.main_quit()
    except Exception:
        pass
    os._exit(0)

def main() -> int:
    ap = argparse.ArgumentParser(description='GStreamer H.264 decode+present sidecar (GTK 4 `gtk4paintablesink` + GTK 3 `gtkglsink`).')
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--title", default="Samyuj - GStreamer presenter")
    ap.add_argument("--sink", default="auto", choices=tuple(SINK_CHAINS))
    ap.add_argument("--decoder", default="auto")
    ap.add_argument("--width", type=int, default=412)
    ap.add_argument("--height", type=int, default=860)
    ap.add_argument("--stats-every", type=float, default=2.0)
    args = ap.parse_args()

    if args.selfcheck:
        return _selfcheck()

    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import GLib, Gst
    except Exception as exc:
        _say({"t": "error", "msg": f"gi/Gst unavailable: {exc!r}"})
        return 2
    Gst.init(None)

    dec_chain = DECODER_CHAIN
    if args.decoder != "auto":
        dec_chain = (args.decoder,) + tuple(d for d in DECODER_CHAIN if d != args.decoder)
    sink_chain = SINK_CHAINS.get(args.sink, SINK_CHAINS["auto"])

    pipe = window = None
    chosen_dec = chosen_sink = None
    last_err = ""
    for sink_name in sink_chain:
        for dec_name in dec_chain:
            try:
                pipe, window, ident = _build(dec_name, sink_name, args.title,
                                             args.width, args.height)
                chosen_dec, chosen_sink = dec_name, sink_name
                break
            except Exception as exc:
                last_err = f"{sink_name}/{dec_name}: {exc!r}"
                pipe = window = None
                continue
        if pipe is not None:
            break
    if pipe is None:
        _say({"t": "error", "msg": f"no working decoder/sink combination ({last_err})"})
        return 3

    loop = GLib.MainLoop()
    stats = {"decoded": 0, "window_t0": time.monotonic(), "last_print": time.monotonic()}

    def _handoff(_ident, buf, _pad):
        stats["decoded"] += 1

    ident.connect("handoff", _handoff)

    def _stats_tick():
        now = time.monotonic()
        dt = now - stats["last_print"]
        if dt > 0:
            _say({"t": "stats", "fps": round(stats["decoded"] / dt, 2),
                  "decoded": stats["decoded"],
                  "sink": chosen_sink, "decoder": chosen_dec})
        stats["decoded"] = 0
        stats["last_print"] = now
        return True

    def _on_message(_bus, msg):
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            _say({"t": "error", "msg": f"{err.message}", "debug": (dbg or "")[:500]})
            loop.quit()
        elif t == Gst.MessageType.EOS:
            _say({"t": "exit", "why": "eos (parent closed stdin)"})
            loop.quit()
        elif t == Gst.MessageType.WARNING:
            err, dbg = msg.parse_warning()
            _say({"t": "warning", "msg": str(err.message)[:300]})

    bus = pipe.get_bus()
    bus.add_signal_watch()
    bus.connect("message", _on_message)

    signal.signal(signal.SIGTERM, lambda *_: loop.quit())
    signal.signal(signal.SIGINT, lambda *_: loop.quit())

    ret = pipe.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        _say({"t": "error", "msg": "pipeline failed to start"})
        return 4
    _say({"t": "ready", "sink": chosen_sink, "decoder": chosen_dec,
          "window": window is not None, "pid": os.getpid()})
    GLib.timeout_add(int(max(0.5, args.stats_every) * 1000), _stats_tick)

    if window is not None:
        from gi.repository import Gtk
        try:
            if hasattr(Gtk, "main"):
                Gtk.main()
            else:
                loop.run()
        except Exception:
            loop.run()
    else:
        loop.run()
    try:
        pipe.set_state(Gst.State.NULL)
    except Exception:
        pass
    return 0

if __name__ == "__main__":
    sys.exit(main())
