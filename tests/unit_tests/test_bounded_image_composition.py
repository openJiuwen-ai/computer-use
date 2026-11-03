# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bounded App reads compose only their intended windows without losing catalogue evidence."""

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
