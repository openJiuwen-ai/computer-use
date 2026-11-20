# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serialization releases its per-call traversal state without cyclic GC."""

import gc
import json
import weakref
from types import MappingProxyType

import computer_use.json as wire_json
import pytest
from computer_use.interaction.read.presentation.sizing import DocumentSize


@pytest.mark.parametrize("circular", [False, True])
def test_traversal_state_is_released_when_serialization_finishes(circular):
    class Child(list):
        pass

    gc.collect()

    def serialize():
        child = Child([1, "text", None])
        reference = weakref.ref(child)
        value = {"child": child}
        if circular:
            child.append(value)
        try:
            return wire_json.dumps(value), reference
        except ValueError as error:
            return str(error), reference
        finally:
            child.clear()

    result, reference = serialize()
    assert result == ("circular value cannot be serialized" if circular else '{"child":[1,"text",null]}')
    assert reference() is None


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


@pytest.mark.parametrize("source", ("[NaN]", b"[Infinity]", bytearray(b"[-Infinity]")))
def test_loads_preserves_standard_json_errors(source):
    text = source if isinstance(source, str) else source.decode()
    with pytest.raises(json.JSONDecodeError) as error:
        wire_json.loads(source)
    assert (error.value.msg, error.value.doc, error.value.pos) == (f"Invalid JSON constant: {text[1:-1]}", text, 0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("ASCII", b"ASCII"), ("中文🙂", "中文🙂".encode()), ("中\ud83d\ude42\ud800\n\udc00", "中🙂�\n�".encode())],
)
def test_utf8_keeps_unicode_and_utf16_surrogate_semantics(text, expected):
    assert wire_json.utf8(text) == expected


def test_utf8_preserves_custom_string_encoding():
    class Custom(str):
        def encode(self, encoding, errors="strict"):
            assert (encoding, errors) == ("utf-16-le", "surrogatepass")
            return "custom".encode(encoding, errors)

    assert wire_json.utf8(Custom("ordinary")) == b"custom"


def test_document_size_matches_serialized_byte_count_for_mixed_unicode():
    repeated = MappingProxyType({"中\u0000": ("'\r\n", "🙂", "\ud83d\ude42", "孤\ud800\udc00\udc00", 3.25)})
    record = {"2": [repeated, repeated], "1": "different", "ascii": ("x", "\u007f")}
    measure = DocumentSize()
    assert measure.data(record) == len(wire_json.utf8(wire_json.dumps(record)))
    assert measure.data(repeated) == len(wire_json.utf8(wire_json.dumps(repeated)))


def test_document_size_keeps_unbounded_totals_for_shared_sources():
    value, size = [None], len("[null]")
    for _ in range(100):
        value, size = (value, value), 2 * size + 3
    assert DocumentSize().data(value) == size


def test_json_string_preserves_non_ascii_controls_and_surrogate_spelling():
    class Custom(str):
        def isascii(self):
            return True

    raw = "\x00\x7f\x85\x9f\u2028\u2029\ud83d\ude42\ud800"
    expected = '"\\u0000\x7f\x85\x9f\u2028\u2029🙂\\ud800"'
    assert wire_json.dumps(raw) == expected
    assert wire_json.dumps(Custom(raw)) == expected
