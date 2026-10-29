# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build lookup snapshot."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.interaction.query.model import FindLease
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.interaction.records import ElementSnapshot
    from computer_use.native.contracts import Binding
    from computer_use.pipeline.core import Control


def create(
    *, snapshots: Callable[[Binding, SemanticSnapshot], dict[str, ElementSnapshot]]
) -> Callable[[FindLease, Control], ElementSnapshot]:
    """Bind the snapshot builder used to retain a resolved element.

    Args:
        snapshots: Build reference-indexed snapshots from a native binding and tree.
    """

    def snapshot(value: FindLease, control: Control) -> ElementSnapshot:
        """Build the matched element's snapshot from its collected subtree.

        The result retains the native binding and captured continuation sources.
        Its display reference is cleared because lookup has not published a
        number for this result.
        """
        ref = next(
            (
                ref
                for ref, node in cast("SemanticSnapshot", value.tree).refs.items()
                if node == cast("Binding", value.selected).node
            )
        )
        result = snapshots(cast("Binding", value.selected), cast("SemanticSnapshot", value.tree))[ref]
        result["source"]["ref"] = None
        return result

    return snapshot


stage = Pass.factory(
    name="build_lookup_snapshot",
    factory=create,
    contract=Contract(FindLease, dict),
)
