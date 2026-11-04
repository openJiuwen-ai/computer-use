# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bounded App reads compose only their intended windows without losing catalogue evidence."""

import asyncio
import base64
from io import BytesIO
from types import SimpleNamespace

import pytest
from computer_use.client.images import image_delivery_epoch, image_label, register_image
from computer_use.errors import ComputerUseError
from computer_use.identity import Record
from computer_use.interaction.read.acquisition.images.capture import WindowImage
from computer_use.interaction.read.acquisition.images.composite.pipeline import InterfaceImagePipeline
from computer_use.interaction.read.views.pipeline import WindowPart
from computer_use.native.contracts import Binding
from computer_use.protocol.captures import capture_metadata, decode_capture
from computer_use.session.image_coordinates import ImageCoordinates
from computer_use.session.interface_images import signature
from computer_use.session.interfaces import InstanceState
from computer_use.session.operations import OperationBudget
from computer_use.session.windows import WindowReferences
from PIL import Image


def member(window, parent=None):
    return dict(
        window=window,
        surface="surface-" + window,
        instance="application",
        parent=parent,
        parentState="known" if parent is not None else "none",
        modal=False,
    )


def captured_part(window, *, x=0):
    buffer = BytesIO()
    with Image.new("RGBA", (100, 80), "white") as image:
        image.save(buffer, format="PNG")
    target = Binding("session", "application", window, "surface-" + window, capture="capture-" + window)
    png = decode_capture(
        dict(
            id="capture-" + window,
            kind="window",
            scope=target.surface,
            startedAt=1,
            completedAt=2,
            width=100,
            height=80,
            png=base64.b64encode(buffer.getvalue()).decode(),
        ),
        "window",
    )
    return WindowPart(
        target,
        image=WindowImage(target, png, {}, dict(nativeSpace="screen", toNative=(1, 0, 0, 1, x, 0))),
    )


def fixture(*, fixed=False):
    members = {item["window"]: item for item in (member("root"), member("other"), member("child", "root"))}
    state = InstanceState(
        selector="fixture",
        session="session",
        name="Fixture",
        directory=dict(surfaces=list(members.values())),
        members=members,
        windows={0: "root", 2: "other", 7: "child"},
    )
    ui = Record(_instance=state, _anchor="root" if fixed else None, _image=None)
    controller = Record(
        transport=Record(session="session"),
        interfaces=SimpleNamespace(windows=WindowReferences()),
        adapters=SimpleNamespace(register_image=register_image, image_delivery_epoch=image_delivery_epoch),
    )
    return ui, controller, InterfaceImagePipeline(controller)


def test_principal_image_remains_executable_with_unexpanded_independent_catalogue_windows():
    ui, controller, pipeline = fixture()
    part = captured_part("root")
    canvas, notes = pipeline.run(ui, [part])
    ui._image = canvas
    assert ui._instance.anchor is None
    assert not canvas.unavailable_mapping
    assert canvas.hit((20, 30)) == (canvas.pieces[0], (20, 30))
    assert canvas.pieces[0].image.target == part.target
    assert canvas.membership == signature(ui._instance.members)
    assert notes[-1].values[0].kind == "single"
    caption = image_label(canvas.png)
    assert "observed windows" in caption
    assert "Window reference: 0" in caption
    assert "unknown" not in caption
    assert controller.transport.session == "session"


def test_missing_a_selected_capture_still_returns_an_explicit_unclickable_partial_image():
    ui, controller, pipeline = fixture()
    root, child = captured_part("root"), captured_part("child", x=120)
    child.image = None
    canvas, notes = pipeline.run(ui, [root, child])
    ui._image = canvas
    assert canvas.unavailable_mapping
    assert notes[-1].values[0].kind == "partial"
    assert canvas.membership == signature(ui._instance.members)
    with pytest.raises(ComputerUseError, match="partial image"):
        canvas.hit((20, 30))
    caption = image_label(canvas.png, coordinates=ImageCoordinates((100, 80), (100, 80)))
    assert "no executable coordinate mapping" in caption
    assert "Use this displayed image's pixels" not in caption
    assert "Window reference: 0" in caption
    assert "Window references" not in caption
    assert controller.transport.session == "session"


def test_fixed_window_composite_needs_no_prior_app_primary_or_anchor():
    ui, controller, pipeline = fixture(fixed=True)
    canvas, notes = pipeline.run(ui, [captured_part("root"), captured_part("child", x=120)])
    ui._image = canvas
    assert ui._instance.anchor is None
    assert not canvas.unavailable_mapping
    assert capture_metadata(canvas.png)["scope"] == "ui:surface-root:root"
    assert notes[-1].values[0].kind == "composite"
    piece, point = canvas.hit((140, 30))
    assert piece.window == "child" and point == (20, 30)
    caption = image_label(canvas.png)
    assert "fixed Window" in caption
    assert "Window references: 0, 7" in caption
    assert "unknown" not in caption
    assert controller.transport.session == "session"


