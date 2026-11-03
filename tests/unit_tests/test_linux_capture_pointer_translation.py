# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Real Linux pointer/click pipelines with substituted X11 server ports.

These are offline contract regressions, not Linux desktop acceptance tests.
"""

import importlib
import sys
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def native_modules():
    source = Path(__file__).resolve().parents[2] / "os_adapter/linux/src"
    with patch.object(sys, "path", [str(source), *sys.path]):
        return SimpleNamespace(
            pointer=importlib.import_module("computer_use_linux.control.native_pointer"),
            click=importlib.import_module("computer_use_linux.control.click.pipeline"),
            offers=importlib.import_module("computer_use_linux.control.click.passes.project_click_offers"),
            capture=importlib.import_module("computer_use_linux.capture.native_capture"),
            registry=importlib.import_module("computer_use_linux.objects.registry"),
            operation=importlib.import_module("computer_use_linux.runtime.native_operation"),
            protocol=importlib.import_module("computer_use_linux.contracts.protocol"),
            x11=importlib.import_module("computer_use_linux.system.x11"),
        )


@pytest.fixture
def scene(native_modules):
    m = native_modules
    original = m.registry.Window(42, "Document", "App", 123, (100, 80, 400, 300), process_start="birth")
    selected = m.registry.Instance(replace(original), selector="instance", scope="surface")
    s = SimpleNamespace(
        modules=m,
        original=original,
        selected=selected,
        current=replace(original),
        screen=(1600, 1000),
        covered=False,
        modal=False,
        scope_valid=True,
        events=[],
        hits=[],
        idle_hook=None,
        geometry_hook=None,
        event_hook=None,
        activation_hook=None,
        activations=[],
        failure=m.protocol.Failure,
    )
    ctx = m.protocol.Request(time.monotonic() + 10, threading.Event())
    # Timing itself is not under test; keep the real request's cancellation and
    # dispatch journal while substituting only the inter-event wait.
    ctx.pause = lambda _seconds=0.02: ctx.check()
    desktop = object.__new__(m.x11.X11)
    # This fixture verifies XTEST geometry and release for an authorized session.
    desktop.allow_global_input = True
    desktop.can_input, desktop.keys, desktop.buttons = True, set(), set()
    desktop.xlib = SimpleNamespace(MotionNotify=6, ButtonPress=4, ButtonRelease=5, KeyRelease=3)
    desktop.pump = lambda: None
    desktop.retire = lambda _key: setattr(selected, "alive", False)
    desktop.info = lambda _key: replace(s.current)

    def screen_geometry():
        if s.geometry_hook:
            hook, s.geometry_hook = s.geometry_hook, None
            hook()
        return SimpleNamespace(width=s.screen[0], height=s.screen[1])

    desktop.root = SimpleNamespace(get_geometry=screen_geometry)

    def idle():
        if s.idle_hook:
            hook, s.idle_hook = s.idle_hook, None
            hook()

    def hit(instance, point):
        s.hits.append(point)
        x, y, width, height = s.current.rect
        if s.covered or not (x <= point[0] < x + width and y <= point[1] < y + height):
            raise s.failure("The original window point is covered.", "stale_target")
        assert instance is selected

    def event(kind, detail=0, x=0, y=0):
        s.events.append((kind, detail, x, y))
        if s.event_hook:
            s.event_hook(kind, detail, x, y)

    def activate(_native, request):
        request.check()
        s.activations.append("original")
        s.covered = False
        if s.activation_hook:
            s.activation_hook()

    def expose(instance, point, request, *, is_reachable):
        assert instance is selected
        activate(s.current, request)
        assert is_reachable()

    desktop.require_idle_input, desktop.require_hit, desktop._event = idle, hit, event
    desktop.activate, desktop.expose = activate, expose

    def validate_scope(target, request):
        request.check()
        if not s.scope_valid:
            raise s.failure("The original window left its working instance.", "stale_target")
        assert target["instance"] == "instance" and target["rootWindow"] == "root"

    modal = m.registry.Window(99, "Modal", "App", 123, (200, 100, 100, 100), transient=42, modal=True)
    directory = SimpleNamespace(
        surfaces={"surface": selected},
        validate=validate_scope,
        windows=lambda *_: [SimpleNamespace(window=modal, ref="modal")] if s.modal else [],
    )
    backend = SimpleNamespace(ax=SimpleNamespace(), desktop=desktop, ensure_unlocked=lambda: None)
    store = SimpleNamespace(pins=set())
    captures = m.capture.NativeCapture(backend, directory)
    captures.entries = OrderedDict()
    target = {
        "session": "session",
        "instance": "instance",
        "rootWindow": "root",
        "surface": "surface",
        "node": None,
        "capture": "image",
    }
    capture = m.registry.Capture(selected, replace(original), "surface", {"width": 200, "height": 150}, (100, 80, 2, 2))
    captures.entries["image"] = m.capture.ImageBinding(dict(target), capture)
    unit = m.pointer.NativePointer(backend, store, directory, captures)

    def inspect(kind="click", *, desktop_target=False, parameters=None, legacy=False):
        if desktop_target:
            target.update(instance=None, rootWindow=None, surface=None)
            capture.instance, capture.window, capture.scope = None, None, "desktop"
            capture.image = {"width": 800, "height": 500}
            capture.transform = (0, 0, 2, 2)
            captures.entries["image"] = m.capture.ImageBinding(dict(target), capture)
        defaults = (
            {"fromX": 10, "fromY": 15, "toX": 80, "toY": 65}
            if kind == "drag"
            else dict(x=10, y=15, **({"direction": "down", "pages": 0.25} if kind == "scroll" else {}))
        )
        intent = m.pointer.pointer_intent({"kind": kind, "parameters": defaults if parameters is None else parameters})
        operation = m.operation.NativeOperation(dict(target), ctx.deadline, intent, writable=True)
        if kind == "click" and not legacy:
            operation.click_contract = "v1"
        facts = unit.inspect(operation, target, (), ctx)
        if kind == "click" and not legacy:
            facts = m.offers.project_click_offers(operation, facts)
        primitive = facts["primitives"][0]
        assert primitive["globalInput"] == "yes"
        s.operation, s.primitive, s.facts = operation, primitive, facts
        s.contract = operation.contracts[primitive["id"]]
        return facts

    def dispatch(*, foreground=False, step=1):
        command = {
            "kind": "pointer_sequence",
            "primitiveId": s.primitive["id"],
            "target": dict(target),
            "foreground": foreground,
        }
        execute = lambda: (
            m.click.NativeClickPipeline(None, unit, None).run(s.operation, command, ctx)
            if s.operation.click_contract == "v1"
            else unit.execute(s.operation, command, ctx)
        )
        result = s.operation.step(step, command, execute)
        return result, command

    s.ctx, s.desktop, s.captures, s.capture, s.target, s.unit = ctx, desktop, captures, capture, target, unit
    s.inspect, s.dispatch = inspect, dispatch
    return s


def move(scene, origin=(300, 200)):
    scene.current = replace(scene.current, rect=(*origin, *scene.current.rect[2:]))


@pytest.mark.parametrize("stage", ("before_inspect", "after_inspect", "exposure", "native_idle"))
def test_window_translation_uses_original_local_pixels_at_delivery(scene, stage):
    if stage == "before_inspect":
        move(scene)
    if stage == "exposure":
        scene.covered, scene.activation_hook = True, lambda: move(scene)
    scene.inspect()
    snapshot = deepcopy((scene.capture.window, scene.capture.image, scene.capture.transform))
    if stage == "after_inspect":
        move(scene)
    if stage == "native_idle":
        scene.idle_hook = lambda: move(scene)

    receipt, _ = scene.dispatch(foreground=stage == "exposure")

    assert receipt["dispatch"] == "dispatched" and receipt["errorCode"] is None
    assert scene.events == [(6, 0, 320, 230), (4, 1, 0, 0), (5, 1, 0, 0)]
    assert scene.hits[-1] == (320, 230)
    assert (scene.capture.window, scene.capture.image, scene.capture.transform) == snapshot
    assert scene.activations == (["original"] if stage == "exposure" else [])


def test_fractional_capture_scale_is_retained_without_rewriting_capture(scene):
    scene.capture.image = {"width": 320, "height": 240}
    scene.capture.transform = (100, 80, 1.25, 1.25)
    scene.inspect(parameters={"x": 10.4, "y": 11.2})
    move(scene)

    receipt, _ = scene.dispatch()

    assert receipt["dispatch"] == "dispatched" and scene.events[0] == (6, 0, 313, 214)
    assert scene.capture.transform == (100, 80, 1.25, 1.25)


def test_translation_uses_client_origin_instead_of_decoration_frame(scene):
    scene.inspect()
    move(scene)
    scene.current = replace(scene.current, frame_rect=(290, 170, 420, 340))

    receipt, _ = scene.dispatch()

    assert receipt["dispatch"] == "dispatched" and scene.events[0] == (6, 0, 320, 230)


def test_x11_point_wrapper_accepts_only_translation_of_same_capture(scene):
    move(scene)
    assert scene.desktop.point(scene.capture, 10, 15) == (320, 230)
    scene.current = replace(scene.current, rect=(300, 200, 400, 301))
    with pytest.raises(scene.failure, match="size changed"):
        scene.desktop.point(scene.capture, 10, 15)


def test_drag_endpoints_are_resolved_from_the_same_native_sample(scene):
    scene.inspect("drag")
    move(scene)
    info = scene.desktop.info
    calls = []

    def sampled(key):
        calls.append(key)
        return info(key)

    scene.desktop.info = sampled
    points, _, native = scene.unit.geometry(scene.operation, scene.target, scene.ctx)

    assert calls == [42] and native.rect == (300, 200, 400, 300)
    assert points == ((320, 230), (460, 330))


def test_legacy_pointer_click_also_freezes_after_native_idle(scene):
    scene.inspect(legacy=True)
    scene.idle_hook = lambda: move(scene)

    receipt, _ = scene.dispatch()

    assert receipt["dispatch"] == "dispatched"
    assert scene.events == [(6, 0, 320, 230), (4, 1, 0, 0), (5, 1, 0, 0)]


def test_prepare_receiver_may_translate_without_freezing_next_dispatch(scene):
    scene.covered = True
    scene.activation_hook = lambda: move(scene)
    scene.inspect("scroll")
    command = {
        "kind": "prepare_receiver",
        "primitiveId": scene.primitive["id"],
        "target": dict(scene.target),
        "foreground": True,
    }

    prepared = scene.operation.step(1, command, lambda: scene.unit.execute(scene.operation, command, scene.ctx))

    assert prepared["confirmation"] == "confirmed" and scene.events == []
    # A distinct dispatch step still resolves the delivery origin, rather than
    # inheriting a freeze from foreground preparation's final reachability check.
    move(scene, (500, 400))
    receipt, _ = scene.dispatch(step=2)
    assert receipt["dispatch"] == "dispatched" and scene.events[0] == (6, 0, 520, 430)


@pytest.mark.parametrize("kind", ("scroll", "drag"))
def test_entire_translated_gesture_uses_one_delivery_origin(scene, kind):
    scene.inspect(kind)
    move(scene)

    receipt, _ = scene.dispatch()

    assert receipt["dispatch"] == "dispatched" and receipt["errorCode"] is None
    assert scene.events[0] == (6, 0, 320, 230)
    motions = [event[2:] for event in scene.events if event[0] == 6]
    if kind == "drag":
        assert len(motions) == 25 and motions[-1] == (460, 330)
        assert scene.events[-1] == (5, 1, 0, 0)
    else:
        assert scene.events[1:] == [(4, 5, 0, 0), (5, 5, 0, 0)] * 2


def test_window_move_after_freeze_before_first_motion_stops_without_rebasing(scene):
    scene.inspect()
    frozen_at_move = []

    def move_when_frozen():
        if scene.contract.get("gesture") is None:
            scene.geometry_hook = move_when_frozen
            return
        frozen_at_move.append(scene.contract["gesture"].points)
        move(scene)

    # Skip admission's geometry preflight. Change the window during the actual
    # event-plan bounds query, after the gesture's original origin was frozen.
    scene.idle_hook = lambda: setattr(scene, "geometry_hook", move_when_frozen)

    receipt, _ = scene.dispatch()

    assert receipt["dispatch"] == "notDispatched" and receipt["errorCode"] == "stale_target"
    assert frozen_at_move == [((120, 110),)] and scene.events == []
    assert "frozen" in receipt["detail"]


@pytest.mark.parametrize("change", ("move", "resize", "cover", "modal", "scope", "pid", "birth"))
def test_changes_after_initial_motion_stop_before_press_and_do_not_replay(scene, change):
    scene.inspect()

    def after_motion(kind, *_):
        if kind != 6:
            return
        if change == "move":
            move(scene)
        elif change == "resize":
            scene.current = replace(scene.current, rect=(100, 80, 401, 300))
        elif change == "cover":
            scene.covered = True
        elif change == "modal":
            scene.modal = True
        elif change == "scope":
            scene.scope_valid = False
        elif change == "pid":
            scene.current = replace(scene.current, pid=124)
        else:
            scene.current = replace(scene.current, process_start="new-birth")

    scene.event_hook = after_motion
    receipt, command = scene.dispatch()

    assert receipt["dispatch"] == "partial" and receipt["retryReason"] is None
    assert scene.events == [(6, 0, 120, 110)]
    assert receipt["click"]["continuation"]["kind"] == "observe"
    assert scene.operation.step(1, command, lambda: pytest.fail("must retain receipt")) == receipt
    with pytest.raises(scene.failure, match="already ended or dispatched"):
        scene.dispatch(step=2)
    assert len(scene.events) == 1


@pytest.mark.parametrize(
    "change",
    (
        "resize",
        "offscreen",
        "covered",
        "modal",
        "scope",
        "xid",
        "pid",
        "birth",
        "unmapped",
        "closed",
        "capture",
        "target",
    ),
)
def test_pre_dispatch_boundaries_remain_required(scene, change):
    scene.inspect()
    if change == "resize":
        scene.current = replace(scene.current, rect=(300, 200, 401, 300))
    elif change == "offscreen":
        move(scene, (1590, 990))
    elif change == "covered":
        scene.covered = True
    elif change == "modal":
        scene.modal = True
    elif change == "scope":
        scene.scope_valid = False
    elif change == "xid":
        scene.current = replace(scene.current, key=43)
    elif change == "pid":
        scene.current = replace(scene.current, pid=124)
    elif change == "birth":
        scene.current = replace(scene.current, process_start="new-birth")
    elif change == "unmapped":
        scene.current = replace(scene.current, mapped=False)
    elif change == "capture":
        scene.captures.entries.clear()
    elif change == "target":
        scene.target["surface"] = "other-surface"
        with pytest.raises(scene.failure, match="changed target"):
            scene.dispatch()
        assert scene.events == [] and scene.activations == []
        return
    else:
        scene.selected.alive = False

    receipt, _ = scene.dispatch()

    assert receipt["dispatch"] == "notDispatched" and receipt["errorCode"] is not None
    assert scene.events == [] and scene.activations == []


def test_drag_rejects_offscreen_endpoint_even_if_press_point_is_inside_screen(scene):
    scene.inspect("drag")
    move(scene, (1500, 900))

    receipt, _ = scene.dispatch()

    assert receipt["dispatch"] == "notDispatched" and scene.events == []


def test_xtest_absolute_coordinate_limit_is_preserved(scene):
    scene.inspect()
    scene.screen = (50000, 1000)
    move(scene, (32760, 200))

    receipt, _ = scene.dispatch()

    assert receipt["dispatch"] == "notDispatched" and scene.events == []


@pytest.mark.parametrize("kind", ("click", "scroll"))
def test_movement_between_repeated_presses_stops_the_remaining_train(scene, kind):
    scene.inspect(
        kind, parameters=dict(x=10, y=15, **({"count": 2} if kind == "click" else {"direction": "down", "pages": 1}))
    )

    def after_release(event, *_):
        if event == 5:
            move(scene)

    scene.event_hook = after_release
    receipt, _ = scene.dispatch()

    assert receipt["dispatch"] in ("partial", "unknown") and receipt["retryReason"] is None
    assert len(scene.events) == 3 and scene.events[0] == (6, 0, 120, 110)
    assert not scene.desktop.buttons


def test_drag_after_button_down_keeps_original_absolute_path_under_grab(scene):
    scene.inspect("drag")
    move(scene)

    def after_down(kind, *_):
        if kind == 4:
            scene.current = replace(scene.current, rect=(600, 500, 700, 450))
            scene.covered, scene.modal = True, True

    scene.event_hook = after_down
    receipt, _ = scene.dispatch()

    assert receipt["dispatch"] == "dispatched"
    assert scene.events[0] == (6, 0, 320, 230) and scene.events[-2] == (6, 0, 460, 330)
    assert scene.events[-1] == (5, 1, 0, 0) and not scene.desktop.buttons


def test_drag_identity_loss_after_down_releases_without_new_motion_or_replay(scene):
    scene.inspect("drag")
    scene.event_hook = lambda kind, *_: setattr(scene.selected, "alive", False) if kind == 4 else None

    receipt, command = scene.dispatch()

    assert receipt["dispatch"] == "unknown" and receipt["retryReason"] is None
    assert scene.events == [(6, 0, 120, 110), (4, 1, 0, 0), (5, 1, 0, 0)]
    assert not scene.desktop.buttons
    assert scene.operation.step(1, command, lambda: pytest.fail("must not replay")) == receipt


@pytest.mark.parametrize("kind", ("click", "scroll", "drag"))
def test_desktop_capture_keeps_original_screen_pixels_when_windows_move(scene, kind):
    scene.inspect(kind, desktop_target=True)
    move(scene)

    receipt, _ = scene.dispatch()

    assert receipt["dispatch"] == "dispatched" and scene.events[0] == (6, 0, 20, 30)
    if kind == "drag":
        assert scene.events[-2] == (6, 0, 160, 130)
    assert scene.activations == []


def test_desktop_capture_screen_resize_still_invalidates_coordinates(scene):
    scene.inspect(desktop_target=True)
    scene.screen = (1700, 1000)

    receipt, _ = scene.dispatch()

    assert receipt["dispatch"] == "notDispatched" and scene.events == []


def test_existing_fixed_point_callbacks_keep_their_validation_return_contract(scene):
    scene.desktop.pointer_sequence(
        "click",
        ((25, 35),),
        scene.ctx,
        callbacks=scene.modules.pointer.PointerCallbacks(validate=lambda: ((800, 900),)),
    )

    assert scene.events == [(6, 0, 25, 35), (4, 1, 0, 0), (5, 1, 0, 0)]


@pytest.mark.parametrize("point", ((-1, 0), (200, 0), (0, 150)))
def test_png_local_bounds_are_checked_before_pointer_or_activation(scene, point):
    with pytest.raises(scene.failure, match="outside the captured image"):
        scene.inspect(parameters={"x": point[0], "y": point[1]})
    assert scene.events == [] and scene.activations == []


@pytest.mark.parametrize("kind", ("click", "scroll", "drag"))
def test_global_pointer_permission_denied_before_exposure_or_dispatch(scene, kind):
    scene.desktop.allow_global_input = False
    scene.covered = True
    scene.inspect(kind)
    if kind == "click":
        receipt, _ = scene.dispatch(foreground=True)
    else:
        receipt = scene.unit.execute(
            scene.operation,
            dict(
                kind="prepare_receiver", primitiveId=scene.primitive["id"], target=dict(scene.target), foreground=True
            ),
            scene.ctx,
        )
    assert receipt["errorCode"] == "permission_required"
    assert receipt["dispatch"] == "notDispatched"
    assert not scene.activations and not scene.events
    assert not scene.operation.dispatched
    assert scene.ctx.effect == "none"


def test_native_event_boundary_denies_missing_permission_before_xlib(scene):
    del scene.desktop.allow_global_input
    # Call the real boundary; the fixture normally records events in memory.
    with pytest.raises(scene.failure, match="global keyboard or pointer") as caught:
        type(scene.desktop)._event(scene.desktop, 6, x=10, y=10)
    assert caught.value.code == "permission_required"
    assert not scene.events
