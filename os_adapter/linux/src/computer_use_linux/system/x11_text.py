"""Read-only XKB text plans. No keymap mutation, composition, or clipboard use.

XkbTranslateKeyCode uses the current lookup modifiers and group, rather than a
column in the core keymap (which is not a shift level across layouts). The C
layouts below follow X11/extensions/XKBstr.h; only owned XkbGetMap allocations
are inspected, and XkbFreeKeyboard releases all of them after each snapshot.
"""

from __future__ import annotations

import ctypes as C
import ctypes.util
import hashlib
from dataclasses import dataclass, replace

from computer_use_linux.contracts.protocol import Failure, require


@dataclass(frozen=True)
class KeyboardState:
    group: int = 0
    locked_group: int = 0
    base_group: int = 0
    latched_group: int = 0
    mods: int = 0
    base_mods: int = 0
    latched_mods: int = 0
    locked_mods: int = 0
    lookup_mods: int = 0

    def without_shift(self):
        return replace(self, mods=self.mods & ~1, base_mods=self.base_mods & ~1, lookup_mods=self.lookup_mods & ~1)


@dataclass(frozen=True)
class XkbSnapshot:
    generation: object
    state: KeyboardState
    symbols: dict
    shift: int | None


@dataclass(frozen=True)
class TextPlan:
    snapshot: XkbSnapshot
    keys: tuple


def plan_text(text, snapshot):
    require(isinstance(text, str), "Text must be a string.")
    state = snapshot.state
    require(
        not state.base_mods and not state.base_group and not state.latched_mods and not state.latched_group,
        "A depressed or latched keyboard modifier makes text input ambiguous.",
        "input_unavailable",
    )
    choices = {}
    for (code, shifted), symbol in sorted(snapshot.symbols.items(), key=lambda item: (item[0][1], item[0][0])):
        if not shifted or snapshot.shift is not None:
            choices.setdefault(symbol, (code, shifted))
    keys = []
    for char in text:
        value = ord(char)
        require(
            value >= 32 and value != 127 and not 0xD800 <= value <= 0xDFFF and value in choices,
            "Text is not directly representable by the current XKB layout; use native text editing or explicit paste.",
            "input_unavailable",
        )
        keys.append(choices[value])
    return TextPlan(snapshot, tuple(keys))


def check_plan(plan, snapshot, shifted):
    state = snapshot.state
    require(
        snapshot.generation == plan.snapshot.generation
        and snapshot.shift == plan.snapshot.shift
        and snapshot.symbols == plan.snapshot.symbols
        and state.without_shift() == plan.snapshot.state.without_shift()
        and state.base_mods == (1 if shifted else 0)
        and bool(state.mods & 1) == shifted
        and bool(state.lookup_mods & 1) == shifted,
        "Keyboard layout or modifier state changed; text input stopped.",
        "input_unavailable",
    )


class _State(C.Structure):
    _fields_ = [
        ("group", C.c_ubyte),
        ("locked_group", C.c_ubyte),
        ("base_group", C.c_ushort),
        ("latched_group", C.c_ushort),
        ("mods", C.c_ubyte),
        ("base_mods", C.c_ubyte),
        ("latched_mods", C.c_ubyte),
        ("locked_mods", C.c_ubyte),
        ("compat_state", C.c_ubyte),
        ("grab_mods", C.c_ubyte),
        ("compat_grab_mods", C.c_ubyte),
        ("lookup_mods", C.c_ubyte),
        ("compat_lookup_mods", C.c_ubyte),
        ("ptr_buttons", C.c_ushort),
    ]


class _Mods(C.Structure):
    _fields_ = [("mask", C.c_ubyte), ("real_mods", C.c_ubyte), ("vmods", C.c_ushort)]


class _Controls(C.Structure):
    # Only the prefix is read; libX11 allocates/frees the complete native record.
    _fields_ = [
        ("mk_dflt_btn", C.c_ubyte),
        ("num_groups", C.c_ubyte),
        ("groups_wrap", C.c_ubyte),
        ("internal", _Mods),
        ("ignore_lock", _Mods),
    ]


class _Entry(C.Structure):
    _fields_ = [("active", C.c_int), ("level", C.c_ubyte), ("mods", _Mods)]


class _Type(C.Structure):
    _fields_ = [
        ("mods", _Mods),
        ("num_levels", C.c_ubyte),
        ("map_count", C.c_ubyte),
        ("map", C.POINTER(_Entry)),
        ("preserve", C.POINTER(_Mods)),
        ("name", C.c_ulong),
        ("level_names", C.POINTER(C.c_ulong)),
    ]


