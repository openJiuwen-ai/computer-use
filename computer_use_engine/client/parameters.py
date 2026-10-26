"""Validate public intent before preparing input or acquiring native resources."""

from computer_use.diagnostics import localFailure
from computer_use.protocol.primitives import finite
from computer_use.types import ClickOption


def reference(value, label="reference"):
    if type(value) is not int or value < 0:
        raise localFailure(f"{label} must be a nonnegative integer", "use an integer published by this session")
    return str(value)


def point(value):
    if not isinstance(value, tuple) or len(value) != 2 or any(not finite(v) or v < 0 for v in value):
        raise localFailure(
            "a point must be a tuple of two finite nonnegative numbers", "use (x, y) from this object's screenshot"
        )
    return value


def click(options):
    if options is None:
        options = ClickOption()
    if (
        not isinstance(options, ClickOption)
        or options.button not in ("left", "right", "middle")
        or type(options.count) is not int
        or options.count not in (1, 2)
    ):
        raise localFailure(
            "click expects ClickOption(button=left/right/middle, count=1/2) or None",
            "omit options for a single left click; do not use an options dictionary",
        )
    return dict(button=options.button, count=options.count)


def scroll(direction, pages):
    if direction not in ("up", "down", "left", "right") or not finite(pages) or pages <= 0:
        raise localFailure(
            "scroll expects a direction and a finite positive page amount", "use up/down/left/right and pages=1 or 0.5"
        )


def view(value, full, menu_path=None):
    if value not in ("tree", "image", "both") or type(full) is not bool:
        raise localFailure(
            "read expects view='tree'/'image'/'both' and a bool full",
            "use read(view='both') to request an image and tree",
        )
    if menu_path is not None:
        from computer_use.protocol.primitives import trim, utf16Length

        if (
            value != "tree"
            or not isinstance(menu_path, list)
            or not 1 <= len(menu_path) <= 8
            or any(not isinstance(name, str) or not trim(name) or utf16Length(name) > 256 for name in menu_path)
        ):
            raise localFailure("menuPath requires 1-8 nonempty titles and view='tree'", "copy the observed menu titles")


def cursor(value):
    if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 256):
        raise localFailure(
            "cursor must be None or a returned opaque string", "copy the continuation from the corresponding read"
        )
