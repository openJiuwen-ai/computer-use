# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unprefixed command-line output through standard logging."""

import logging
import sys
from threading import RLock

_OUTPUT = logging.getLogger(__name__)
_OUTPUT.setLevel(logging.INFO)
_OUTPUT.propagate = False
_LOCK = RLock()


def report(message: object, *, stream=None) -> None:
    """Emit one message to the supplied stream, defaulting to standard output."""
    with _LOCK:
        handler = logging.StreamHandler(sys.stdout if stream is None else stream)
        _OUTPUT.addHandler(handler)
        try:
            _OUTPUT.info("%s", message)
        finally:
            _OUTPUT.removeHandler(handler)
            handler.close()
