# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A receipt label names the actual bound object, never a replacement slot."""

from types import SimpleNamespace

import pytest
from computer_use.interaction.requests import ExecuteAction
from computer_use.interaction.write.request.pipeline import _target_label
from computer_use.native.contracts import Binding, Intent
from computer_use.session.interfaces import WindowIdentity


@pytest.mark.parametrize("changed", [None, "surface", "instance", "missing"])
def test_receipt_label_does_not_borrow_a_replacement_windows_public_reference(changed):
    target = Binding("session", "original-app-instance", "root", "original-surface", "button")
    snapshot = {"binding": target}
    member = {"window": target.root_window, "instance": target.instance, "surface": target.surface}
    if changed in ("surface", "instance"):
        member[changed] = "replacement"
    instance = SimpleNamespace(
        members={} if changed == "missing" else {"root": member},
        windows={7: "root"},
        refs={3: snapshot},
    )
    request = ExecuteAction(SimpleNamespace(_instance=instance), Intent("click", {}), target, snapshot=snapshot)
    assert _target_label(request, target) == ("Window 7 / Element 3" if changed is None else None)
    assert request.target is target and request.snapshot is snapshot


@pytest.mark.parametrize("changed", [None, "surface", "instance", "missing"])
def test_image_receipt_alias_never_borrows_a_replacement_host(changed):
    target = Binding("session", "popup-instance", "popup", "popup-surface", capture="menu-image")
    host = {"window": "host", "surface": "host-surface", "instance": "host-instance"}
    displayed = WindowIdentity.of(host)
    if changed in ("surface", "instance"):
        host[changed] = "replacement"
    members = {"popup": {"window": "popup", "surface": target.surface, "instance": target.instance}}
    if changed != "missing":
        members["host"] = host
    instance = SimpleNamespace(members=members, windows={0: "host", 1: "popup"})
    request = ExecuteAction(SimpleNamespace(_instance=instance), Intent("click", {}), target, display_window=displayed)
    assert _target_label(request, target) == ("Window 0" if changed is None else "Window 1")
    assert request.target is target and request.target.root_window == "popup"
    # Computer input still describes the global operation, not this optional
    # display alias or the App's captured native receiver.
    global_request = ExecuteAction(None, Intent("click", {}, True), target, display_window=displayed)
    assert _target_label(global_request, target) == "Computer"
