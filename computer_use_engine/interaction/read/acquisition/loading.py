# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Use the retained provider evidence to decide whether a window is loading."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.accessibility.snapshot import ReadSnapshot


def window_content_unexposed(snapshot: ReadSnapshot | None) -> bool:
    return snapshot is not None and snapshot.capture.window_content_unexposed()


def busy_nodes(snapshot: ReadSnapshot | None) -> tuple[str, ...]:
    return tuple(snapshot.capture.busy_nodes()) if snapshot is not None else ()


def refreshable(snapshot: ReadSnapshot | None) -> bool:
    """Avoid replaying collection failures as evidence of application loading."""
    return snapshot is not None and snapshot.capture.refreshable()
