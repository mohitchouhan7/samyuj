from __future__ import annotations

from typing import Any


class _EnumNamespace:
    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getattr__(self, _name: str) -> int:
        return 0


class _StyleContext:
    def add_class(self, _cls: str) -> None:
        pass

    def remove_class(self, _cls: str) -> None:
        pass

    @staticmethod
    def add_provider_for_screen(_scr: Any, _prov: Any, _prio: int) -> None:
        pass

    @staticmethod
    def add_provider_for_display(_disp: Any, _prov: Any, _prio: int) -> None:
        pass


class _Adjustment:
    def __init__(self, value: float = 0.0, lower: float = 0.0, upper: float = 100.0, page_size: float = 10.0) -> None:
        self._value = float(value)
        self._lower = float(lower)
        self._upper = float(upper)
        self._page_size = float(page_size)

    def get_value(self) -> float:
        return self._value

    def set_value(self, val: float) -> None:
        self._value = float(val)

    def get_lower(self) -> float:
        return self._lower

    def get_upper(self) -> float:
        return self._upper

    def get_page_size(self) -> float:
        return self._page_size

    def connect(self, _sig: str, _cb: Any, *_args: Any) -> int:
        return 1

    def __getattr__(self, name: str) -> Any:
        if name.startswith(("set_", "get_", "add_", "drag_", "grab_", "ungrab_", "append_", "expand_", "collapse_")) or name in (
            "move", "fullscreen", "unfullscreen", "maximize", "unmaximize", "realize", "popup", "popdown", "put"
        ):
            def _noop(*_a: Any, **_kw: Any) -> Any:
                return None
            return _noop
        raise AttributeError(name)


class _Allocation:
    width: int = 860
    height: int = 540


