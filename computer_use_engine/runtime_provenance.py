# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Capture runtime provenance once, immediately before each driver launch.

These are startup filesystem facts, not a later inspection of whichever files
now occupy the same paths. No version query or hashing belongs in an operation.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

from computer_use._process import resolve_executable


def _source(package: Path) -> dict:
    deadline = time.monotonic() + 0.2

    def git(*args):
        return subprocess.run(
            [git_path, "-C", str(package), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=max(0.001, deadline - time.monotonic()),
        ).stdout

    result = {"status": "unavailable", "head": None, "dirty": None, "dirtyContentSha256": None}
    try:
        git_path = resolve_executable("git")
        root, head = git("rev-parse", "--show-toplevel", "HEAD").decode().splitlines()
        module = package / "__init__.py"
        covered = git("ls-files", "-z", "--cached", "--others", "--exclude-standard", "--full-name", "--", str(module))
        if os.fsencode(module.relative_to(root).as_posix()) not in covered.split(b"\0"):
            # An installed wheel inside this checkout's ignored .venv does not
            # run this checkout's Python source just because Git finds a parent.
            raise ValueError("imported SDK is not covered by this checkout")
        result.update(root=root, head=head)
        status = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        result["dirty"] = bool(status)
        digest = hashlib.sha256(status)
        entries = iter(status.split(b"\0"))
        for entry in entries:
            if not entry:
                continue
            code, relative = entry[:2], entry[3:]
            # Porcelain -z emits the new path first, followed by the old path
            # for rename/copy entries. Status bytes already retain both names.
            if b"R" in code or b"C" in code:
                next(entries)
            if time.monotonic() >= deadline:
                raise TimeoutError("source provenance budget exhausted")
            path = Path(root) / os.fsdecode(relative)
            if path.is_symlink():
                content = hashlib.sha256(os.fsencode(os.readlink(path))).digest()
            elif not path.exists():
                content = b"deleted"
            else:
                if not path.is_file():
                    # A dirty submodule cannot be represented by the parent
                    # status alone. Never call a partial snapshot complete.
                    raise ValueError("non-file dirty source")
                file_hash = hashlib.sha256()
                with path.open("rb") as source:
                    before = os.fstat(source.fileno())
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        file_hash.update(block)
                        if time.monotonic() >= deadline:
                            raise TimeoutError("source provenance budget exhausted")
                    after = os.fstat(source.fileno())
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError("source changed during fingerprint")
                content = file_hash.digest()
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(content)
        result.update(status="complete", dirtyContentSha256=digest.hexdigest() if status else None)
    except (OSError, subprocess.SubprocessError, ValueError, StopIteration) as error:
        result["error"] = type(error).__name__
    return result


def startup_provenance(command: str) -> dict:
    """Record imported SDK location and pre-spawn executable bytes once.

    Failure to acquire diagnostic evidence must not prevent the driver launch.
    The source fingerprint covers Git status and actual dirty working-tree
    content, including nonignored untracked files, independently of mtimes.
    """
    started = time.monotonic()
    imported = sys.modules.get("computer_use")
    sdk = Path(getattr(imported, "__file__", __file__)).resolve()
    result = {"sdkModulePath": str(sdk), "pythonExecutable": sys.executable, "source": _source(sdk.parent)}
    driver = {"path": command, "captureStage": "beforeSpawn", "sha256": None}
    try:
        path = Path(resolve_executable(command)).resolve()
        with path.open("rb") as binary:
            before = os.fstat(binary.fileno())
            digest = hashlib.file_digest(binary, "sha256").hexdigest()
            after = os.fstat(binary.fileno())
        driver.update(path=str(path), sha256=digest, size=after.st_size, mtimeNs=after.st_mtime_ns)
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            driver.update(sha256=None, error="changedDuringFingerprint")
    except (OSError, ValueError) as error:
        driver["error"] = type(error).__name__
    result.update(driver=driver, durationMs=(time.monotonic() - started) * 1000)
    return result
