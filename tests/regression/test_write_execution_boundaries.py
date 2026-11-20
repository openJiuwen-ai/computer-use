# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Write composition contracts beyond the receipt gate unit suite."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from computer_use.errors import ComputerUseError
from computer_use.interaction.write.model import OpenedWrite, TargetBinding, WriteRequest
from computer_use.interaction.write.passes.parse_write_intent import parse_request
from computer_use.interaction.write.passes.pin_operation_target import stage as pin_target
from computer_use.interaction.write.pipeline import WritePipeline
from computer_use.interaction.write.progress import WriteRunState
from computer_use.native.contracts import Binding, HostPolicy, Intent
from computer_use.pipeline import Control

TARGET = Binding("session", "instance", "root", "surface", "editor")
INTENT = Intent("key", {"key": "super+s"})


def test_write_receipt_expansion_rejects_pair_iterable():
    calls = SimpleNamespace(validate_claim=lambda _: None, control=lambda remaining: Control(remaining=remaining))
    pipeline = WritePipeline(SimpleNamespace(calls=calls, policy=HostPolicy()))
    pipeline.main = SimpleNamespace(arun=AsyncMock(return_value=[("status", "dispatched")]))

    with pytest.raises(ComputerUseError) as caught:
        asyncio.run(pipeline.run_in_call(INTENT, TARGET, lambda: 1234))

    assert caught.value.code == "execution_failed"
    assert isinstance(caught.value.__cause__, TypeError)
    assert str(caught.value.__cause__) == "'list' object is not a mapping"


def test_write_parser_rejects_boolean_wait_before_reading_intent():
    with pytest.raises(ValueError, match="A bounded query wait requires an element path"):
        parse_request((None, None, [{"name": "editor"}], True), None)


def test_write_parser_unpacks_without_querying_iterator_length_hint():
    fields = iter((INTENT, TARGET, None, 0))
    hints = []

    class HintedFields:
        def __iter__(self):
            return self

        def __next__(self):
            return next(fields)

        def __length_hint__(self):
            hints.append(True)
            return 4

    class SlicedArguments:
        def __getitem__(self, key):
            assert key == slice(None, 4)
            return HintedFields()

        def __len__(self):
            return 4

    assert parse_request(SlicedArguments(), None).target is TARGET
    assert hints == []


def test_target_pin_keeps_initial_resolution_memory_when_lease_token_read_fails():
    request = WriteRequest(INTENT, TARGET)
    within = ("editor-owner",)
    value = TargetBinding(OpenedWrite(request, object()), TARGET, within)
    state = WriteRunState()
    native = SimpleNamespace(policy=HostPolicy())

    with pytest.raises(AttributeError, match="has no attribute 'token'"):
        pin_target(native=native, state=state).run(value, Control(remaining=lambda: 1234))

    assert state.memory.within is within