class _StubWidget:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._children: list[Any] = []
        self._visible: bool = True
        self._sensitive: bool = True
        self._text: str = str(kwargs.get("label") or kwargs.get("title") or (args[0] if args and isinstance(args[0], str) else ""))
        self._markup: str = self._text
        self._active: bool = False
        self._active_id: str | None = None
        self._style_ctx = _StyleContext()
        self._signals: dict[str, list[Any]] = {}
        self._size_req: tuple[int, int] = (-1, -1)
        self._default_size: tuple[int, int] = (860, 540)
        self._skip_taskbar: bool = False
        self._skip_pager: bool = False
        self._orientation: int = int(kwargs.get("orientation", 0))
        self._vadj = _Adjustment()
        self._hadj = _Adjustment()
        self._named_children: dict[str, Any] = {}
        self._visible_child_name: str = ""

    def get_style_context(self) -> _StyleContext:
        return self._style_ctx

    def connect(self, sig: str, cb: Any, *args: Any) -> int:
        self._signals.setdefault(sig, []).append((cb, args))
        return len(self._signals[sig])

    def disconnect(self, _handler_id: int) -> None:
        pass

    def add(self, child: Any) -> None:
        if child not in self._children:
            self._children.append(child)

    def set_child(self, child: Any) -> None:
        self._children = [child] if child is not None else []

    def append(self, child: Any, *_args: Any) -> None:
        if child not in self._children:
            self._children.append(child)

    def remove(self, child: Any) -> None:
        if child in self._children:
            self._children.remove(child)

    def pack_start(self, child: Any, *_args: Any, **_kwargs: Any) -> None:
        self.append(child)

    def pack_end(self, child: Any, *_args: Any, **_kwargs: Any) -> None:
        self.append(child)

    def attach(self, child: Any, *_args: Any, **_kwargs: Any) -> None:
        self.append(child)

    def insert(self, child: Any, _pos: int = -1) -> None:
        self.append(child)

    def get_children(self) -> list[Any]:
        return list(self._children)

    def get_child(self) -> Any:
        return self._children[0] if self._children else None

    def show(self) -> None:
        self._visible = True

    def show_all(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False

    def set_visible(self, v: bool) -> None:
        self._visible = bool(v)

    def get_visible(self) -> bool:
        return self._visible

    def set_no_show_all(self, _v: bool) -> None:
        pass

    def set_sensitive(self, v: bool) -> None:
        self._sensitive = bool(v)

    def get_sensitive(self) -> bool:
        return self._sensitive

    def set_text(self, text: str) -> None:
        self._text = str(text)

    def get_text(self) -> str:
        return self._text

    def set_label(self, text: str) -> None:
        self._text = str(text)

    def get_label(self) -> str:
        return self._text

    def set_markup(self, markup: str) -> None:
        self._markup = str(markup)
        import re
        self._text = re.sub(r"<[^>]+>", "", self._markup)

    def set_active(self, val: Any) -> None:
        self._active = bool(val)

    def get_active(self) -> bool:
        return self._active

    def set_active_id(self, val: str) -> bool:
        self._active_id = str(val)
        return True

    def get_active_id(self) -> str | None:
        return self._active_id

    def get_active_text(self) -> str | None:
        return self._active_id or self._text

    def append_text(self, text: str) -> None:
        if not self._text:
            self._text = str(text)

    def remove_all(self) -> None:
        self._children.clear()

    def clear(self) -> None:
        self._children.clear()

    def add_titled(self, child: Any, name: str, _title: str) -> None:
        self._named_children[name] = child
        self.append(child)
        if not self._visible_child_name:
            self._visible_child_name = name

    def add_named(self, child: Any, name: str) -> None:
        self._named_children[name] = child
        self.append(child)
        if not self._visible_child_name:
            self._visible_child_name = name

    def set_visible_child_name(self, name: str) -> None:
        self._visible_child_name = str(name)

    def get_visible_child_name(self) -> str:
        return self._visible_child_name

    def get_child_by_name(self, name: str) -> Any:
        return self._named_children.get(name)

    def set_size_request(self, w: int, h: int) -> None:
        self._size_req = (int(w), int(h))

    def set_default_size(self, w: int, h: int) -> None:
        self._default_size = (int(w), int(h))

    def get_default_size(self) -> tuple[int, int]:
        return self._default_size

    def resize(self, w: int, h: int) -> None:
        self._default_size = (int(w), int(h))

    def get_size(self) -> tuple[int, int]:
        return self._default_size

    def get_width(self) -> int:
        return self._default_size[0]

    def get_height(self) -> int:
        return self._default_size[1]

    def get_allocated_width(self) -> int:
        return self._default_size[0]

    def get_allocated_height(self) -> int:
        return self._default_size[1]

    def get_allocation(self) -> _Allocation:
        return _Allocation()

    def set_skip_taskbar_hint(self, val: bool) -> None:
        self._skip_taskbar = bool(val)

    def get_skip_taskbar_hint(self) -> bool:
        return self._skip_taskbar

    def set_skip_pager_hint(self, val: bool) -> None:
        self._skip_pager = bool(val)

    def get_skip_pager_hint(self) -> bool:
        return self._skip_pager

    def get_vadjustment(self) -> _Adjustment:
        return self._vadj

    def get_hadjustment(self) -> _Adjustment:
        return self._hadj

    def get_orientation(self) -> int:
        return self._orientation

    def get_window(self) -> None:
        return None

    def get_content_area(self) -> _StubWidget:
        return self

    def destroy(self) -> None:
        self._visible = False

    def queue_draw(self) -> None:
        pass

    def present(self) -> None:
        self._visible = True

    def deiconify(self) -> None:
        self._visible = True

    def iconify(self) -> None:
        pass

    @classmethod
    def new(cls, *_args: Any, **_kwargs: Any) -> _StubWidget:
        return cls()

    @classmethod
    def new_with_label(cls, label: str = "") -> _StubWidget:
        return cls(label=label)

    @classmethod
    def new_from_pixbuf(cls, _pb: Any = None) -> _StubWidget:
        return cls()

    @classmethod
    def new_from_icon_name(cls, _name: str = "", _size: int = 0) -> _StubWidget:
        return cls()

    def __getattr__(self, name: str) -> Any:
        if name.startswith(("set_", "get_", "add_", "drag_", "grab_", "ungrab_", "append_", "expand_", "collapse_")) or name in (
            "move", "fullscreen", "unfullscreen", "maximize", "unmaximize", "realize", "popup", "popdown", "put"
        ):
            def _noop(*_a: Any, **_kw: Any) -> Any:
                return None
            return _noop
        raise AttributeError(name)


class _StubCssProvider:
    def load_from_data(self, _data: bytes) -> None:
        pass

    def load_from_bytes(self, _gbytes: Any) -> None:
        pass


class _StubClipboard:
    _text: str = ""

    @classmethod
    def get(cls, _sel: Any = None) -> _StubClipboard:
        return cls()

    def set_text(self, text: str, _len: int = -1) -> None:
        self._text = str(text)

    def wait_for_text(self) -> str:
        return self._text


class _StubSettings:
    @classmethod
    def get_default(cls) -> _StubSettings:
        return cls()

    def set_property(self, _k: str, _v: Any) -> None:
        pass

    def get_property(self, _k: str) -> Any:
        return None


class _StubPixbuf:
    def __init__(self, width: int = 64, height: int = 64) -> None:
        self._w = int(width)
        self._h = int(height)

    def get_width(self) -> int:
        return self._w

    def get_height(self) -> int:
        return self._h

    def get_rowstride(self) -> int:
        return self._w * 4

    def get_n_channels(self) -> int:
        return 4

    def get_has_alpha(self) -> bool:
        return True

    def get_pixels(self) -> bytes:
        return bytes(self._w * self._h * 4)

    def scale_simple(self, tw: int, th: int, _interp: Any = None) -> _StubPixbuf:
        return _StubPixbuf(tw, th)

    @classmethod
    def new_from_bytes(cls, _b: Any, _cs: Any, _alpha: bool, _bps: int, w: int, h: int, _rs: int) -> _StubPixbuf:
        return cls(w, h)

    @classmethod
    def new_from_file_at_scale(cls, _path: str, w: int, h: int, _preserve: bool) -> _StubPixbuf:
        return cls(max(1, w), max(1, h))

    @classmethod
    def new_from_file(cls, _path: str) -> _StubPixbuf:
        return cls(64, 64)


class _StubPixbufLoader:
    @classmethod
    def new_with_type(cls, _t: str) -> _StubPixbufLoader:
        return cls()

    def write(self, _b: bytes) -> None:
        pass

    def close(self) -> None:
        pass

    def get_pixbuf(self) -> _StubPixbuf:
        return _StubPixbuf(64, 64)


class _StubVariant:
    def __init__(self, _sig: str, value: Any) -> None:
        self._value = value

    def unpack(self) -> Any:
        def _u(v: Any) -> Any:
            if isinstance(v, _StubVariant):
                return _u(v._value)
            if isinstance(v, tuple):
                return tuple(_u(x) for x in v)
            if isinstance(v, list):
                return [_u(x) for x in v]
            if isinstance(v, dict):
                return {k: _u(val) for k, val in v.items()}
            return v
        return _u(self._value)


class _StubBytes:
    @staticmethod
    def new(data: bytes) -> bytes:
        return bytes(data)


class _StubMainContext:
    @classmethod
    def default(cls) -> _StubMainContext:
        return cls()

    def pending(self) -> bool:
        return False

    def iteration(self, _blocking: bool = False) -> bool:
        return False


class Gtk:
    Window = _StubWidget
    Widget = _StubWidget
    Box = _StubWidget
    Button = _StubWidget
    ToggleButton = _StubWidget
    RadioButton = _StubWidget
    CheckButton = _StubWidget
    Switch = _StubWidget
    Label = _StubWidget
    Entry = _StubWidget
    Image = _StubWidget
    DrawingArea = _StubWidget
    ScrolledWindow = _StubWidget
    Frame = _StubWidget
    Overlay = _StubWidget
    EventBox = _StubWidget
    FlowBox = _StubWidget
    Grid = _StubWidget
    HeaderBar = _StubWidget
    Layout = _StubWidget
    Stack = _StubWidget
    Popover = _StubWidget
    Menu = _StubWidget
    MenuButton = _StubWidget
    MenuItem = _StubWidget
    SeparatorMenuItem = _StubWidget
    ComboBoxText = _StubWidget
    Dialog = _StubWidget
    MessageDialog = _StubWidget
    FileChooserDialog = _StubWidget
    ListStore = _StubWidget
    TreeView = _StubWidget
    TreeViewColumn = _StubWidget
    CellRendererText = _StubWidget
    TreePath = _StubWidget
    StatusIcon = _StubWidget
    EventControllerScroll = _StubWidget
    TargetEntry = _StubWidget
    CssProvider = _StubCssProvider
    StyleContext = _StyleContext
    Clipboard = _StubClipboard
    Settings = _StubSettings

    Align = _EnumNamespace(FILL=0, START=1, END=2, CENTER=3)
    Orientation = _EnumNamespace(HORIZONTAL=0, VERTICAL=1)
    PolicyType = _EnumNamespace(AUTOMATIC=0, NEVER=1, ALWAYS=2)
    ReliefStyle = _EnumNamespace(NORMAL=0, HALF=1, NONE=2)
    WindowPosition = _EnumNamespace(NONE=0, CENTER=1, MOUSE=2, CENTER_ALWAYS=3, CENTER_ON_PARENT=4)
    WindowType = _EnumNamespace(TOPLEVEL=0, POPUP=1)
    SelectionMode = _EnumNamespace(NONE=0, SINGLE=1, BROWSE=2, MULTIPLE=3)
    ShadowType = _EnumNamespace(NONE=0, IN=1, OUT=2, ETCHED_IN=3, ETCHED_OUT=4)
    StackTransitionType = _EnumNamespace(NONE=0, CROSSFADE=1, SLIDE_LEFT_RIGHT=2)
    Justification = _EnumNamespace(LEFT=0, RIGHT=1, CENTER=2, FILL=3)
    ResponseType = _EnumNamespace(OK=-5, CANCEL=-6, CLOSE=-7, YES=-8, NO=-9)
    ButtonsType = _EnumNamespace(NONE=0, OK=1, CLOSE=2, CANCEL=3, YES_NO=4, OK_CANCEL=5)
    MessageType = _EnumNamespace(INFO=0, WARNING=1, QUESTION=2, ERROR=3)
    DialogFlags = _EnumNamespace(MODAL=1, DESTROY_WITH_PARENT=2)
    FileChooserAction = _EnumNamespace(OPEN=0, SAVE=1, SELECT_FOLDER=2, CREATE_FOLDER=3)
    DestDefaults = _EnumNamespace(MOTION=1, HIGHLIGHT=2, DROP=4, ALL=7)
    EventControllerScrollFlags = _EnumNamespace(NONE=0, VERTICAL=1, HORIZONTAL=2, DISCRETE=4, KINETIC=8, BOTH_AXES=3)
    PropagationPhase = _EnumNamespace(NONE=0, CAPTURE=1, BUBBLE=2, TARGET=3)

    STOCK_CANCEL = "gtk-cancel"
    STOCK_OPEN = "gtk-open"
    STYLE_PROVIDER_PRIORITY_APPLICATION = 600

    @staticmethod
    def init_check(argv: list[str] | None = None) -> tuple[bool, list[str]]:
        return True, list(argv or [])

    @staticmethod
    def get_major_version() -> int:
        return 3

    @staticmethod
    def get_minor_version() -> int:
        return 24

    @staticmethod
    def get_micro_version() -> int:
        return 0

    @staticmethod
    def events_pending() -> bool:
        return False

    @staticmethod
    def main_iteration_do(_blocking: bool = False) -> bool:
        return False

    @staticmethod
    def get_current_event_time() -> int:
        return 0

    @staticmethod
    def drag_finish(_ctx: Any, _success: bool, _del: bool, _time: int) -> None:
        pass


class Gdk:
    Cursor = _EnumNamespace(new_from_name=lambda *_a: None, new_for_display=lambda *_a: None)
    CursorType = _EnumNamespace(BLANK_CURSOR=-2, LEFT_PTR=68)
    Display = _EnumNamespace(get_default=lambda: None)
    Screen = _EnumNamespace(get_default=lambda: None)
    DragAction = _EnumNamespace(COPY=1, MOVE=2, LINK=4)
    EventMask = _EnumNamespace(
        BUTTON_PRESS_MASK=256,
        BUTTON_RELEASE_MASK=512,
        POINTER_MOTION_MASK=4,
        SCROLL_MASK=2097152,
        SMOOTH_SCROLL_MASK=4194304,
        KEY_PRESS_MASK=1024,
        KEY_RELEASE_MASK=2048,
        ENTER_NOTIFY_MASK=4096,
        LEAVE_NOTIFY_MASK=8192,
        STRUCTURE_MASK=131072,
    )
    EventButton = object
    EventCrossing = object
    EventKey = object
    EventMotion = object
    EventScroll = object
    GrabStatus = _EnumNamespace(SUCCESS=0)
    ModifierType = _EnumNamespace(SHIFT_MASK=1, CONTROL_MASK=4, MOD1_MASK=8, SUPER_MASK=67108864)
    ScrollDirection = _EnumNamespace(UP=0, DOWN=1, LEFT=2, RIGHT=3, SMOOTH=4)
    SeatCapabilities = _EnumNamespace(ALL=7, POINTER=1, KEYBOARD=4)
    Texture = _EnumNamespace()
    WindowState = _EnumNamespace(ICONIFIED=2, MAXIMIZED=4, FULLSCREEN=16)
    WindowTypeHint = _EnumNamespace(NORMAL=0, DIALOG=1, MENU=2, TOOLBAR=3, UTILITY=5, DOCK=6, POPUP_MENU=8)
    SELECTION_CLIPBOARD = "CLIPBOARD"
    SELECTION_PRIMARY = "PRIMARY"

    @staticmethod
    def cairo_set_source_pixbuf(_cr: Any, _pb: Any, _x: float, _y: float) -> None:
        pass

    @staticmethod
    def keyval_name(_kv: int) -> str:
        return ""


class GdkPixbuf:
    Colorspace = _EnumNamespace(RGB=0)
    InterpType = _EnumNamespace(NEAREST=0, TILES=1, BILINEAR=2, HYPER=3)
    Pixbuf = _StubPixbuf
    PixbufLoader = _StubPixbufLoader


class GLib:
    Bytes = _StubBytes
    MainContext = _StubMainContext
    Variant = _StubVariant
    VariantType = _EnumNamespace(new=lambda s: s)
    PRIORITY_DEFAULT_IDLE = 200

    @staticmethod
    def idle_add(_cb: Any, *_args: Any, **_kwargs: Any) -> int:
        return 1

    @staticmethod
    def timeout_add(_interval: int, _cb: Any, *_args: Any) -> int:
        return 1

    @staticmethod
    def source_remove(_sid: int) -> bool:
        return True


class Gio:
    BusNameOwnerFlags = _EnumNamespace(NONE=0, ALLOW_REPLACEMENT=1, REPLACE=2)
    BusType = _EnumNamespace(SESSION=2, SYSTEM=1)
    DBusCallFlags = _EnumNamespace(NONE=0)
    DBusConnection = object
    DBusNodeInfo = _EnumNamespace(new_for_xml=lambda _xml: _EnumNamespace(interfaces=[None]))

    @staticmethod
    def bus_get_sync(*_args: Any) -> Any:
        raise RuntimeError("No DBus session in stub mode")

    @staticmethod
    def bus_own_name_on_connection(*_args: Any) -> int:
        return 0

    @staticmethod
    def bus_unown_name(_id: int) -> None:
        pass


class Pango:
    EllipsizeMode = _EnumNamespace(NONE=0, START=1, MIDDLE=2, END=3)