class _SymbolMap(C.Structure):
    _fields_ = [("kt_index", C.c_ubyte * 4), ("group_info", C.c_ubyte), ("width", C.c_ubyte), ("offset", C.c_ushort)]


class _ClientMap(C.Structure):
    _fields_ = [
        ("size_types", C.c_ubyte),
        ("num_types", C.c_ubyte),
        ("types", C.POINTER(_Type)),
        ("size_syms", C.c_ushort),
        ("num_syms", C.c_ushort),
        ("syms", C.POINTER(C.c_ulong)),
        ("key_sym_map", C.POINTER(_SymbolMap)),
        ("modmap", C.POINTER(C.c_ubyte)),
    ]


class _ServerMap(C.Structure):
    _fields_ = [
        ("num_acts", C.c_ushort),
        ("size_acts", C.c_ushort),
        ("acts", C.POINTER(C.c_ubyte * 8)),
        ("behaviors", C.POINTER(C.c_ubyte * 2)),
        ("key_acts", C.POINTER(C.c_ushort)),
        ("explicit", C.POINTER(C.c_ubyte)),
        ("vmods", C.c_ubyte * 16),
        ("vmodmap", C.POINTER(C.c_ushort)),
    ]


class _Description(C.Structure):
    _fields_ = [
        ("dpy", C.c_void_p),
        ("flags", C.c_ushort),
        ("device_spec", C.c_ushort),
        ("min_key_code", C.c_ubyte),
        ("max_key_code", C.c_ubyte),
        ("ctrls", C.c_void_p),
        ("server", C.POINTER(_ServerMap)),
        ("map", C.POINTER(_ClientMap)),
        ("indicators", C.c_void_p),
        ("names", C.c_void_p),
        ("compat", C.c_void_p),
        ("geom", C.c_void_p),
    ]


