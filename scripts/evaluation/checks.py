# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Independent exact-byte artifact evidence; never an execution-side check."""

from pathlib import Path
from .ledger import Check
from .provenance import digest


def evaluate_file(path: Path, expected: bytes, control: Path, control_hash: str) -> Check:
    """Read full bytes independently of UI/receipts; missing evidence is unknown."""
    try:
        actual, untouched = path.read_bytes(), control.read_bytes()
    except OSError as error:
        return Check("UNKNOWN", str(path), {"error_type": type(error).__name__})
    return Check(
        "TRUE" if actual == expected and digest(untouched) == control_hash else "FALSE",
        str(path),
        {
            "expected_sha256": digest(expected),
            "actual_sha256": digest(actual),
            "control_unchanged": digest(untouched) == control_hash,
            "bytes": len(actual),
        },
    )
