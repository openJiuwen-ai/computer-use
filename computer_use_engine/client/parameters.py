"""Validate public intent before preparing input or acquiring native resources."""

from computer_use.diagnostics import local_failure
from computer_use.protocol.primitives import finite
from computer_use.types import ClickOption


def reference(value, label="reference"):
    # Exact types preserve scalar validation and identity contracts.
    # pylint: disable-next=unidiomatic-typecheck
    if type(value) is not int or value < 0:
        raise local_failure(f"{label} must be a nonnegative integer", "use an integer published by this session")
    return str(value)


def point(value):
    if not isinstance(value, tuple) or len(value) != 2 or any(not finite(v) or v < 0 for v in value):
        raise local_failure(
            "a point must be a tuple of two finite nonnegative numbers", "use (x, y) from this object's screenshot"
        )
    return value


def click(options):
    if options is None:
        options = ClickOption()
    invalid_options = (
        True
        if (
            not isinstance(options, ClickOption)
            or options.button not in ("left", "right", "middle")
            # pylint: disable-next=unidiomatic-typecheck
            or type(options.count) is not int
            or options.count not in (1, 2)
        )
        else False
    )
    if invalid_options:
        raise local_failure(
            "click expects ClickOption(button=left/right/middle, count=1/2) or None",
            "omit options for a single left click; do not use an options dictionary",
        )
    return dict(button=options.button, count=options.count)


def scroll(direction, pages):
    if direction not in ("up", "down", "left", "right") or not finite(pages) or pages <= 0:
        raise local_failure(
            "scroll expects a direction and a finite positive page amount", "use up/down/left/right and pages=1 or 0.5"
        )


def view(value, full, menu_path=None):
    # pylint: disable-next=unidiomatic-typecheck
    if value not in ("tree", "image", "both") or type(full) is not bool:
        raise local_failure(
            "read expects view='tree'/'image'/'both' and a bool full",
            "use read(view='both') to request an image and tree",
        )
    if menu_path is not None:
        from computer_use.protocol.primitives import trim, utf16_length

        invalid_value_condition = (
            True
            if (
                value != "tree"
                or not isinstance(menu_path, list)
                or not 1 <= len(menu_path) <= 8
                or any(not isinstance(name, str) or not trim(name) or utf16_length(name) > 256 for name in menu_path)
            )
            else False
        )
        if invalid_value_condition:
            raise local_failure(
                "menu_path requires 1-8 nonempty titles and view='tree'", "copy the observed menu titles"
            )


def cursor(value):
    invalid_value = (
        True if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 256) else False
    )
    if invalid_value:
        raise local_failure(
            "cursor must be None or a returned opaque string", "copy the continuation from the corresponding read"
        )
