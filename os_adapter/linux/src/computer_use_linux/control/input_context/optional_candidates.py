# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Local query failures do not invalidate independent object contracts."""

from computer_use_linux.contracts.protocol import Failure


def can_keep_object_contracts(result, error, request_effect, unit):
    if request_effect != "none":
        return False
    if isinstance(error, Failure) and (
        error.effect != "none"
        or error.code
        not in (
            "input_unavailable",
            "unsupported_operation",
            "observation_incomplete",
            "ambiguous_target",
        )
    ):
        return False
    return any(
        primitive["unit"] == unit and primitive["channel"] == "object" and primitive["supported"] == "yes"
        for primitive in result["primitives"]
    )
