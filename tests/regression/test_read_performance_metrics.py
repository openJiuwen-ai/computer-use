# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pathlib import Path
from runpy import run_path

import pytest


@pytest.fixture(scope="module")
def validate_sample():
    script = Path(__file__).resolve().parents[1] / "fixtures" / "read_ax_metrics.py"
    return run_path(str(script))["validate_sample"]


def sample(*, private_rows=True):
    initial = [0x4355415842454E43, 731, 0, *([0] * 26)]
    final = initial.copy()
    final[3:5] = [4, 700]  # Public attribute value getter.
    final[25:29] = [2, 40, 3, 30]  # Public PID and timeout configuration.
    if private_rows:
        initial.extend((0, 0, 0, 0))
        final.extend((2, 60, 3, 95))  # Window lookup and actual PID.
    return {"before": initial, "after": final, "readNs": 1000, "trees": [{"nodes": 579}]}


def test_read_ratio_includes_window_id_and_keeps_public_measurement(validate_sample):
    result = validate_sample(sample())

    assert result["leafAxNs"] == 760
    assert result["externalRatio"] == 0.24
    assert result["publicLeafAxNs"] == 700
    assert result["publicExternalRatio"] == 0.30
    assert result["axMethods"]["actualAccessibilityPid"] == {"calls": 3, "ns": 95}


def test_read_ratio_requires_matching_probe_layout(validate_sample):
    with pytest.raises(RuntimeError, match="ledger layout mismatch"):
        validate_sample(sample(private_rows=False))
