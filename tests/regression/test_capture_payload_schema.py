# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Capture errors expose the ordered schema issue records."""

import pytest

from computer_use.protocol.captures import decode_capture
from computer_use.protocol.schema import SchemaError


def test_capture_enum_error_retains_the_original_field_values_list():
    def issues():
        with pytest.raises(SchemaError) as caught:
            decode_capture({"kind": 3, "windowState": {"sampledAt": 0, "onScreen": 1, "ownerForeground": 1}}, "window")
        return {tuple(issue["path"]): issue["values"] for issue in caught.value.issues if "values" in issue}

    first, second = issues(), issues()
    assert first[("kind",)] == ["window", "overview", "desktop"]
    assert first[("windowState", "onScreen")] == [True, False]
    assert first[("windowState", "ownerForeground")] == [True, False]
    assert first[("windowState", "onScreen")] is not first[("windowState", "ownerForeground")]
    assert all(first[path] is second[path] for path in first)
