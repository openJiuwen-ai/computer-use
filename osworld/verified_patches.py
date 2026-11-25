# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runtime fixes for OSWorld-Verified code that breaks on a Windows host or a slow one.

The upstream checkout stays untouched; run_task_sdk.py applies these to
the imported modules instead.
"""
from __future__ import annotations

import functools
import re
import tempfile
from types import ModuleType


class _CleanupTolerantTempfile:
    """tempfile, except TemporaryDirectory ignores errors when it is removed."""

    def __getattr__(self, name: str):
        return getattr(tempfile, name)

    @staticmethod
    def TemporaryDirectory(*args, **kwargs):
        kwargs.setdefault("ignore_cleanup_errors", True)
        return tempfile.TemporaryDirectory(*args, **kwargs)


def tolerate_setup_tempdir_cleanup(setup_module: ModuleType) -> None:
    """Let Verified's setup.py finish when it leaves a file open in a temp dir.

    _update_browse_history_setup uploads history_empty.sqlite with a bare
    open(db_path, "rb") that is never closed; on Windows the open file blocks
    removing its TemporaryDirectory, so the setup step fails every time
    (chrome 44ee5668, 2026-10-09: WinError 32). That is the module's only
    TemporaryDirectory, so the cost is one small file left in %TEMP% per run.
    """
    setup_module.tempfile = _CleanupTolerantTempfile()


_SLEEP_CALL = re.compile(r"time\.sleep\(\s*([0-9.]+)\s*\)")


def slow_scripted_waits(desktop_env_cls: type, setup_controller_cls: type, evaluate_factor: float = 5.0,
                        reset_factor: float = 3.0) -> None:
    """Lengthen the waits in the task's scripted GUI steps: the setup run by reset() and the grader's
    postconfig run by evaluate().

    Many GIMP graders export the result themselves: Shift+Ctrl+E, then type a file name about 1.5 s
    later, then Enter twice and read the file 5 s after. When GIMP's export dialog is slower than
    that, the name lands on the canvas and no file is written (gimp 554785e9, 7a4deb26, f4aec372,
    2a729ded, d16c99dc, 72f83cdc, night1026: "Failed to get file" after a finished agent), or the
    file is read half-written ("image file is truncated", 7a4deb26). Three times was not enough under
    heavy CPU load (load average 11 on 4 cores, 7a4deb26 noscriptfu1027: the same 404, then 1.0 at load 3),
    so evaluate() waits five times as long. A setup step that clicks 4 s after opening a file hits a
    window that is not drawn yet on a slow guest: impress 550ce7e7's setup selects slide 5 that way and
    every run started on slide 1, so reset() waits three times as long. Only the waits change: the
    sleep steps and the time.sleep calls of execute steps. The agent's run is untouched.
    """
    if getattr(desktop_env_cls.evaluate, "__wrapped__", None) is not None:
        return
    sleep_setup = setup_controller_cls._sleep_setup
    execute_setup = setup_controller_cls._execute_setup

    # On the class, not the instance: reset() may build a new controller (_start_emulator) mid-call.
    setup_controller_cls.postconfig_scale = 1.0

    def with_longer_waits(method, factor: float):
        @functools.wraps(method)
        def wrapped(self, *args, **kwargs):
            setup_controller_cls.postconfig_scale = factor
            try:
                return method(self, *args, **kwargs)
            finally:
                setup_controller_cls.postconfig_scale = 1.0
        return wrapped

    def scaled(text, scale: float):
        if not isinstance(text, str):
            return text
        return _SLEEP_CALL.sub(lambda m: f"time.sleep({float(m.group(1)) * scale:g})", text)

    @functools.wraps(sleep_setup)
    def _sleep_setup(self, seconds: float):
        return sleep_setup(self, seconds * getattr(self, "postconfig_scale", 1.0))

    @functools.wraps(execute_setup)
    def _execute_setup(self, command, **kwargs):
        scale = getattr(self, "postconfig_scale", 1.0)
        if scale != 1.0:
            command = [scaled(c, scale) for c in command] if isinstance(command, list) else scaled(command, scale)
        return execute_setup(self, command, **kwargs)

    desktop_env_cls.evaluate = with_longer_waits(desktop_env_cls.evaluate, evaluate_factor)
    if hasattr(desktop_env_cls, "reset"):
        desktop_env_cls.reset = with_longer_waits(desktop_env_cls.reset, reset_factor)
    setup_controller_cls._sleep_setup = _sleep_setup
    setup_controller_cls._execute_setup = _execute_setup
