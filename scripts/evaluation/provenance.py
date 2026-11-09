# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pin checkout content and the actual loaded SDK/driver for a local episode."""

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

import computer_use
from computer_use.paths import native_driver
from computer_use.protocol.session import NATIVE_PROTOCOL_VERSION


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_snapshot(root: Path) -> dict:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root)

    names = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0")
    # Ignore user research/downloads; pin every build/runtime/test source, even
    # untracked additions. Full tracked dirty diff is identified separately.
    roots = ("computer_use_engine/", "computer_use_agent/", "interface_adapter/", "os_adapter/", "scripts/", "tests/")
    files = {
        name: digest((root / name).read_bytes()) if (root / name).is_file() else None
        for name in sorted(set(names))
        if name.startswith(roots) or name in ("pyproject.toml", "setup.py", "MANIFEST.in")
    }
    return {
        "head": git("rev-parse", "HEAD").decode().strip(),
        "dirty_diff_sha256": digest(git("diff", "HEAD", "--binary")),
        "source_sha256": digest(json.dumps(files, sort_keys=True).encode()),
        "files": files,
    }


def runtime_snapshot(root: Path) -> dict:
    driver = native_driver()
    path = Path(driver["command"])
    return {
        "source": source_snapshot(root),
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "sdk_module": computer_use.__file__,
        "sdk_version": computer_use.__version__,
        "driver": {
            **driver,
            "sha256": digest(path.read_bytes()) if path.is_file() else None,
            "build_source_verified": False,
        },
        "expected_protocol": NATIVE_PROTOCOL_VERSION,
    }
