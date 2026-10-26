"""Resolve an executable before handing a command to the operating system."""

from __future__ import annotations

import errno
import os
import shutil
from collections.abc import Mapping
from os import PathLike


def resolve_executable(
    name: str, *, env: Mapping[str, str] | None = None, cwd: str | PathLike[str] | None = None
) -> str:
    """Use the child's POSIX search path, or Windows' parent-process search path.

    Relative PATH entries are anchored before process creation, including the
    empty entry that means the working directory. Resolution reports the same
    missing-program error instead of passing None as an executable.
    """
    if os.name == "nt":
        environment, directory = os.environ, os.getcwd()
    else:
        environment = os.environ if env is None else env
        directory = os.path.abspath(cwd if cwd is not None else os.getcwd())
    search = os.pathsep.join(
        entry if os.path.isabs(entry) else os.path.join(directory, entry)
        for entry in environment.get("PATH", os.defpath).split(os.pathsep)
    )
    candidate = os.path.join(directory, name) if os.path.dirname(name) and not os.path.isabs(name) else name
    resolved = shutil.which(candidate, path=search)
    if resolved is None:
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), name)
    return os.path.abspath(resolved)
