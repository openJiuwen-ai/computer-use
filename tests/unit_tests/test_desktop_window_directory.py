# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from copy import deepcopy

import pytest
from computer_use.protocol.desktop import decode_desktop


def catalogue():
    return {
        "canvas": {"width": 100, "height": 100},
        "frontmost": None,
        "displays": [{"name": "display", "rect": {"x": 0, "y": 0, "width": 100, "height": 100}, "captured": True}],
        "windows": [
            {
                "app": "Provider",
                "title": "",
                "selector": "instance:provider",
                "rect": {"x": 10, "y": 10, "width": 60, "height": 50},
                "window": {
                    "window": "native-reference",
                    "instance": "window-instance",
                    "surface": "native-scope",
                    "node": None,
                    "title": "",
                    "kind": "window",
                    "parent": None,
                    "parentState": "unknown",
                    "modal": None,
                    "nativeWindow": True,
                    "accessibility": False,
                },
            }
        ],
        "omittedWindows": 0,
    }


def test_desktop_retains_exact_native_window_with_unknown_ax_and_parent():
    value = catalogue()
    assert decode_desktop(value) == value
    # Other platform catalogues can remain useful without an actionable native
    # identity; neither title nor geometry is promoted to a binding.
    old = deepcopy(value)
    del old["windows"][0]["window"]
    assert decode_desktop(old) == old


def test_desktop_binding_requires_application_scope():
    value = catalogue()
    del value["windows"][0]["selector"]
    with pytest.raises(ValueError, match="application selector"):
        decode_desktop(value)


def test_desktop_rejects_partial_window_identity():
    value = catalogue()
    del value["windows"][0]["window"]["surface"]
    with pytest.raises(ValueError):
        decode_desktop(value)


@pytest.mark.parametrize("field,invalid", [("parentState", "known"), ("accessibility", True)])
def test_desktop_and_application_directory_enforce_same_fact_contract(field, invalid):
    value = catalogue()
    value["windows"][0]["window"][field] = invalid
    with pytest.raises(ValueError, match="inconsistent"):
        decode_desktop(value)
