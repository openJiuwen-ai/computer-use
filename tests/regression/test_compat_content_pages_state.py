# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A continuation alias is reused only for its exact provider prefix."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.pages import ContentPages
from computer_use.native.contracts import Binding

TARGET = Binding("session", "app", "window", "surface", "parent", capture="old-image")


def observed(cursor, *, total=9, inspected_count=3):
    page = {
        "reference": "7",
        "node": "parent",
        "property": "AXChildren",
        "kind": "children",
        "cursor": cursor,
        "offset": inspected_count,
        "total": total,
    }
    return SimpleNamespace(public_pages=lambda _target: [page])


def test_reobserving_exact_page_touches_lru_but_resolve_and_display_do_not():
    pages = ContentPages(capacity=2)
    a, b, c, d = [observed(name) for name in "abcd"]
    pages.remember(TARGET, a)
    pages.remember(TARGET, b)
    assert pages.resolve("p1", replace(TARGET, capture="new-image")) == {
        "property": "AXChildren",
        "cursor": "a",
        "limit": 128,
    }
    assert pages.hints(TARGET, a) == '\nContinuations:\n7 children: next offset 3 of 9; cursor="p1" (live order)'
    pages.remember(TARGET, c)
    with pytest.raises(ComputerUseError) as expired:
        pages.resolve("p1", TARGET)
    assert expired.value.code == "snapshot_expired"
    pages.remember(TARGET, b)
    pages.remember(TARGET, d)
    assert pages.resolve("p2", TARGET)["cursor"] == "b"
    with pytest.raises(ComputerUseError) as evicted:
        pages.resolve("p3", TARGET)
    assert evicted.value.code == "snapshot_expired"
    pages.remember(TARGET, a)
    assert pages.resolve("p5", TARGET)["cursor"] == "a"
    assert pages.hints(TARGET, a, public_api=True) == '\nContinuations:\n7 children: cursor="p5" (live order)'


def test_page_identity_counts_sparse_positions_and_retains_window_scope():
    pages = ContentPages()
    original = observed("shared-prefix")
    other_range = observed("shared-prefix", inspected_count=5)
    other_window = replace(TARGET, root_window="different-window")
    pages.remember(TARGET, original)
    pages.remember(TARGET, other_range)
    assert "next offset 3 of 9" in pages.hints(TARGET, original)
    assert "next offset 5 of 9" in pages.hints(TARGET, other_range)
    assert pages.resolve("p1", TARGET) == pages.resolve("p2", TARGET)
    for alias in ("p1", "p2"):
        with pytest.raises(ComputerUseError) as mismatch:
            pages.resolve(alias, other_window)
        assert (mismatch.value.code, mismatch.value.target, mismatch.value.effect) == (
            "invalid_request",
            "element content",
            "none",
        )
        assert mismatch.value.detail == "This continuation is expired or belongs to a different element or window."
