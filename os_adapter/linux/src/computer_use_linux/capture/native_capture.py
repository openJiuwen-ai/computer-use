"""Native pixels and immutable geometry, independent of an AT-SPI tree.

The SDK owns observation layout and image delivery. This module retains only
native identity/geometry for the image it actually captured, never a new target.
"""

import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace

from computer_use_linux.contracts.observation import stamp
from computer_use_linux.contracts.protocol import Failure, gap, now, require
from computer_use_linux.objects.registry import Capture


@dataclass(frozen=True)
class KeyboardDestination:
    # Snapshot identity must not follow Instance.window as the directory refreshes.
    identity: tuple
    instance: object = field(compare=False, repr=False)


@dataclass(frozen=True)
class ImageBinding:
    target: dict
    capture: Capture
    windows: tuple = ()
    keyboard: KeyboardDestination | None = None


class NativeCapture:
    def __init__(self, backend, directory):
        self.b, self.directory = backend, directory
        self.entries = OrderedDict()

    def retain(self, target, image, transform, instance=None, window=None, *, windows=(), keyboard=None):
        # Pixel bytes belong to the reply. Retain bounded metadata, not 64 PNGs.
        metadata = {key: value for key, value in image.items() if key != "png"}
        owner = self.b.registry.instance(target["instance"]) if instance else None
        self.entries[image["id"]] = ImageBinding(
            {**target, "capture": image["id"]},
            Capture(
                instance,
                replace(window) if window is not None else None,
                image["scope"],
                metadata,
                tuple(transform),
                owner=owner,
            ),
            tuple(windows),
            keyboard,
        )
        while len(self.entries) > 64:
            self.entries.popitem(last=False)

    def read(self, target, kind, max_dimension, ctx):
        require(
            getattr(self.b.desktop, "can_capture", False),
            "This desktop has no native pixel capture route.",
            "mode_unsupported",
        )
        ctx.check()
        self.b.ensure_unlocked()
        start = int(now())
        try:
            return (
                self.window(target, max_dimension, ctx, start)
                if kind == "window"
                else self.desktop(target, max_dimension, ctx, start)
            )
        except Failure:
            raise
        except Exception as error:
            raise Failure("Native screenshot unavailable: " + str(error), "observation_unavailable") from error

    def window(self, target, max_dimension, ctx, start):
        self.directory.validate(target, ctx)
        item = self.directory.surfaces[target["surface"]]
        image, transform = self.b.desktop.capture(item, target["surface"], ctx, max_dimension=max_dimension)
        # A provider or compositor can run while pixels are acquired. Validate
        # both the original work scope and this exact image's current geometry.
        self.directory.validate(target, ctx)
        current = self.b.desktop.validate(item)
        x, y, sx, sy = transform
        rect = (x, y, sx * image["width"], sy * image["height"])
        require(
            current.mapped
            and current.rect is not None
            and all(math.isclose(a, b, rel_tol=0, abs_tol=1e-6) for a, b in zip(current.rect, rect)),
            "Window geometry changed during capture.",
            "stale_target",
        )
        self.b.ensure_unlocked()
        ctx.check()
        result = dict(
            kind="capture",
            binding=dict(target),
            observed=stamp(start),
            image=image,
            coordinates=dict(
                space="window-pixels", origin="top-left", nativeSpace="screen-pixels", toNative=[sx, 0, 0, sy, x, y]
            ),
        )
        self.retain(target, image, transform, item, current)
        return result

    def desktop(self, target, max_dimension, ctx, start):
        scope = self.b.registry.desktop_scope
        before = self.keyboard_snapshot(ctx)
        image, transform = self.b.desktop.capture(None, scope, ctx, max_dimension=max_dimension)
        width, height = image["width"], image["height"]
        ox, oy, sx, sy = transform
        items, notes, frontmost, observed_windows = [], [], None, []
        try:
            windows = self.directory.windows(ctx, notes)
            active = self.b.desktop.active()
            for item in windows:
                ctx.check()
                w = item.window
                if not w.mapped or w.rect is None:
                    continue
                observed_windows.append(self.destination(item))
                selector = self.directory.window_application(item)
                app = self.directory.text(w.app, notes, "Window application name", placeholder="Unnamed application")
                if w.key == active:
                    frontmost = dict(name=app, selector=selector)
                x, y, w_width, w_height = w.rect
                left, top = max(0, (x - ox) / sx), max(0, (y - oy) / sy)
                right, bottom = min(width, (x + w_width - ox) / sx), min(height, (y + w_height - oy) / sy)
                if right > left and bottom > top:
                    items.append(
                        dict(
                            app=app,
                            title=self.directory.text(w.title, notes, "Window title"),
                            selector=selector,
                            rect=dict(x=left, y=top, width=right - left, height=bottom - top),
                        )
                    )
        except Exception as error:
            if isinstance(error, Failure) and error.code in ("cancelled", "deadline_exceeded"):
                raise
            ctx.check()  # Cancellation/deadline cannot become a catalogue gap.
            gap(notes, "Desktop window catalogue unavailable: " + str(error))
        self.b.ensure_unlocked()
        ctx.check()
        # One X screen is a unified canvas, not a claimed physical monitor.
        observation = dict(
            mode=self.b.mode,
            canvas=dict(width=width, height=height),
            frontmost=frontmost,
            displays=[dict(name="X11 screen", rect=dict(x=0, y=0, width=width, height=height), captured=True)],
            windows=items[:256],
            omittedWindows=max(0, len(items) - 256),
        )
        if notes:
            observation["catalogError"] = "\n".join(dict.fromkeys(notes))[:4096]
        result = dict(
            kind="desktopCapture",
            binding=dict(target),
            desktopRef=image["id"],
            scope=scope,
            observed=stamp(start),
            observation=observation,
            screenshot=image,
        )
        after = self.keyboard_snapshot(ctx)
        result["observed"] = stamp(start)
        keyboard = before if before is not None and before == after and before in observed_windows else None
        self.retain(target, image, transform, windows=observed_windows, keyboard=keyboard)
        return result

    @staticmethod
    def destination(item):
        w = item.window
        return KeyboardDestination((item.scope, w.key, w.pid, w.process_start), item)

    def keyboard_snapshot(self, ctx):
        """Optional native window evidence; no accessibility calls or focus changes."""
        try:
            ctx.check()
            desktop = self.b.desktop
            desktop.pump()
            active = desktop.active()
            if not active:
                return None
            native = desktop.info(active)
            if not native.mapped or not native.pid or not native.process_start:
                return None
            item = self.b.registry.window(native)
            expected = self.destination(item)
            current = desktop.validate(item)
            if (
                (current.key, current.pid, current.process_start) != expected.identity[1:]
                or desktop.active() != active
                or not desktop.focus_belongs(active)
            ):
                return None
            ctx.check()
            return expected
        except Exception:
            ctx.check()  # Cancellation cannot become a missing optional fact.
            return None

    def keyboard_destination(self, target, ctx):
        image = self.input(target)
        expected = image.keyboard
        require(
            expected is not None and expected.instance.alive,
            "The observed desktop keyboard window is unknown or expired; read the desktop before keys.",
            "stale_target",
        )
        current = self.keyboard_snapshot(ctx)
        require(
            current == expected, "The desktop keyboard window changed; read the desktop before keys.", "stale_target"
        )
        return expected

    def pointer_destination(self, target, point, ctx):
        """Identify the actual native hit among observed windows, without AX."""
        image = self.input(target)
        for item in image.windows:
            ctx.check()
            if not item.instance.alive:
                continue
            try:
                self.b.desktop.validate(item.instance)
                self.b.desktop.require_hit(item.instance, point)
                return item
            except Exception:
                ctx.check()
        return None

    def input_finished(self, target, ctx, *, expected=None, pointer=False):
        """Retain an observed effect, never assume a click already moved focus.

        A pointer receipt can confirm its actual hit window or leave keyboard
        input unavailable. Waiting samples the OS; it sends no preparation.
        """
        image = self.input(target)
        destination = None
        until = min(ctx.deadline - 0.02, time.monotonic() + 0.2)
        while True:
            current = self.keyboard_snapshot(ctx)
            matches_current = (
                True
                if current is not None and current in image.windows and (not pointer or current == expected)
                else False
            )
            if matches_current:
                destination = current
                break
            if not pointer or expected is None or time.monotonic() >= until:
                break
            ctx.pause(0.01)
        self.entries[target["capture"]] = replace(image, keyboard=destination)

    def input_failed(self):
        for key, image in list(self.entries.items()):
            if image.target["surface"] is None:
                self.entries[key] = replace(image, keyboard=None)

    def input(self, target):
        retained = self.entries.get(target["capture"])
        require(
            retained is not None and retained.target == target,
            "The original image binding expired or changed scope; read before using coordinates.",
            "stale_target",
        )
        return retained

    def clear(self):
        self.entries.clear()
