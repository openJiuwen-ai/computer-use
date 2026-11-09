# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serialization releases its per-call traversal state without cyclic GC."""

import gc
import weakref
from types import MappingProxyType

import computer_use.json as wire_json
import pytest


@pytest.mark.parametrize("circular", [False, True])
def test_traversal_state_is_released_when_serialization_finishes(monkeypatch, circular):
    # Track only the per-call cycle detector. Do not disable automatic GC or
    # retain frames/tracebacks, which would themselves extend its lifetime.
    contexts = []

    def traversal_set():
        context = set()
        contexts.append(weakref.ref(context))
        return context

    monkeypatch.setattr(wire_json, "set", traversal_set, raising=False)
    value = {"child": [1, "text", None]}
    if circular:
        value["child"].append(value)
    gc.collect()

    def serialize():
        try:
            return wire_json.dumps(value)
        except ValueError as error:
            return str(error)

    result = serialize()
    assert result == ("circular value cannot be serialized" if circular else '{"child":[1,"text",null]}')
    assert len(contexts) == 1
    assert contexts[0]() is None


def test_numeric_keys_and_immutable_mappings_keep_wire_order():
    value = MappingProxyType(
        {"10": 10, "b": "B", "2": 2, "01": "leading", "4294967295": "large", "0": False, "4294967294": None}
    )
    assert wire_json.dumps(value) == (
        '{"0":false,"2":2,"10":10,"4294967294":null,"b":"B","01":"leading","4294967295":"large"}'
    )


def test_text_numbers_and_shared_children_keep_wire_values():
    child = ["中文\ud83d\ude42\ud800\n", -0.0, 1e-7, 1e20, float("nan"), float("inf"), 2**80]
    encoded = '["中文🙂\\ud800\\n",0,1e-7,100000000000000000000,null,null,1208925819614629174706176]'
    # A shared child is not a cycle; the active traversal set must be unwound.
    assert wire_json.dumps((child, child)) == "[" + encoded + "," + encoded + "]"


def test_exception_fields_and_unsupported_values_keep_errors():
    error = RuntimeError("not a wire field")
    error.code = "failure"
    error.detail = MappingProxyType({"message": "recorded"})
    assert wire_json.dumps(error) == '{"code":"failure","detail":{"message":"recorded"}}'
    with pytest.raises(TypeError, match="^Cannot serialize object$"):
        wire_json.dumps({"value": object()})
