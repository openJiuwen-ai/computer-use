"""Session-owned bindings. Display numbers never route input by themselves."""

from __future__ import annotations

from dataclasses import dataclass, field

from computer_use_linux.contracts.protocol import Failure, now, token


@dataclass
class Window:
    key: int | str
    title: str
    app: str
    pid: int
    rect: tuple | None
    transient: int | str | None = None
    modal: bool = False
    mapped: bool = True
    process_start: str = ""
    frame_rect: tuple | None = None
    override_redirect: bool = False
    accessible: object | None = None
    parent_state: str | None = None
    wm_class: tuple = ()


@dataclass
class Instance:
    window: Window
    selector: str = field(default_factory=lambda: token("instance"))
    scope: str = field(default_factory=lambda: token("window"))
    ref: str = ""
    alive: bool = True


@dataclass
class Capture:
    instance: Instance | None
    window: Window | None
    scope: str
    image: dict | None
    transform: tuple
    owner: Instance | None = None
    created: float = field(default_factory=now)


class Registry:
    def __init__(self):
        self.instances = {}
        self.by_key = {}
        self.next_number = 0
        self.desktop_scope = token("desktop")

    def window(self, native):
        previous = self.by_key.get(native.key)
        if previous and (
            not previous.alive
            or previous.window.pid != native.pid
            or previous.window.process_start != native.process_start
        ):
            previous.alive = False
            previous = None
        if previous is None:
            previous = Instance(native)
            previous.ref = str(self.next_number)
            self.next_number += 1
            self.instances[previous.selector] = previous
            self.by_key[native.key] = previous
        previous.window = native
        return previous

    def retire(self, key):
        if key in self.by_key:
            self.by_key[key].alive = False

    def instance(self, selector):
        item = self.instances.get(selector)
        if not item or not item.alive:
            raise Failure("Working window closed or instance selector expired.", "stale_target", selector)
        return item
