"""Unprefixed standard output for native command-line tools."""

import logging
import sys
from threading import RLock

_OUTPUT = logging.getLogger(__name__)
_OUTPUT.setLevel(logging.INFO)
_OUTPUT.propagate = False
_LOCK = RLock()


def report(message: object) -> None:
    """Emit one message using the current standard output stream."""
    with _LOCK:
        handler = logging.StreamHandler(sys.stdout)
        _OUTPUT.addHandler(handler)
        try:
            _OUTPUT.info("%s", message)
        finally:
            _OUTPUT.removeHandler(handler)
            handler.close()
