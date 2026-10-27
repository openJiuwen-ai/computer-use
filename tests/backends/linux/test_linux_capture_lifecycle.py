"""Production capture leases with only Xlib requests/events and time substituted."""

import sys
import threading
import unittest
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

from PIL import Image

import backends.linux.test_linux_native as base  # noqa: F401 - Initialize the portable Linux package path.
from computer_use_linux.contracts.protocol import Failure, Request
from computer_use_linux.objects.registry import Window
from computer_use_linux.system.x11 import X11
from computer_use_linux.system.x11_capture import X11WindowCaptures


class XError(Exception):
    pass


class BadMatch(XError):
    pass


class BadWindow(XError):
    pass


class CatchError:
    def __init__(self):
        self.error = None

    def __call__(self, error, request=None):
        self.error = error
        return True

    def get_error(self):
        return self.error


class NativeCaptureSeams:
    """Emulate X requests, not the lease/wait/read/disposal algorithms."""

    X = NS(
        StructureNotifyMask=1,
        MapNotify=19,
        UnmapNotify=18,
        ConfigureNotify=22,
        ReparentNotify=21,
        DestroyNotify=17,
        MappingNotify=34,
    )

    def __init__(self, *, damage=True, compositor=False, paint_delay=0.2):
        self.now, self.next_id = 100.0, 1000
        self.calls, self.events, self.scheduled, self.resources = [], [], [], set()
        self.windows, self.native_windows, self.colors = {}, {}, {}
        self.has_damage, self.compositor, self.paint_delay = damage, compositor, paint_delay
        self.failures, self.after_pixels, self.last_image = {}, None, None
        self.grabbed, self.manager, self.watched = False, None, set()
        self.root = NS(id=0)
        self.d = NS(
            has_extension=lambda name: name != "DAMAGE" or damage,
            composite_query_version=lambda: NS(major_version=0, minor_version=4),
            damage_query_version=lambda: NS(),
            sync=lambda: self.request("sync"),
            flush=lambda: self.request("flush"),
            pending_events=lambda: len(self.events),
            grab_server=self.grab,
            ungrab_server=self.ungrab,
        )
        self.d.display = NS(
            allocate_resource_id=self.allocate, free_resource_id=self.release, get_extension_major=lambda _: 143
        )
        self.modules = {
            "Xlib": NS(error=NS(CatchError=CatchError, BadMatch=BadMatch, BadWindow=BadWindow, XError=XError)),
            "Xlib.ext": NS(
                composite=NS(RedirectAutomatic=0),
                damage=NS(
                    DamageReportNonEmpty=3,
                    DamageCreate=self.damage_create,
                    DamageSubtract=self.damage_subtract,
                    DamageDestroy=self.damage_destroy,
                ),
            ),
        }
        self.add(1)

    def allocate(self):
        self.next_id += 1
        self.resources.add(self.next_id)
        return self.next_id

    def release(self, value):
        self.calls.append(("release-id", value))
        self.resources.discard(value)

    def request(self, name, onerror=None, **details):
        self.calls.append((name, details))
        error = self.failures.get(name)
        if error:
            if callable(error):
                error = error()
            if error is not None:
                if onerror is not None:
                    onerror(error, None)
                else:
                    raise error
                return False
        return True

    def add(self, key, *, size=(40, 30), parent=None):
        self.windows[key] = Window(key, "Fixture", "fixture", 1, (20, 30, *size))
        self.colors[key] = "#345678" if self.compositor else "#eeeeee"
        resource = NS(id=key, redirected=self.compositor, parent=parent or self.root, mask=8)
        resource.query_tree = lambda: NS(parent=resource.parent)
        resource.get_attributes = lambda: NS(your_event_mask=resource.mask)

        def attributes(event_mask, onerror=None):
            if self.request("attributes", onerror, xid=key, mask=event_mask):
                resource.mask = event_mask

        resource.change_attributes = attributes

        def redirect(mode, onerror=None):
            if self.request("redirect", onerror, xid=key):
                resource.redirected = True

        def unredirect(mode, onerror=None):
            if self.request("unredirect", onerror, xid=key):
                resource.redirected = self.compositor

        def name(onerror=None):
            identifier = self.allocate()
            pixmap = NS(id=identifier)
            if not resource.redirected:
                onerror(BadMatch("not redirected"), None)
            else:
                self.request("name", onerror, xid=key)
            pixmap.get_geometry = lambda: NS(width=self.windows[key].rect[2], height=self.windows[key].rect[3])

            def free(onerror=None):
                if self.request("free", onerror, xid=identifier):
                    self.release(identifier)

            pixmap.free = free
            pixmap.window = key
            return pixmap

        resource.composite_redirect_window, resource.composite_unredirect_window = redirect, unredirect
        resource.composite_name_window_pixmap = name
        self.native_windows[key] = resource
        return NS(scope="scope-" + str(key), window=key)

    def native(self, key):
        return self.native_windows[key]

    def validate(self, instance):
        self.pump()
        if instance.window not in self.windows:
            raise Failure("window destroyed", "stale_target")
        return replace(self.windows[instance.window])

    def grab(self):
        self.request("grab")
        self.grabbed = True

    def ungrab(self):
        self.request("ungrab")
        self.grabbed = False
        if self.paint_delay is not None and self.has_damage:
            for lease in self.manager.entries.values():
                if not lease.sampled and lease.damage is not None:
                    self.scheduled.append((self.now + self.paint_delay, lease.window.id, lease.damage))

    def damage_create(self, **kw):
        if self.request("damage-create", kw["onerror"], damage=kw["damage"]):
            self.events.append(NS(type=143, damage=kw["damage"]))  # Initial region is not client repaint.

    def damage_subtract(self, **kw):
        self.request("damage-subtract", kw["onerror"], damage=kw["damage"])

    def damage_destroy(self, **kw):
        self.request("damage-destroy", kw["onerror"], damage=kw["damage"])

    def pump(self):
        for item in list(self.scheduled):
            when, window, damage = item
            if not self.grabbed and self.now >= when:
                self.scheduled.remove(item)
                self.colors[window] = "#345678"
                self.events.append(NS(type=143, damage=damage))
        events, self.events = self.events, []
        for event in events:
            if self.manager is not None:
                self.manager.on_event(event)

    def advance(self, seconds):
        assert not self.grabbed, "A client update wait occurred under GrabServer"
        self.now += seconds
        self.pump()

    def context(self, seconds=10):
        stop = NS(is_set=lambda: False, wait=self.advance)
        return Request(self.now + seconds, stop, threading.Event(), pump=self.pump)

    def read_pixels(self, drawable, width, height):
        self.request("pixels")
        self.last_image = Image.new("RGB", (width, height), self.colors[drawable.window])
        if self.after_pixels:
            self.after_pixels()
        return self.last_image

    def event(self, key, type_, **fields):
        self.manager.on_event(NS(window=self.native_windows[key], type=type_, **fields))


