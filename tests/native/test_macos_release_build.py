# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise the real native build without requiring desktop permissions."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "darwin", reason="Requires the macOS SDK")
def test_macos_release_driver_builds() -> None:
    """Catch Swift actor-isolation errors in the optimized installation build."""
    swift = shutil.which("swift")
    if swift is None:
        pytest.skip("Requires Swift Command Line Tools")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [swift, "build", "--package-path", str(root / "os_adapter/macos"), "-c", "release"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, result.stdout
