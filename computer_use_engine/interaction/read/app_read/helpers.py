# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.app_read.model import ReadRequest
    from computer_use.pipeline.core import Control
from dataclasses import replace

from computer_use.interaction.read.app import RunningApplication, SelectedApplicationWindow, WorkingWindow


def parse_read_request(request: ReadRequest, control: Control) -> ReadRequest:
    """Parse read request.

    Args:
        request: Application observation options before working-scope selection.
    """
    selector, window = request.selector, request.window
    if (
        not isinstance(selector, (str, RunningApplication, WorkingWindow))
        or isinstance(selector, str)
        and not selector.strip()
    ):
        raise ValueError("Expected an application selector or an observed application/window")
    if request.on_scope is not None and not callable(request.on_scope):
        raise ValueError("The scope observer must be callable")
    if type(request.allow_launch) is not bool or window is not None and (not isinstance(window, str) or not window):
        raise ValueError("Invalid launch option or observed window id")
    if isinstance(selector, WorkingWindow) and window is not None:
        raise ValueError("A bound WorkingWindow cannot be retargeted; select a new working window explicitly")
    if isinstance(selector, SelectedApplicationWindow):
        if window is not None and window != selector.window:
            raise ValueError("An exact window choice cannot be retargeted")
        window = selector.window
    if (
        request.max_nodes is not None
        and (type(request.max_nodes) is not int or not 1 <= request.max_nodes <= 2000)
        or request.max_depth is not None
        and (type(request.max_depth) is not int or not 0 <= request.max_depth <= 2000)
        or type(request.max_dimension) is not int
        or not 1 <= request.max_dimension <= 4096
    ):
        raise ValueError("Invalid app observation budget")
    path = request.menu_path
    if path is not None and (
        not isinstance(path, (list, tuple))
        or not 1 <= len(path) <= 8
        or any(not isinstance(title, str) or not title.strip() for title in path)
    ):
        raise ValueError("A menu path contains one to eight exact observed titles")
    return replace(request, window=window)
