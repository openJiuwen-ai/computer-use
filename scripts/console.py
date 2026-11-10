# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Command-line results and diagnostic reporting."""

import logging
import sys
from threading import RLock
from typing import TextIO

_OUTPUT = logging.getLogger(__name__)
_OUTPUT.setLevel(logging.INFO)
_OUTPUT.propagate = False
_LOCK = RLock()


def write_result(stream: TextIO, value: str) -> None:
    """Write one machine-readable result independently of log configuration."""
    stream.write(f"{value}\n")


class _StrictStreamHandler(logging.StreamHandler):
    """Keep output delivery errors visible to command-line callers."""

    def handleError(self, record):
        error = sys.exception()
        if error is None:
            raise RuntimeError("Output error handler requires an active exception")
        raise error


def report(message: object, *, stream=None, raise_errors: bool = False) -> None:
    """Emit to stdout by default; result packets can require delivery errors."""
    with _LOCK:
        handler_type = _StrictStreamHandler if raise_errors else logging.StreamHandler
        handler = handler_type(sys.stdout if stream is None else stream)
        _OUTPUT.addHandler(handler)
        try:
            _OUTPUT.info("%s", message)
        finally:
            _OUTPUT.removeHandler(handler)
            handler.close()
