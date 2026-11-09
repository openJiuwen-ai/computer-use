# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Outcome verification contracts. Missing or unknown evidence degrades to UNKNOWN, never a faked PASS."""

from __future__ import annotations

import json
import re
from typing import Any

from .paths import Verdict, VerifySpec


def _json_field(payload: Any, dotted: str) -> Any:
    current = payload
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


async def verify(spec: VerifySpec | None, evidence: dict[str, Any]) -> tuple[Verdict, str]:
    if spec is None:
        return Verdict.UNKNOWN, "no verification contract; outcome not independently verified"
    try:
        if spec.kind == "http_status":
            status = int(evidence.get("status", 0))
            expected = int(spec.expect)
            return (
                (Verdict.PASS, f"http status {status}")
                if status == expected
                else (
                    Verdict.FAIL,
                    f"http status {status}, expected {expected}",
                )
            )
        if spec.kind == "exit_code":
            code = int(evidence.get("returncode", -1))
            expected = int(spec.expect)
            return (
                (Verdict.PASS, f"exit code {code}")
                if code == expected
                else (
                    Verdict.FAIL,
                    f"exit code {code}, expected {expected}",
                )
            )
        output = str(evidence.get("output", ""))
        if spec.kind == "output_contains":
            return (
                (Verdict.PASS, "expected text present in output")
                if str(spec.expect) in output
                else (Verdict.FAIL, f"output missing {spec.expect!r}")
            )
        if spec.kind == "output_regex":
            return (
                (Verdict.PASS, "pattern matched output")
                if re.search(str(spec.expect), output, re.DOTALL)
                else (Verdict.FAIL, f"output does not match {spec.expect!r}")
            )
        if spec.kind == "json_field":
            if not spec.field:
                return Verdict.UNKNOWN, "json_field verifier needs a field path"
            try:
                payload = json.loads(output)
            except ValueError:
                return Verdict.FAIL, "output is not valid JSON"
            actual = _json_field(payload, spec.field)
            if actual is None:
                return Verdict.FAIL, f"field {spec.field!r} missing"
            if str(actual) == str(spec.expect):
                return Verdict.PASS, f"field {spec.field} == {spec.expect!r}"
            if str(spec.expect) in str(actual):
                return Verdict.PASS, f"field {spec.field} contains {spec.expect!r}"
            return Verdict.FAIL, f"field {spec.field} is {actual!r}, expected {spec.expect!r}"
    except (TypeError, ValueError) as error:
        return Verdict.UNKNOWN, f"verification error: {error}"
    return Verdict.UNKNOWN, f"unknown verifier kind {spec.kind!r}"
