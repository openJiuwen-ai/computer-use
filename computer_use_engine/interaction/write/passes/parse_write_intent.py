# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Parse the model operation without resolving or dispatching it."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from computer_use.interaction.write.model import WriteRequest
from computer_use.native.contracts import ApplicationBinding
from computer_use.pipeline import Contract, Pass
from computer_use.protocol.element_path import element_path

if TYPE_CHECKING:
    from computer_use.interaction.records import WriteArguments


def parse_request(value: WriteArguments, _: object) -> WriteRequest:
    """Parse operation options into a bounded write request.

    Validate launch, path-wait, and current-receiver forwarding combinations.
    Retain the observed lookup root or forwarding container while normalizing the
    operation target for the native lease.
    """
    intent, target, path, query_timeout_ms = value[:4]
    forward_current = value[4] if len(value) == 5 else False
    invalid_query_timeout_ms = (
        True
        # Exact types preserve scalar validation and identity contracts.
        # pylint: disable-next=unidiomatic-typecheck
        if type(query_timeout_ms) is not int or not 0 <= query_timeout_ms <= 10000 or path is None and query_timeout_ms
        else False
    )
    if invalid_query_timeout_ms:
        raise ValueError("A bounded query wait requires an element path")
    invalid_target = (
        True
        if (
            isinstance(target, ApplicationBinding) != (intent.kind == "launch")
            or intent.kind == "launch"
            and (intent.parameters or path is not None)
        )
        else False
    )
    if invalid_target:
        raise ValueError("Launch requires an observed installation and has no element path or parameters")
    query_root = None
    if path is not None:
        path = element_path(path)
        invalid_cast = (
            True
            if (
                cast("Binding", target).node is None
                or cast("Binding", target).capture is not None
                or cast("Binding", target).instance is None
                or cast("Binding", target).surface is None
            )
            else False
        )
        if invalid_cast:
            raise ValueError("A scoped path requires an observed accessible working-window root")
        query_root, target = target, replace(target, node=None)
    if forward_current:
        invalid_path = (
            True
            if (
                path is not None
                or cast("Binding", target).node is None
                or cast("Binding", target).capture is not None
                or intent.kind not in ("insert", "paste", "key", "focus")
            )
            else False
        )
        if invalid_path:
            raise ValueError("Current receiver forwarding requires a fixed container and keyboard intent")
        return WriteRequest(intent, replace(target, node=None), forward_scope=target)
    return WriteRequest(intent, target, query_root, path, query_timeout_ms)


stage = Pass.define(
    name="parse_write_intent",
    function=parse_request,
    contract=Contract(tuple, WriteRequest, failures=("invalid request",)),
)
if TYPE_CHECKING:
    from computer_use.native.contracts import Binding
