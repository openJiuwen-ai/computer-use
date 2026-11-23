# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Presentation options scoped to one public observation call."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_visual_text = ContextVar("computer_use_visual_text", default=False)


@contextmanager
def visual_text_scope(enabled: bool) -> Iterator[None]:
    """Ask native captures in this observation to return OCR evidence.

    This is a presentation option, not an input or scope decision: it only
    controls whether a window capture also collects text regions from the
    pixels it already photographed. The scope ends with the public read.
    """
    token = _visual_text.set(enabled)
    try:
        yield
    finally:
        _visual_text.reset(token)


def visual_text_requested() -> bool:
    """Whether the current observation asked captures to collect OCR evidence."""
    return _visual_text.get()