def expired_budget(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    budget = OperationBudget(clock=lambda: clock.now)
    clock.now = 1.001
    monkeypatch.setattr(
        "computer_use.interaction.read.acquisition.images.composite.pipeline.current_operation_budget", lambda: budget
    )
    return clock, budget


def test_expired_composition_keeps_one_original_png_and_its_executable_mapping(monkeypatch):
    ui, _, pipeline = fixture()
    root, child = captured_part("root"), captured_part("child", x=120)
    expired_budget(monkeypatch)
    monkeypatch.setattr(Image, "new", lambda *args, **kwargs: pytest.fail("No new pixel canvas after the deadline"))
    gaps = []
    canvas, notes = pipeline.run(ui, [root, child], on_gap=gaps.append)
    assert canvas.png is root.image.image
    assert not canvas.unavailable_mapping
    assert canvas.pieces[0].image is root.image and canvas.pieces[0].box == (0, 0, 100, 80)
    assert canvas.hit((20, 30)) == (canvas.pieces[0], (20, 30))
    header = notes[-1].values[0]
    assert header.kind == "single" and header.windows == (0,) and header.mapping_available
    assert [item["displayed"] for item in header.captures] == [True, False]
    assert len(gaps) == 1 and gaps[0].code == "observation_incomplete"
    assert "original Window" in str(notes[0])
    caption = image_label(canvas.png)
    assert "Window reference: 0" in caption and "Window references:" not in caption


def test_expiry_inside_a_piece_stops_before_more_pieces_or_png_encoding(monkeypatch):
    ui, _, pipeline = fixture()
    root, child = captured_part("root"), captured_part("child", x=120)
    clock = SimpleNamespace(now=0.0)
    budget = OperationBudget(clock=lambda: clock.now)
    monkeypatch.setattr(
        "computer_use.interaction.read.acquisition.images.composite.pipeline.current_operation_budget", lambda: budget
    )
    original = Image.Image.resize
    resized = []

    def resize(image, *args, **kwargs):
        result = original(image, *args, **kwargs)
        resized.append(image)
        clock.now = 1.001
        return result

    monkeypatch.setattr(Image.Image, "resize", resize)
    monkeypatch.setattr(Image.Image, "save", lambda *args, **kwargs: pytest.fail("Do not encode after the deadline"))
    canvas, _ = pipeline.run(ui, [root, child])
    assert len(resized) == 1
    assert canvas.png is root.image.image and canvas.hit((20, 30))[1] == (20, 30)


@pytest.mark.parametrize("exact_identity", [False, True])
def test_fixed_window_preference_requires_the_exact_original_identity(monkeypatch, exact_identity):
    ui, _, pipeline = fixture(fixed=True)
    root, child = captured_part("root"), captured_part("child", x=120)
    ui._window_identity = Binding(
        "session", "application", "root", "surface-root" if exact_identity else "different-lifetime"
    )
    expired_budget(monkeypatch)
    canvas, notes = pipeline.run(ui, [child, root])
    expected = root if exact_identity else child
    assert canvas.png is expected.image.image and canvas.pieces[0].image is expected.image
    assert notes[-1].values[0].windows == ((0,) if exact_identity else (7,))


def test_expired_multiwindow_read_with_a_missing_capture_keeps_the_available_original_map(monkeypatch):
    ui, _, pipeline = fixture()
    root, child = captured_part("root"), captured_part("child", x=120)
    child.image = None
    expired_budget(monkeypatch)
    canvas, _ = pipeline.run(ui, [root, child])
    assert canvas.png is root.image.image and not canvas.unavailable_mapping
    assert canvas.hit((20, 30))[1] == (20, 30)


def test_explicit_cancellation_is_not_downgraded_to_an_image_gap(monkeypatch):
    ui, _, pipeline = fixture()
    root, child = captured_part("root"), captured_part("child", x=120)
    budget = OperationBudget(cancelled=lambda: True)
    monkeypatch.setattr(
        "computer_use.interaction.read.acquisition.images.composite.pipeline.current_operation_budget", lambda: budget
    )
    gaps = []
    with pytest.raises(asyncio.CancelledError):
        pipeline.run(ui, [root, child], on_gap=gaps.append)
    assert not gaps
