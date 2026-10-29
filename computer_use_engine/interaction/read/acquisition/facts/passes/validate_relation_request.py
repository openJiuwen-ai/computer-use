# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate relation request."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.facts.model import PURE, RelationReadRequest
from computer_use.pipeline import Contract, Pass
from computer_use.protocol.primitives import nonempty

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def validate_relation_request(request: RelationReadRequest, control: Control) -> RelationReadRequest:
    """Validate the element binding, native relation name, continuation cursor, and target-
    count limit.
    """
    target_value, property_name, cursor_value, limit_value = (
        request.target,
        request.property,
        request.cursor,
        request.limit,
    )
    if target_value.node is None or target_value.capture is not None:
        raise ValueError("Native relation reads require an observed node binding")
    nonempty(property_name, "native relation property")
    if cursor_value is not None:
        nonempty(cursor_value, "native relation cursor")
    # Exact types preserve scalar validation and identity contracts.
    # pylint: disable-next=unidiomatic-typecheck
    if type(limit_value) is not int or not 1 <= limit_value <= 2000:
        raise ValueError("Native relation page limit must be an integer in 1..2000")
    return request


stage = Pass.define(
    name="validate_relation_request",
    function=validate_relation_request,
    contract=Contract(RelationReadRequest, RelationReadRequest, PURE),
)
