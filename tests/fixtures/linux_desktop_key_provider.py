"""Native OS seams for desktop input; production capture/engine/sequence run."""

import json
import signal
import sys
from copy import copy
from pathlib import Path

from linux_native_provider import PointerBackend, PointerDesktop

from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.objects.registry import Window
from computer_use_linux.runtime.server import Server
from computer_use_linux.system.x11 import X11


class Desktop(PointerDesktop):
    press_key = X11.press_key

    def __init__(self, registry, windows, mode):
        super().__init__(registry, windows, mode)
        self.keymap_missing = set()
        self.pump_hook = None
        self.texts = {11: "first:", 22: "second:"}
        self.text_journal = (
            Path(sys.argv[sys.argv.index("--desktop-text-journal") + 1])
            if "--desktop-text-journal" in sys.argv
            else None
        )
        self.items.append(Window(22, "Other", "Fixture", 42, (680, 0, 300, 300), process_start="generation"))

    def pump(self):
        if self.pump_hook:
            self.pump_hook()

    def info(self, xid):
        item = next((w for w in self.items if w.key == xid), None)
        if item is None:
            raise Failure("Window missing", "stale_target")
        return copy(item)

    def keycode(self, name):
        if name in self.keymap_missing:
            raise Failure("Unmapped key", "input_unavailable")
        return name

    def _event(self, kind, detail=0, x=0, y=0):
        if kind in (self.X.KeyPress, self.X.KeyRelease):
            if kind == self.X.KeyPress and type(detail) is int and detail != self.text_keyboard.shift:
                symbol = self.text_keyboard.snapshot().symbols.get((detail, self.text_keyboard.shift in self.keys))
                if symbol and self.keyboard_window in self.texts:
                    self.texts[self.keyboard_window] += chr(symbol)
            detail = self.text_keyboard.key_name(detail)
        super()._event(kind, detail, x, y)
        if kind == self.X.KeyRelease and "--desktop-key-release-failed" in sys.argv:
            raise Failure("Synthetic key release unavailable", "execution_unknown", effect="possible")
        if kind == self.X.ButtonPress:
            # Substitute the fixture's real click-to-focus effect, not SDK routing.
            self.foreground = self.keyboard_window = 11 if self.position[0] < 640 else 22
        if self.text_journal is not None:
            with self.text_journal.open("a") as stream:
                stream.write(json.dumps(dict(kind=kind, detail=detail, texts=self.texts)) + "\n")


class Backend(PointerBackend):
    def __init__(self, mode="exclusive", stopped=None):
        super().__init__(mode, stopped)
        self.desktop = Desktop(self.registry, self.desktop.items, mode)


def main():
    from computer_use_linux.windowing import native_directory

    native_directory.process_start = lambda pid: "generation"
    server = Server(Backend)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: server.stopped.set())
    return server.run()


if __name__ == "__main__":
    raise SystemExit(main())
