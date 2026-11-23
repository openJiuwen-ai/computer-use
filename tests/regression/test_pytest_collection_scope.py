# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep the hosted UT entry point and the explicit regression entry point separate."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("path", "collect_unit_tests", "collect_regressions"),
    [
        ("tests", True, False),
        ("tests/regression", False, True),
    ],
)
def test_pytest_collection_scope(path, collect_unit_tests, collect_regressions):
    repo = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-o", "addopts=", "-p", "no:cacheprovider", "--collect-only", "-q", path],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    node_ids = result.stdout.splitlines()
    assert any(node.startswith("tests/unit_tests/") for node in node_ids) == collect_unit_tests
    assert any(node.startswith("tests/regression/") for node in node_ids) == collect_regressions
