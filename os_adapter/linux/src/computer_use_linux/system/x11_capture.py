"""Persistent XComposite window storage; native pixels, not UI readiness.

One event consumer (X11.pump) owns structure and DAMAGE notifications. Each
read names the current pixmap, so no old pixmap survives resize/remapping.
A bounded first-update wait addresses cold redirection without raising windows
or synthesizing Expose. DAMAGE never proves that a complete UI frame is ready.
"""

import time
from collections import OrderedDict
from dataclasses import dataclass, field

from computer_use_linux.contracts.protocol import Failure, require


@dataclass
class Lease:
    window: object
    instance: str
    size: tuple
    ancestors: set = field(default_factory=set)
    damage: int | None = None
    redirected: bool = False
    initializing: bool = True
    sampled: bool = False
    epoch: int = 0
    last_update: float = 0
    parents_dirty: bool = False
    fault: str | None = None
    storage_dirty: bool = False


class X11WindowCaptures:
    MAX_WINDOWS = 32
    MAX_PIXELS = 64 * 1024 * 1024
    FIRST_UPDATE_WAIT = 0.7
    UPDATE_COALESCE = 0.02
    WAIT_NOTE = (
        "The window image was sampled before an update after capture storage initialization "
        "was confirmed. It may omit content; the bounded wait ended. Read again "
        "if the required content is missing."
    )

    def __init__(self, owner):
        from Xlib import error
        from Xlib.ext import composite, damage

        self.x, self.d = owner, owner.d
        self.error, self.composite, self.damage = error, composite, damage
        self.entries, self.watches = OrderedDict(), {}
        self.failed = None
        require(
            self.d.has_extension("Composite"),
            "Composite unavailable; use the desktop screenshot.",
            "observation_unavailable",
        )
        version = self.d.composite_query_version()
        require(
            (version.major_version, version.minor_version) >= (0, 2),
            "Composite does not support window pixmaps.",
            "observation_unavailable",
        )
        self.has_damage = self.d.has_extension("DAMAGE")
        if self.has_damage:
            self.d.damage_query_version()

    def _checked(self, send):
        caught = self.error.CatchError()
        result = send(caught)
        self.d.sync()
        if caught.get_error() is not None:
            raise caught.get_error()
        return result

    def _name(self, window, *, probe=False):
        caught = self.error.CatchError()
        pixmap = window.composite_name_window_pixmap(onerror=caught)
        self.d.sync()
        error = caught.get_error()
        if error is not None:
            # The server never created this pixmap; only its local XID is owned.
            self.d.display.free_resource_id(pixmap.id)
            if probe and isinstance(error, self.error.BadMatch):
                return None
            raise error
        return pixmap

    def _free_pixmap(self, pixmap):
        try:
            self._checked(lambda caught: pixmap.free(onerror=caught))
        except Exception as error:
            self.failed = str(error)
            raise

    def _damage_request(self, kind, **fields):
        return self._checked(
            lambda caught: kind(
                display=self.d.display, opcode=self.d.display.get_extension_major("DAMAGE"), onerror=caught, **fields
            )
        )

    def _create_damage(self, lease):
        if not self.has_damage:
            return
        identifier = self.d.display.allocate_resource_id()
        try:
            self._damage_request(
                self.damage.DamageCreate,
                damage=identifier,
                drawable=lease.window.id,
                level=self.damage.DamageReportNonEmpty,
            )
        except BaseException:
            self.d.display.free_resource_id(identifier)
            raise
        lease.damage = identifier

    def _clear_damage(self, lease):
        if lease.damage is not None:
            self._damage_request(self.damage.DamageSubtract, damage=lease.damage, repair=0, parts=0)

    def _ancestors(self, lease):
        parent = lease.window.query_tree().parent
        while parent.id != self.x.root.id and parent.id not in lease.ancestors:
            require(len(lease.ancestors) < 64, "Window parent chain exceeds capture budget.", "observation_unavailable")
            previous = self.watches.get(parent.id)
            if previous is None:
                mask = parent.get_attributes().your_event_mask
                self._checked(
                    lambda caught, *, mask=mask, parent=parent: parent.change_attributes(
                        event_mask=mask | self.x.X.StructureNotifyMask, onerror=caught
                    )
                )
                self.watches[parent.id] = [parent, mask, 1]
            else:
                previous[2] += 1
            lease.ancestors.add(parent.id)
            parent = parent.query_tree().parent

    def _dispose(self, lease, *, destroyed=False):
        self.entries.pop(lease.window.id, None)
        failures = []
        if lease.damage is not None:
            try:
                self._damage_request(self.damage.DamageDestroy, damage=lease.damage)
            except Exception as error:
                if not destroyed:
                    failures.append(error)
            finally:
                self.d.display.free_resource_id(lease.damage)
                lease.damage = None
        if lease.redirected:
            try:
                self._checked(
                    lambda caught: lease.window.composite_unredirect_window(
                        self.composite.RedirectAutomatic, onerror=caught
                    )
                )
            except self.error.BadWindow:
                pass
            except Exception as error:
                failures.append(error)
            lease.redirected = False
        failures.extend(self._unwatch(lease))
        if failures:
            # Keep further capture acquisitions bounded when resource release is
            # uncertain. Closing the X connection ultimately releases ownership.
            self.failed = str(failures[0])
            raise failures[0]

    def _unwatch(self, lease):
        failures = []
        ancestors, lease.ancestors = lease.ancestors, set()
        for xid in ancestors:
            watch = self.watches.get(xid)
            if watch is None:
                continue
            watch[2] -= 1
            if not watch[2]:
                del self.watches[xid]
                try:
                    # info() may have independently subscribed to this window
                    # after it was first watched as a capture ancestor.
                    mask = watch[1] | (self.x.X.StructureNotifyMask if xid in self.x.watched else 0)
                    self._checked(
                        lambda caught, *, mask=mask, watch=watch: watch[0].change_attributes(
                            event_mask=mask, onerror=caught
                        )
                    )
                except self.error.BadWindow:
                    pass
                except Exception as error:
                    failures.append(error)
        return failures

    def _budget(self, size, keep=None):
        require(0 < size[0] * size[1] <= self.MAX_PIXELS, "Capture exceeds pixel budget.", "observation_unavailable")
        while self.entries and (
            len(self.entries) + (keep is None) > self.MAX_WINDOWS
            or sum(e.size[0] * e.size[1] for e in self.entries.values() if e is not keep) + size[0] * size[1]
            > self.MAX_PIXELS
        ):
            victim = next((e for e in self.entries.values() if e is not keep), None)
            if victim is None:
                break
            self._dispose(victim)

    def _drain(self):
        # Called under a short server grab: no other client may draw here. This
        # excludes DamageCreate's initial region and redirect's background paint.
        self.d.sync()
        for _ in range(16):
            self.x.pump()
            if not self.d.pending_events():
                return
        raise Failure("X11 capture initialization event budget exceeded.", "observation_unavailable")

    def _acquire(self, instance, window, ctx):
        lease = Lease(self.x.native(window.key), instance.scope, window.rect[2:])
        self._budget(lease.size)
        self.entries[window.key] = lease
        try:
            self._ancestors(lease)
            ctx.check()
            try:
                self.d.grab_server()
                # Only native requests on this connection run inside the grab.
                # Never wait for client repaint while the server is grabbed.
                prior = self._name(lease.window, probe=True)
                if prior is not None:
                    self._free_pixmap(prior)
                self._checked(
                    lambda caught: lease.window.composite_redirect_window(
                        self.composite.RedirectAutomatic, onerror=caught
                    )
                )
                lease.redirected = True
                self._create_damage(lease)
                self._clear_damage(lease)
                self._drain()
                lease.sampled = prior is not None
                lease.storage_dirty = False
                lease.initializing = False
            finally:
                self.d.ungrab_server()
                self.d.flush()
        except BaseException:
            self._dispose(lease)
            raise
        return lease

    def on_event(self, event):
        xid = getattr(getattr(event, "window", None), "id", None)
        damage_id = getattr(event, "damage", None)
        for lease in list(self.entries.values()):
            if damage_id is not None and damage_id == lease.damage:
                if not lease.initializing and not lease.storage_dirty:
                    lease.sampled = True
                    lease.last_update = time.monotonic()
                try:
                    self._clear_damage(lease)
                except self.error.XError as error:
                    # Damage may precede DestroyNotify in our event queue even
                    # though the server has already destroyed the drawable. Do
                    # not prevent the remaining events from retiring its scope.
                    lease.fault = str(error)
                    lease.sampled = False
            if xid != lease.window.id and xid not in lease.ancestors:
                continue
            if event.type == self.x.X.DestroyNotify and xid == lease.window.id:
                try:
                    self._dispose(lease, destroyed=True)
                except self.error.XError:
                    pass  # self.failed records uncertain cleanup for next read.
                continue
            changed = event.type in (self.x.X.MapNotify, self.x.X.UnmapNotify, self.x.X.ReparentNotify)
            if event.type == self.x.X.ConfigureNotify and xid == lease.window.id:
                size = (event.width, event.height)
                changed = size != lease.size
                lease.size = size
            if event.type in (self.x.X.ReparentNotify, self.x.X.DestroyNotify):
                lease.parents_dirty = True
            if changed or event.type == self.x.X.DestroyNotify:
                lease.epoch += 1
                lease.sampled = False
                lease.storage_dirty = True

    def _baseline(self, lease):
        # Mapping/resize also allocates storage and paints a server background.
        # Their queued DAMAGE is not evidence of the client's new content.
        lease.initializing, lease.sampled = True, False
        lease.last_update = 0
        try:
            self.d.grab_server()
            self._clear_damage(lease)
            self._drain()
            lease.storage_dirty = False
        finally:
            lease.initializing = False
            self.d.ungrab_server()
            self.d.flush()

    def read(self, instance, ctx):
        require(
            self.failed is None,
            "Capture resource cleanup is uncertain; reconnect the driver.",
            "observation_unavailable",
        )
        window = self.x.validate(instance)
        require(window.mapped, "Unmapped window cannot be captured.", "observation_unavailable")
        lease = self.entries.get(window.key)
        if lease is not None and lease.instance != instance.scope:
            self._dispose(lease)
            lease = None
        if lease is None:
            lease = self._acquire(instance, window, ctx)
        require(self.entries.get(window.key) is lease, "Window disappeared during capture setup.", "stale_target")
        if lease.parents_dirty:
            errors = self._unwatch(lease)
            if errors:
                self.failed = str(errors[0])
                raise errors[0]
            self._ancestors(lease)
            lease.parents_dirty = False
        require(
            lease.fault is None,
            "Window update observation failed; read the desktop instead.",
            "observation_unavailable",
        )
        if lease.storage_dirty:
            self._baseline(lease)
        self.entries.move_to_end(window.key)
        self._budget(window.rect[2:], keep=lease)
        until = min(ctx.deadline - 0.05, time.monotonic() + self.FIRST_UPDATE_WAIT)
        while self.has_damage and time.monotonic() < until:
            ctx.check()
            current = self.x.validate(instance)
            require(
                current.mapped and current.rect == window.rect,
                "Window moved, resized or became hidden during capture.",
                "stale_target",
            )
            if lease.sampled and (
                not lease.last_update or time.monotonic() - lease.last_update >= self.UPDATE_COALESCE
            ):
                break
            ctx.pause(min(0.01, max(0, until - time.monotonic())))
        ctx.check()
        epoch, sampled = lease.epoch, lease.sampled
        pixmap = self._name(lease.window)
        try:
            geometry = pixmap.get_geometry()
            require(
                (geometry.width, geometry.height) == window.rect[2:],
                "Composite image does not match the current client bounds.",
                "stale_target",
            )
            image = self.x.read_pixels(pixmap, geometry.width, geometry.height)
        finally:
            self._free_pixmap(pixmap)
        current = self.x.validate(instance)
        if (
            current.rect != window.rect
            or not current.mapped
            or lease.epoch != epoch
            or self.entries.get(window.key) is not lease
        ):
            image.close()
            raise Failure("Window storage or geometry changed during capture.", "stale_target")
        return image, window, ([] if sampled else [self.WAIT_NOTE])

    def close(self):
        failures = []
        for lease in list(self.entries.values()):
            try:
                self._dispose(lease)
            except Exception as error:
                failures.append(error)
        if failures:
            raise failures[0]
