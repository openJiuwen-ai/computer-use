"""Process startup helpers for disposable developer fixtures."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def launch_logged_process(command: list[str], log_path: Path, **options: Any) -> subprocess.Popen:
    """Start a caller-owned process and release the parent's log descriptor.

    The child inherits its own stdout/stderr handles. Closing the parent's file
    after process creation keeps logging active and also covers startup errors.
    The caller retains responsibility for bounded termination and reaping.
    """
    with log_path.open("wb") as stream:
        return subprocess.Popen(command, stdout=stream, stderr=stream, **options)
