# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A receipt label names the actual bound object, never a replacement slot."""

from types import SimpleNamespace

import pytest
from computer_use.interaction.requests import ExecuteAction
from computer_use.interaction.write.request.pipeline import _target_label
from computer_use.native.contracts import Binding, Intent


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
