# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from .windows_fixtures.agent_acceptance import automated_pass


@pytest.mark.parametrize(
    ("answer", "effect_ok", "passed"),
    [
        ("结果是 1,200。", True, True),
        ("结果是 12,000。", True, False),
        ("结果是 1,200。", False, False),
    ],
)
def test_calculator_answer_matches_complete_numeric_value(answer, effect_ok, passed):
    report = {"effect_ok": effect_ok, "exit": 0, "desktop_calls": 3, "expected_answer": "1200", "answer": answer}
    assert automated_pass("calculator", report) is passed
