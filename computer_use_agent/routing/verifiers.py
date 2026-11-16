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
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _json_actual(spec: VerifySpec, evidence: dict[str, Any], output: str) -> Any:
    """Resolve the field: the `receipts` prefix addresses execution evidence (structured action
    receipts); every other path resolves against the JSON payload of the output text, so business
    fields can never be shadowed by executor bookkeeping keys (status/returncode/output)."""
    if spec.field and spec.field.split(".", 1)[0] == "receipts":
        return _json_field(evidence, spec.field)
    try:
        payload = json.loads(output)
    except ValueError:
        return None
    return _json_field(payload, spec.field)


async def verify(spec: VerifySpec | None, evidence: dict[str, Any]) -> tuple[Verdict, str]:
    if spec is None:
        return Verdict.UNKNOWN, "no verification contract; outcome not independently verified"
    if spec.kind == "gui_observe":
        # The router evaluates gui_observe against a live session; static evidence cannot confirm it.
        return Verdict.UNKNOWN, "gui_observe needs the router's live observation runner"
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
            actual = _json_actual(spec, evidence, output)
            if actual is None:
                return Verdict.FAIL, f"field {spec.field!r} missing"
            if str(spec.expect) in str(actual):  # equality is subsumed by the containment check
                return Verdict.PASS, f"field {spec.field} contains {spec.expect!r}"
            return Verdict.FAIL, f"field {spec.field} is {actual!r}, expected {spec.expect!r}"
    except (TypeError, ValueError) as error:
        return Verdict.UNKNOWN, f"verification error: {error}"
    return Verdict.UNKNOWN, f"unknown verifier kind {spec.kind!r}"
