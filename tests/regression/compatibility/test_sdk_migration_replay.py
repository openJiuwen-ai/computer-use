# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Check the strict differential replay against a single source twice."""

import json
import subprocess
import sys
from pathlib import Path



def test_sdk_and_mcp_replay_rejects_early_reuse_and_native_request_changes(tmp_path, native_read):
    source = Path(__file__).resolve().parents[3]
    runner = source / "tests/manual/sdk_migration_replay.py"
    output = tmp_path / "replay"
    completed = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--baseline",
            str(source),
            "--candidate",
            str(source),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["sdk"] == report["mcp"] > 0
    assert set(report["negative_controls"]) == {"reorder", "skip", "duplicate", "early-reuse"}
