# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Composite gestures keep one native capture despite unrelated window changes."""

import asyncio
import base64
import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.images.capture import WindowImage
from computer_use.interaction.write.ui.pipeline import InterfacePointerPipeline
from computer_use.native.contracts import Binding
from computer_use.protocol.captures import decode_capture
from computer_use.session.interface_images import ImagePiece, InterfaceImage
from PIL import Image

MODULE = "computer_use.interaction.write.ui.pipeline"
ROOT = ("root", "surface", None, "none", False)
CHILD = ("child", "child-surface", "root", "known", False)


def capture(window, width=100, height=80):
    encoded = BytesIO()
    Image.new("RGBA", (width, height), "white").save(encoded, format="PNG")
    png = decode_capture(
        {
            "id": "capture-" + window,
            "kind": "window",
            "scope": "surface-" + window,
            "startedAt": 1,
            "completedAt": 2,
            "width": width,
            "height": height,
            "png": base64.b64encode(encoded.getvalue()).decode(),
        },
        "window",
    )
    return WindowImage(
        Binding("session", "app", "root", "surface-" + window, capture="capture-" + window),
        png,
        {},
        {"toNative": (1, 0, 0, 1, 0, 0)},
    )


def setup_pipeline(monkeypatch, *, target_size=(100, 80), members=(ROOT, CHILD), captured_members=(ROOT, CHILD)):
    root, child = capture("root"), capture("child")
    image = InterfaceImage(
        root.image,
        (ImagePiece("root", root, (0, 0, 100, 80)), ImagePiece("child", child, (100, 0, 100, 80))),
        captured_members,
    )
    image.digest = hashlib.sha256(image.png).digest()
    queried = []

    async def window(target):
        queried.append(target)
        if target != root.target:
            raise AssertionError("Unrelated captures must not become gesture preconditions")
        return capture("root", *target_size)

    reader = SimpleNamespace(images=SimpleNamespace(window=window))
    controller = SimpleNamespace(reader=reader, adapters=SimpleNamespace(image_delivery_required=lambda: False))
    write = SimpleNamespace(arun=AsyncMock(return_value="dispatched"))
    monkeypatch.setattr(MODULE + ".validate_session", lambda *args: None)
    monkeypatch.setattr(MODULE + ".refresh", AsyncMock())
    monkeypatch.setattr(
        MODULE + ".allowed",
        lambda ui: {
            item[0]: dict(zip(("window", "surface", "parent", "parentState", "modal"), item)) for item in members
        },
    )
    ui = SimpleNamespace(_image=image, _anchor="root")
    return InterfacePointerPipeline(controller, write), ui, write, queried, root.target


@pytest.mark.parametrize(
    "intent,points", [("click", ((20, 30),)), ("scroll", ((20, 30),)), ("drag", ((20, 30), (40, 50)))]
)
def test_gesture_queries_only_its_original_member(monkeypatch, intent, points):
    pipeline, ui, write, queried, target = setup_pipeline(monkeypatch)
    assert asyncio.run(pipeline.run(ui, intent, points, {}, None)) == "dispatched"
    assert write.arun.call_args.args[0].target == target and queried == [target]
    assert not ui._image.expired


def test_confirmed_nonmodal_child_does_not_retarget_composite_gesture(monkeypatch):
    extra = ("new", "new-surface", "root", "known", False)
    pipeline, ui, write, _, target = setup_pipeline(monkeypatch, members=(ROOT, CHILD, extra))
    asyncio.run(pipeline.run(ui, "click", ((20, 30),), {}, None))
    assert write.arun.call_args.args[0].target == target


@pytest.mark.parametrize("original_peer", [(), (("peer", "old-peer-surface", None, "none", False),)])
def test_new_or_replaced_independent_peer_keeps_original_app_image_target(monkeypatch, original_peer):
    peer = ("peer", "new-peer-surface", None, "none", False)
    pipeline, ui, write, queried, target = setup_pipeline(
        monkeypatch, members=(ROOT, CHILD, peer), captured_members=(ROOT, CHILD, *original_peer)
    )
    assert asyncio.run(pipeline.run(ui, "click", ((20, 30),), {}, None)) == "dispatched"
    assert write.arun.call_args.args[0].target == target and queried == [target]
    assert not ui._image.expired


@pytest.mark.parametrize("members", [(ROOT,), (ROOT, ("child", "replacement-surface", "root", "known", False))])
def test_unaddressed_member_lifetime_does_not_expire_the_original_root_capture(monkeypatch, members):
    pipeline, ui, write, queried, target = setup_pipeline(monkeypatch, members=members)
    assert asyncio.run(pipeline.run(ui, "click", ((20, 30),), {}, None)) == "dispatched"
    assert write.arun.call_args.args[0].target == target and queried == [target]
    assert not ui._image.expired


@pytest.mark.parametrize(
    "members", [(ROOT,), (ROOT, ("child", "replacement-surface", "root", "known", False)), (CHILD,)]
)
def test_addressed_member_or_its_original_ancestor_cannot_be_replaced(monkeypatch, members):
    pipeline, ui, write, queried, _ = setup_pipeline(monkeypatch, members=members)
    with pytest.raises(ComputerUseError, match="membership or modal layout changed"):
        asyncio.run(pipeline.run(ui, "click", ((120, 30),), {}, None))
    write.arun.assert_not_awaited()
    assert not queried and ui._image.expired


@pytest.mark.parametrize("size", [(101, 80), (100, 81)])
def test_addressed_member_resize_still_requires_fresh_pixels(monkeypatch, size):
    pipeline, ui, write, _, _ = setup_pipeline(monkeypatch, target_size=size)
    with pytest.raises(ComputerUseError, match="window changed size"):
        asyncio.run(pipeline.run(ui, "click", ((20, 30),), {}, None))
    write.arun.assert_not_awaited()
    assert ui._image.expired


def test_drag_cannot_cross_original_member_mappings(monkeypatch):
    pipeline, ui, write, queried, _ = setup_pipeline(monkeypatch)
    with pytest.raises(ComputerUseError, match="one captured member window"):
        asyncio.run(pipeline.run(ui, "drag", ((20, 30), (120, 30)), {}, None))
    write.arun.assert_not_awaited()
    assert queried == []


@pytest.mark.parametrize("modal", [True, None])
def test_new_modal_or_unconfirmed_member_stops_before_input(monkeypatch, modal):
    extra = ("new", "new-surface", "root", "known", modal)
    pipeline, ui, write, queried, _ = setup_pipeline(monkeypatch, members=(ROOT, CHILD, extra))
    with pytest.raises(ComputerUseError, match="modal layout changed"):
        asyncio.run(pipeline.run(ui, "click", ((20, 30),), {}, None))
    write.arun.assert_not_awaited()
    assert queried == []