class LinuxCaptureLifecycleTests(unittest.TestCase):
    def create(self, **kwargs):
        native = NativeCaptureSeams(**kwargs)
        modules = patch.dict(sys.modules, native.modules)
        modules.start()
        self.addCleanup(modules.stop)
        clock = patch("time.monotonic", side_effect=lambda: native.now)
        clock.start()
        self.addCleanup(clock.stop)
        native.manager = X11WindowCaptures(native)

        def close():
            native.failures.clear()
            native.manager.close()

        self.addCleanup(close)
        return native, native.manager, NS(scope="scope-1", window=1)

    def test_cold_wait_ignores_initial_damage_then_samples_real_delayed_update(self):
        n, manager, instance = self.create()
        image, _, notes = manager.read(instance, n.context())
        self.assertEqual(notes, [])
        self.assertEqual(image.getpixel((0, 0)), (52, 86, 120))
        self.assertGreaterEqual(n.now, 100.2)
        self.assertLess(n.now, 100.25)
        self.assertFalse(n.grabbed)
        self.assertEqual(sum(name == "redirect" for name, _ in n.calls), 1)

    def test_warm_read_reuses_redirect_but_names_and_frees_current_pixmap(self):
        n, manager, instance = self.create()
        manager.read(instance, n.context())
        resource_ids, before = set(n.resources), n.now
        for _ in range(10):
            image, _, notes = manager.read(instance, n.context())
            self.assertEqual(notes, [])
            image.close()
        self.assertEqual(n.now, before)
        self.assertEqual(n.resources, resource_ids)
        self.assertEqual(sum(name == "redirect" for name, _ in n.calls), 1)
        self.assertEqual(sum(name == "unredirect" for name, _ in n.calls), 0)

    def test_existing_compositor_storage_does_not_wait_for_new_client_paint(self):
        n, manager, instance = self.create(compositor=True, paint_delay=None)
        _, _, notes = manager.read(instance, n.context())
        self.assertEqual(notes, [])
        self.assertEqual(n.now, 100.0)
        manager.close()
        self.assertTrue(n.native(1).redirected, "Another compositor still owns the storage")
        self.assertFalse(n.resources)

    def test_no_update_returns_bounded_pixels_with_explicit_gap(self):
        n, manager, instance = self.create(paint_delay=None)
        image, _, notes = manager.read(instance, n.context())
        self.assertEqual(notes, [manager.WAIT_NOTE])
        self.assertEqual(image.size, (40, 30))
        self.assertAlmostEqual(n.now, 100.7)
        self.assertFalse(manager.entries[1].sampled, "Initial DamageCreate region is not repaint evidence")

    def test_missing_damage_extension_keeps_pixels_and_reports_uncertainty(self):
        n, manager, instance = self.create(damage=False)
        image, _, notes = manager.read(instance, n.context())
        self.assertEqual(notes, [manager.WAIT_NOTE])
        self.assertEqual(n.now, 100.0)
        self.assertEqual(image.size, (40, 30))
        self.assertNotIn("damage-create", [name for name, _ in n.calls])

    def test_short_caller_budget_reserves_time_for_pixel_acquisition(self):
        n, manager, instance = self.create(paint_delay=None)
        _, _, notes = manager.read(instance, n.context(0.2))
        self.assertEqual(notes, [manager.WAIT_NOTE])
        self.assertAlmostEqual(n.now, 100.15)

    def test_cancelled_cold_request_never_grabs_or_creates_storage(self):
        n, manager, instance = self.create()
        ctx = n.context()
        ctx.cancelled.set()
        with self.assertRaises(Failure) as error:
            manager.read(instance, ctx)
        self.assertEqual(error.exception.code, "cancelled")
        self.assertNotIn("grab", [name for name, _ in n.calls])
        self.assertFalse(n.resources)
        self.assertFalse(manager.entries)

    def test_cancel_during_wait_keeps_lease_without_leaking_grab_or_pixmap(self):
        n, manager, instance = self.create(paint_delay=None)
        ctx = n.context()
        advance = n.advance

        def stop(seconds):
            advance(seconds)
            ctx.cancelled.set()

        ctx.stopped.wait = stop
        with self.assertRaises(Failure) as error:
            manager.read(instance, ctx)
        self.assertEqual(error.exception.code, "cancelled")
        self.assertFalse(n.grabbed)
        self.assertEqual(n.resources, {manager.entries[1].damage})
        manager.close()
        self.assertFalse(n.resources)

    def test_damage_creation_failure_ungrabs_and_unredirects(self):
        n, manager, instance = self.create()
        n.failures["damage-create"] = RuntimeError("cannot create damage")
        with self.assertRaisesRegex(RuntimeError, "cannot create damage"):
            manager.read(instance, n.context())
        self.assertFalse(n.grabbed)
        self.assertFalse(n.resources)
        self.assertFalse(manager.entries)
        self.assertFalse(n.native(1).redirected)
        self.assertIn("ungrab", [name for name, _ in n.calls])

    def test_subtract_failure_still_ungrabs_and_disposes_created_damage(self):
        n, manager, instance = self.create()
        n.failures["damage-subtract"] = RuntimeError("cannot subtract")
        with self.assertRaisesRegex(RuntimeError, "cannot subtract"):
            manager.read(instance, n.context())
        self.assertFalse(n.grabbed)
        self.assertFalse(n.resources)
        self.assertFalse(manager.entries)
        self.assertIn("damage-destroy", [name for name, _ in n.calls])

    def test_pixel_failure_always_frees_named_pixmap_and_keeps_bounded_lease(self):
        n, manager, instance = self.create()
        n.failures["pixels"] = RuntimeError("pixels failed")
        with self.assertRaisesRegex(RuntimeError, "pixels failed"):
            manager.read(instance, n.context())
        self.assertEqual(n.resources, {manager.entries[1].damage})
        self.assertFalse(n.grabbed)

    def test_disposal_attempts_all_resources_and_reports_native_cleanup_failure(self):
        n, manager, instance = self.create()
        manager.read(instance, n.context())
        n.failures["damage-destroy"] = RuntimeError("damage destroy failed")
        with self.assertRaisesRegex(RuntimeError, "damage destroy failed"):
            manager.close()
        self.assertIn("unredirect", [name for name, _ in n.calls])
        self.assertFalse(n.native(1).redirected)
        self.assertFalse(manager.entries)

    def test_failed_pixmap_release_stops_further_capture_allocations(self):
        n, manager, instance = self.create()
        n.failures["free"] = XError("pixmap release uncertain")
        with self.assertRaisesRegex(XError, "pixmap release uncertain"):
            manager.read(instance, n.context())
        before = sum(name == "name" for name, _ in n.calls)
        n.failures.clear()
        with self.assertRaises(Failure) as error:
            manager.read(instance, n.context())
        self.assertEqual(error.exception.code, "observation_unavailable")
        self.assertEqual(sum(name == "name" for name, _ in n.calls), before)

    def test_close_processes_other_leases_after_one_native_disposal_failure(self):
        n, manager, instance = self.create()
        manager.read(instance, n.context())
        other = n.add(2)
        manager.read(other, n.context())
        n.failures["unredirect"] = RuntimeError("unredirect failed")
        with self.assertRaisesRegex(RuntimeError, "unredirect failed"):
            manager.close()
        self.assertEqual(sum(name == "unredirect" for name, _ in n.calls), 2)
        self.assertFalse(manager.entries)

    def test_resize_invalidates_sampling_and_acquires_current_pixmap_geometry(self):
        n, manager, instance = self.create()
        manager.read(instance, n.context())
        lease = manager.entries[1]
        n.windows[1] = replace(n.windows[1], rect=(20, 30, 60, 45))
        n.event(1, n.X.ConfigureNotify, width=60, height=45)
        self.assertFalse(lease.sampled)
        self.assertEqual(lease.epoch, 1)
        n.scheduled.append((n.now + 0.2, 1, lease.damage))
        image, window, notes = manager.read(instance, n.context())
        self.assertEqual(image.size, (60, 45))
        self.assertEqual(window.rect[2:], (60, 45))
        self.assertEqual(notes, [])
        self.assertEqual(sum(name == "redirect" for name, _ in n.calls), 1)

    def test_remap_epoch_during_pixel_read_rejects_same_geometry_image(self):
        n, manager, instance = self.create()
        n.after_pixels = lambda: n.event(1, n.X.UnmapNotify)
        with self.assertRaises(Failure) as error:
            manager.read(instance, n.context())
        self.assertEqual(error.exception.code, "stale_target")
        with self.assertRaises(ValueError):
            n.last_image.getpixel((0, 0))
        self.assertEqual(n.resources, {manager.entries[1].damage})

    def test_movement_during_pixels_is_not_published(self):
        n, manager, instance = self.create()

        def move():
            n.windows[1] = replace(n.windows[1], rect=(21, 30, 40, 30))

        n.after_pixels = move
        with self.assertRaises(Failure) as error:
            manager.read(instance, n.context())
        self.assertEqual(error.exception.code, "stale_target")

    def test_unmapped_window_does_not_acquire_a_lease(self):
        n, manager, instance = self.create()
        n.windows[1] = replace(n.windows[1], mapped=False)
        with self.assertRaises(Failure) as error:
            manager.read(instance, n.context())
        self.assertEqual(error.exception.code, "observation_unavailable")
        self.assertFalse(manager.entries)

    def test_new_instance_on_reused_xid_disposes_old_lease(self):
        n, manager, instance = self.create()
        manager.read(instance, n.context())
        old = manager.entries[1]
        instance.scope = "replacement"
        manager.read(instance, n.context())
        self.assertIsNot(manager.entries[1], old)
        self.assertEqual(sum(name == "unredirect" for name, _ in n.calls), 1)
        self.assertEqual(manager.entries[1].instance, "replacement")

    def test_lru_eviction_removes_oldest_lease_and_native_storage(self):
        n, manager, instance = self.create()
        manager.MAX_WINDOWS = 2
        manager.read(instance, n.context())
        second = n.add(2)
        manager.read(second, n.context())
        manager.read(instance, n.context())
        third = n.add(3)
        manager.read(third, n.context())
        self.assertEqual(list(manager.entries), [1, 3])
        self.assertFalse(n.native(2).redirected)
        self.assertEqual(len(n.resources), 2)

    def test_pixel_budget_evicts_old_storage_but_rejects_single_oversize(self):
        n, manager, instance = self.create()
        manager.MAX_PIXELS = 2000
        manager.read(instance, n.context())
        other = n.add(2)
        manager.read(other, n.context())
        self.assertEqual(list(manager.entries), [2])
        huge = n.add(3, size=(50, 50))
        with self.assertRaises(Failure) as error:
            manager.read(huge, n.context())
        self.assertEqual(error.exception.code, "observation_unavailable")
        self.assertEqual(list(manager.entries), [2])

    def test_shared_ancestor_watch_restores_original_mask_after_last_release(self):
        n, manager, instance = self.create()
        n.add(10)
        parent = n.native(10)
        n.native(1).parent = parent
        second = n.add(2, parent=parent)
        manager.read(instance, n.context())
        manager.read(second, n.context())
        self.assertEqual(manager.watches[10][2], 2)
        self.assertEqual(parent.mask, 9)
        n.event(1, n.X.DestroyNotify)
        self.assertEqual(manager.watches[10][2], 1)
        self.assertEqual(parent.mask, 9)
        manager.close()
        self.assertFalse(manager.watches)
        self.assertEqual(parent.mask, 8)

    def test_destroyed_window_releases_lease_without_retargeting(self):
        n, manager, instance = self.create()
        manager.read(instance, n.context())
        n.event(1, n.X.DestroyNotify)
        n.windows.pop(1)
        self.assertFalse(manager.entries)
        self.assertFalse(n.resources)
        with self.assertRaises(Failure) as error:
            manager.read(instance, n.context())
        self.assertEqual(error.exception.code, "stale_target")

    def test_legitimate_uniform_pixels_are_not_rejected(self):
        n, manager, instance = self.create()
        image, _, notes = manager.read(instance, n.context())
        self.assertEqual(image.getextrema(), ((52, 52), (86, 86), (120, 120)))
        self.assertEqual(notes, [])

    def test_gap_is_cleared_when_later_client_damage_arrives_on_same_lease(self):
        n, manager, instance = self.create(paint_delay=1.2)
        _, _, first = manager.read(instance, n.context())
        n.advance(0.6)
        image, _, second = manager.read(instance, n.context())
        self.assertEqual(first, [manager.WAIT_NOTE])
        self.assertEqual(second, [])
        self.assertEqual(image.getpixel((0, 0)), (52, 86, 120))
        self.assertEqual(sum(name == "redirect" for name, _ in n.calls), 1)

    def test_event_drain_budget_failure_releases_server_grab_and_owned_resources(self):
        n, manager, instance = self.create()
        n.d.pending_events = lambda: 1
        with self.assertRaises(Failure) as error:
            manager.read(instance, n.context())
        self.assertEqual(error.exception.code, "observation_unavailable")
        self.assertFalse(n.grabbed)
        self.assertFalse(manager.entries)
        self.assertFalse(n.resources)
        self.assertEqual(n.now, 100.0, "Initialization must not wait for another client while grabbed")

    def test_native_name_failure_frees_its_uncreated_xid_and_releases_grab(self):
        n, manager, instance = self.create(compositor=True)
        n.failures["name"] = BadWindow("gone")
        with self.assertRaises(BadWindow):
            manager.read(instance, n.context())
        self.assertFalse(n.grabbed)
        self.assertFalse(n.resources)
        self.assertFalse(manager.entries)

    def test_uncertain_lease_cleanup_poison_blocks_new_storage(self):
        n, manager, instance = self.create()
        manager.read(instance, n.context())
        n.failures["unredirect"] = XError("release unconfirmed")
        with self.assertRaises(XError):
            manager.close()
        n.failures.clear()
        before = len(n.calls)
        with self.assertRaises(Failure) as error:
            manager.read(instance, n.context())
        self.assertEqual(error.exception.code, "observation_unavailable")
        self.assertEqual(len(n.calls), before)

    def test_damage_observation_fault_is_exposed_instead_of_silent_ready_image(self):
        n, manager, instance = self.create()
        manager.read(instance, n.context())
        n.failures["damage-subtract"] = XError("damage object disappeared")
        manager.on_event(NS(type=143, damage=manager.entries[1].damage))
        n.failures.clear()
        self.assertFalse(manager.entries[1].sampled)
        with self.assertRaises(Failure) as error:
            manager.read(instance, n.context())
        self.assertEqual(error.exception.code, "observation_unavailable")

    def test_reparent_rebinds_watches_and_preserves_independent_native_subscription(self):
        n, manager, instance = self.create()
        n.add(10)
        n.add(11)
        first, second = n.native(10), n.native(11)
        n.native(1).parent = first
        manager.read(instance, n.context())
        n.watched.add(10)
        n.native(1).parent = second
        n.event(1, n.X.ReparentNotify)
        manager.read(instance, n.context())
        self.assertNotIn(10, manager.watches)
        self.assertIn(11, manager.watches)
        self.assertEqual(first.mask, 9, "Capture must not unsubscribe independent window discovery")
        manager.close()
        self.assertEqual(second.mask, 8)

    def test_ancestor_unmap_during_pixels_invalidates_even_unchanged_client_geometry(self):
        n, manager, instance = self.create()
        n.add(10)
        n.native(1).parent = n.native(10)
        n.after_pixels = lambda: n.event(10, n.X.UnmapNotify)
        with self.assertRaises(Failure) as error:
            manager.read(instance, n.context())
        self.assertEqual(error.exception.code, "stale_target")

    def test_window_destroyed_during_wait_retires_lease_before_pixels(self):
        n, manager, instance = self.create(paint_delay=None)
        ctx = n.context()

        def destroy(seconds):
            n.advance(seconds)
            n.event(1, n.X.DestroyNotify)
            n.windows.pop(1)

        ctx.stopped.wait = destroy
        with self.assertRaises(Failure) as error:
            manager.read(instance, ctx)
        self.assertEqual(error.exception.code, "stale_target")
        self.assertFalse(manager.entries)
        self.assertFalse(n.resources)
        self.assertNotIn("pixels", [name for name, _ in n.calls])

    def test_damage_arriving_after_pixels_cannot_remove_that_images_gap(self):
        n, manager, instance = self.create(paint_delay=None)

        def later_update():
            n.colors[1] = "#345678"
            manager.on_event(NS(type=143, damage=manager.entries[1].damage))

        n.after_pixels = later_update
        image, _, notes = manager.read(instance, n.context())
        self.assertTrue(manager.entries[1].sampled, "The later update did reach the manager")
        self.assertEqual(image.getpixel((0, 0)), (238, 238, 238), "Pixels were sampled before that update")
        self.assertEqual(notes, [manager.WAIT_NOTE], "A later update cannot certify the old image")
        n.after_pixels = None
        next_image, _, next_notes = manager.read(instance, n.context())
        self.assertEqual(next_image.getpixel((0, 0)), (52, 86, 120))
        self.assertEqual(next_notes, [])

    def test_queued_damage_for_destroyed_drawable_does_not_block_native_registry_retirement(self):
        n, manager, instance = self.create()
        manager.read(instance, n.context())
        lease = manager.entries[1]
        n.failures.update(
            {
                "damage-subtract": BadWindow("already destroyed"),
                "damage-destroy": BadWindow("already destroyed"),
                "unredirect": BadWindow("already destroyed"),
            }
        )
        events = [NS(type=143, damage=lease.damage), NS(type=n.X.DestroyNotify, window=n.native(1))]
        retired = []
        x = X11.__new__(X11)
        x.d = NS(pending_events=lambda: len(events), next_event=lambda: events.pop(0))
        x.X, x.window_captures, x.retire, x.watched = n.X, manager, retired.append, {1}
        x.pump()  # Run the production pump, including the native registry retire hook.
        self.assertEqual(retired, [1])
        self.assertFalse(x.watched)
        self.assertFalse(events)
        self.assertFalse(manager.entries)
        self.assertFalse(n.resources)

    def test_resize_initial_background_damage_is_excluded_by_new_storage_baseline(self):
        n, manager, instance = self.create()
        manager.read(instance, n.context())
        lease = manager.entries[1]
        n.paint_delay = None
        n.windows[1] = replace(n.windows[1], rect=(20, 30, 60, 45))
        n.colors[1] = "#eeeeee"
        n.event(1, n.X.ConfigureNotify, width=60, height=45)
        self.assertTrue(lease.storage_dirty)
        # One notification is already queued; another arrives as baseline
        # requests are drained under the server grab. Neither is client paint.
        n.events.append(NS(type=143, damage=lease.damage))

        def grab_with_background_damage():
            n.grab()
            n.events.append(NS(type=143, damage=lease.damage))

        n.d.grab_server = grab_with_background_damage
        before = n.now
        image, _, notes = manager.read(instance, n.context())
        self.assertAlmostEqual(n.now - before, manager.FIRST_UPDATE_WAIT)
        self.assertEqual(image.size, (60, 45))
        self.assertEqual(notes, [manager.WAIT_NOTE])
        self.assertFalse(lease.storage_dirty)
        self.assertFalse(lease.sampled)
        self.assertEqual(lease.last_update, 0)
        self.assertFalse(n.grabbed)
        self.assertEqual(
            sum(name == "redirect" for name, _ in n.calls), 1, "Resize retains the existing redirect lease"
        )

    def test_unrelated_damage_does_not_mark_a_lease_as_sampled(self):
        n, manager, instance = self.create(paint_delay=None)
        _, _, notes = manager.read(instance, n.context())
        manager.on_event(NS(type=143, damage=999999))
        self.assertFalse(manager.entries[1].sampled)
        self.assertEqual(notes, [manager.WAIT_NOTE])


if __name__ == "__main__":
    unittest.main()