class XkbKeyboard:
    """A private read-only libX11 connection; the caller owns event dispatch."""

    def __init__(self, display_name):
        self.display = None
        try:
            self.lib = C.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
            self.unicode = C.CDLL(ctypes.util.find_library("xkbcommon") or "libxkbcommon.so.0")
            self.unicode.xkb_keysym_to_utf32.argtypes = [C.c_uint32]
            self.unicode.xkb_keysym_to_utf32.restype = C.c_uint32
            self.lib.XOpenDisplay.argtypes, self.lib.XOpenDisplay.restype = [C.c_char_p], C.c_void_p
            self.lib.XCloseDisplay.argtypes = [C.c_void_p]
            self.lib.XkbGetState.argtypes = [C.c_void_p, C.c_uint, C.POINTER(_State)]
            self.lib.XkbGetMap.argtypes = [C.c_void_p, C.c_uint, C.c_uint]
            self.lib.XkbGetMap.restype = C.POINTER(_Description)
            self.lib.XkbGetControls.argtypes = [C.c_void_p, C.c_ulong, C.POINTER(_Description)]
            self.lib.XkbFreeKeyboard.argtypes = [C.POINTER(_Description), C.c_uint, C.c_int]
            self.lib.XkbTranslateKeyCode.argtypes = [
                C.POINTER(_Description),
                C.c_ubyte,
                C.c_uint,
                C.POINTER(C.c_uint),
                C.POINTER(C.c_ulong),
            ]
            self.display = self.lib.XOpenDisplay(
                display_name.encode() if isinstance(display_name, str) else display_name
            )
            require(self.display, "Cannot open the XKB display.", "input_unavailable")
        except (OSError, AttributeError) as error:
            self.close()
            raise Failure("XKB text mapping requires libX11 and libxkbcommon.", "input_unavailable") from error

    def state(self):
        value = _State()
        require(
            self.lib.XkbGetState(self.display, 0x100, C.byref(value)) == 0,
            "Cannot read XKB state.",
            "input_unavailable",
        )
        return KeyboardState(**{name: getattr(value, name) for name in KeyboardState.__dataclass_fields__})

    @staticmethod
    def map_facts(description):
        """Copy logical fields, never addresses or padding, into a stable digest."""
        client, server = description.map.contents, description.server.contents

        def mods(value):
            return value.mask, value.real_mods, value.vmods

        types = []
        for item in client.types[: client.num_types]:
            types.append(
                (
                    mods(item.mods),
                    item.num_levels,
                    tuple((e.active, e.level, mods(e.mods)) for e in item.map[: item.map_count]),
                    tuple(mods(e) for e in item.preserve[: item.map_count]) if item.preserve else (),
                )
            )
        count = description.max_key_code + 1
        symbols = tuple((tuple(m.kt_index), m.group_info, m.width, m.offset) for m in client.key_sym_map[:count])
        return (
            description.min_key_code,
            description.max_key_code,
            types,
            symbols,
            tuple(client.syms[: client.num_syms]),
            bytes(client.modmap[:count]),
            tuple(bytes(a) for a in server.acts[: server.num_acts]),
            tuple(bytes(b) for b in server.behaviors[:count]),
            tuple(server.key_acts[:count]),
            bytes(server.vmods),
            tuple(server.vmodmap[:count]),
        )

    def snapshot(self):
        before = self.state()
        pointer = self.lib.XkbGetMap(self.display, 0xFF, 0x100)
        require(pointer, "Cannot read the current XKB map.", "input_unavailable")
        try:
            description = pointer.contents
            require(description.map and description.server, "Incomplete XKB map.", "input_unavailable")
            require(
                self.lib.XkbGetControls(self.display, 1 << 28, pointer) == 0 and description.ctrls,
                "Cannot read XKB internal modifiers.",
                "input_unavailable",
            )
            internal = C.cast(description.ctrls, C.POINTER(_Controls)).contents.internal.mask
            # KeyPress lookup modifiers are effective mods minus internal mods
            # (XKB spec 5.2). XkbGetState.lookup_mods is zero/stale on some servers
            # after XkbLockModifiers, despite actual KeyPress.state containing
            # CapsLock. Derive the specified value from its two native inputs.
            lookup_mods = before.mods & ~internal
            client, server = description.map.contents, description.server.contents
            symbols, shifts, num_lock_mask = {}, [], 0
            # XKB actions can redirect keys, change groups or lock modifiers.
            # Exclude such text keys even if their displayed symbol looks safe.
            for code in range(description.min_key_code, description.max_key_code + 1):
                entry = client.key_sym_map[code]
                count = entry.width * (entry.group_info & 15)
                keysyms = client.syms[entry.offset : entry.offset + count]
                actions = (
                    tuple(bytes(a) for a in server.acts[server.key_acts[code] : server.key_acts[code] + count])
                    if server.key_acts[code]
                    else ()
                )
                behavior = bytes(server.behaviors[code])
                if 0xFF7F in keysyms:  # Num_Lock, not assumed to be Mod2.
                    num_lock_mask |= client.modmap[code]
                ordinary_shift = (
                    client.modmap[code] == 1
                    and behavior == b"\0\0"
                    and any(symbol in (0xFFE1, 0xFFE2) for symbol in keysyms)
                    and actions
                    and all(a[0] == 1 and not a[1] & ~5 and a[2] == 1 and not a[4] and not a[5] for a in actions)
                )
                if ordinary_shift and not internal & 1:
                    shifts.append(code)
                if client.modmap[code] or behavior != b"\0\0" or any(a[0] for a in actions):
                    continue
                for shifted in (False, True):
                    mods = ((lookup_mods & ~1) | int(shifted)) & ~internal
                    consumed, symbol = C.c_uint(), C.c_ulong()
                    if self.lib.XkbTranslateKeyCode(
                        pointer, code, mods | (before.group << 13), C.byref(consumed), C.byref(symbol)
                    ):
                        value = self.unicode.xkb_keysym_to_utf32(symbol.value)
                        if value >= 32 and value != 127:
                            symbols[code, shifted] = int(value)
            require(
                not before.locked_mods & ~(2 | (num_lock_mask & 16)),
                "A locked shortcut modifier makes text input ambiguous.",
                "input_unavailable",
            )
            require(before == self.state(), "XKB state changed while planning text.", "input_unavailable")
            digest = hashlib.sha256(repr((self.map_facts(description), internal)).encode()).hexdigest()
            return XkbSnapshot(
                digest, replace(before, lookup_mods=lookup_mods), symbols, min(shifts) if shifts else None
            )
        finally:
            self.lib.XkbFreeKeyboard(pointer, 0x7F, True)

    def close(self):
        if self.display:
            self.lib.XCloseDisplay(self.display)
            self.display = None


def keyboard_for(desktop):
    if getattr(desktop, "text_keyboard", None) is None:
        desktop.text_keyboard = XkbKeyboard(desktop.d.get_display_name())
    return desktop.text_keyboard
