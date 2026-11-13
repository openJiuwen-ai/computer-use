# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compare retained content without interpreting coverage loss as a change."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.accessibility.snapshot import ReadSnapshot


def same_content_scope(before: ReadSnapshot | None, after: ReadSnapshot | None) -> bool:
    return before is not None and after is not None and before.capture.same_content_scope(after.capture)


def has_content_progress(before: ReadSnapshot | None, after: ReadSnapshot | None) -> bool:
    return before is not None and after is not None and before.capture.has_content_progress(after.capture)


def prefer_earlier_budget_capture(before: ReadSnapshot | None, after: ReadSnapshot | None) -> bool:
    return before is not None and after is not None and before.capture.prefer_earlier_budget_capture(after.capture)
