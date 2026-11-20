# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Windows Agent acceptance outcome scored independently of the executor."""

import re


def reports_expected_answer(case: str, report: dict) -> bool:
    answer = str(report.get("answer"))
    expected = report["expected_answer"]
    if case != "calculator":
        return expected in answer
    # Match a complete result, including ordinary thousands grouping.
    formatted = f"{int(expected):,}"
    numbers = re.findall(r"\d+(?:[,，]\d+)*", answer)
    return any(number.replace("，", ",") in (expected, formatted) for number in numbers)


def automated_pass(case: str, report: dict) -> bool:
    return (
        bool(report.get("effect_ok"))
        and report.get("exit") == 0
        and bool(report.get("desktop_calls"))
        and reports_expected_answer(case, report)
        and not report.get("cleanup_error")
    )
