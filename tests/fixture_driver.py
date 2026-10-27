"""Launch Python protocol fixtures with a per-child environment on every host."""

import sys

from repository import FIXTURES


def environmentDriver(args, timeoutMs=5000):
    return dict(
        command=sys.executable,
        args=["-u", str(FIXTURES / "env_driver.py"), *args],
        **(dict(timeoutMs=timeoutMs) if timeoutMs is not None else {}),
    )
