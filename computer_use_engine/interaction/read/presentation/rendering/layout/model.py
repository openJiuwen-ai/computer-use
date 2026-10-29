# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.read.presentation.records import FormatField, NodeView, RenderedTree
    from computer_use.protocol.records import Record


@dataclass(frozen=True)
class LayoutFrame:
    """Rendered lines and their pure layout continuation; source bindings stay fixed.

    Attributes:
        lines: Rendered observation lines, in their current display order.
        roots: Ordered roots in this observed or projected view.
        context: Native window or application context used to resolve menu sources.
        format_field: Pure formatter for one text field.
        records: Layout-stage diagnostics in execution order.
        finalize: Pure renderer continuation called after the line-layout stages complete.
    """

    lines: list[str]
    roots: Sequence[NodeView]
    context: Record
    format_field: FormatField
    records: tuple[Record, ...] = ()
    finalize: Callable[[list[str], list[Record]], RenderedTree] | None = None
