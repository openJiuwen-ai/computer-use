# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build lookup snapshot."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.query.model import FindLease
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.accessibility.snapshot import ReadSnapshot, RetainedElement
    from computer_use.interaction.records import ElementSnapshot
    from computer_use.native.contracts import Binding
    from computer_use.pipeline.core import Control


def create(
    *,
    native_snapshots: Callable[[Binding, str, list[RetainedElement]], dict[str, ElementSnapshot]],
) -> Callable[[FindLease, Control], ElementSnapshot]:
    """Bind the snapshot builder used to retain a resolved element.

    Args:
        snapshots: Build reference-indexed snapshots from a native binding and tree.
        native_snapshots: Build the same retained snapshots from native binding rows.
    """

    def snapshot(value: FindLease, control: Control) -> ElementSnapshot:
        """Build the matched element's snapshot from its collected subtree.

        The result retains the native binding and captured continuation sources.
        Its display reference is cleared because lookup has not published a
        number for this result.
        """
        tree = cast("ReadSnapshot", value.tree)
        selected = cast("Binding", value.selected)
        rows = tree.retained(selected, include_public_pages=False)
        row = next(row for row in rows if row["node"] == selected.node)
        result = native_snapshots(selected, tree.session, [row])[row["reference"]]
        result["source"]["ref"] = None
        if result.get("scope_kind") == "applicationMenu":
            result["menu_window_identity"] = value.plan.state.menu_window_identity
        return result

    return snapshot


stage = Pass.factory(
    name="build_lookup_snapshot",
    factory=create,
    contract=Contract(FindLease, dict),
)
