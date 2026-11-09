# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Validate unified observation types, references and bounded query coverage."""

from __future__ import annotations

import math
import types
from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from functools import cache
from typing import Any, ForwardRef, Literal, Union, get_args, get_origin, get_type_hints

from . import model
from .model import (
    ElementValue,
    FieldPath,
    Geometry,
    NativeNode,
    NativeObservation,
    ReadIssue,
    ReadRef,
    Relation,
    TextInfo,
    TextSpan,
)


@cache
def record_types(cls: type) -> dict[str, Any]:
    """Resolve one public record's annotations, including recursive value aliases."""
    return get_type_hints(cls, vars(model), vars(model))


@cache
def resolved_type(expected: Any) -> Any:
    """Resolve aliases that remain nested inside recursive ExtraValue unions."""
    if isinstance(expected, str):
        return vars(model)[expected]
    if isinstance(expected, ForwardRef):
        return vars(model)[expected.__forward_arg__]
    return expected.__supertype__ if hasattr(expected, "__supertype__") else expected


@cache
def _type_shape(expected: Any) -> tuple[Any, Any, tuple[Any, ...]]:
    """Cache schema analysis shared by every instance of a record field."""
    expected = resolved_type(expected)
    return expected, get_origin(expected), get_args(expected)


@cache
def _record_fields(cls: type) -> tuple[tuple[str, Any], ...]:
    """Resolve field names and declared types once per immutable record class."""
    hints = record_types(cls)
    return tuple((item.name, hints[item.name]) for item in fields(cls))


@cache
def _union_choices(expected: Any, actual: type) -> tuple[Any, ...]:
    """Exclude impossible outer types before recursively validating a union.

    Matching branches still validate every nested value with the original
    nesting budget. Exact primitive matching keeps bool separate from int.
    """
    possible = []
    for choice in _type_shape(expected)[2]:
        resolved, origin, args = _type_shape(choice)
        if resolved in (str, bool, int, float, type(None)):
            matches = actual in ((int, float) if resolved is float else (resolved,))
        elif origin is Literal:
            matches = actual in tuple(type(item) for item in args)
        elif origin is tuple:
            matches = actual is tuple
        elif origin is Mapping:
            matches = issubclass(actual, Mapping)
        elif resolved is datetime or isinstance(resolved, type) and issubclass(resolved, Enum):
            matches = issubclass(actual, resolved)
        elif isinstance(resolved, type) and is_dataclass(resolved):
            matches = actual is resolved
        else:
            matches = True
        if matches:
            possible.append(choice)
    return tuple(possible)


def _type_error(path: FieldPath, message: str) -> ValueError:
    location = ".".join(str(part) for part in path) or "<observation>"
    return ValueError(f"observation: {location}: {message}")


_TypeCheck = Callable[[Any, FieldPath, int, list[tuple[FieldPath, Any]]], None]


@cache
def _type_check(expected: Any) -> _TypeCheck:
    """Compile schema dispatch once; each invocation still validates its value.

    Recursive ExtraValue aliases are resolved when their actual container is
    visited, so no observation data or validation result enters this cache.
    """
    declared = expected
    expected, origin, args = _type_shape(expected)
    if origin in (Union, types.UnionType):

        @cache
        def choices(actual: type) -> tuple[_TypeCheck, ...]:
            return tuple(_type_check(choice) for choice in _union_choices(declared, actual))

        def check(value: Any, path: FieldPath, depth: int, records: list) -> None:
            if depth > 96:
                raise _type_error(path, "record nesting budget exceeded")
            for choice in choices(type(value)):
                before = len(records)
                try:
                    choice(value, path, depth + 1, records)
                    return
                except ValueError:
                    del records[before:]
            raise _type_error(path, "value does not match its declared type")
    elif origin is Literal:
        allowed = tuple((type(choice), choice) for choice in args)

        def check(value: Any, path: FieldPath, depth: int, records: list) -> None:
            if depth > 96:
                raise _type_error(path, "record nesting budget exceeded")
            # Bool and int can compare equal while representing distinct literal kinds.
            # pylint: disable-next=unidiomatic-typecheck
            if not any(type(value) is kind and value == choice for kind, choice in allowed):
                raise _type_error(path, "unknown enum value")
    elif expected is type(None):

        def check(value: Any, path: FieldPath, depth: int, records: list) -> None:
            if depth > 96:
                raise _type_error(path, "record nesting budget exceeded")
            if value is not None:
                raise _type_error(path, "expected None")
    elif expected in (str, bool, int, float):
        allowed = (int, float) if expected is float else (expected,)

        def check(value: Any, path: FieldPath, depth: int, records: list) -> None:
            if depth > 96:
                raise _type_error(path, "record nesting budget exceeded")
            if type(value) not in allowed:
                raise _type_error(path, f"expected {expected.__name__}")
    elif expected is datetime:

        def check(value: Any, path: FieldPath, depth: int, records: list) -> None:
            if depth > 96:
                raise _type_error(path, "record nesting budget exceeded")
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise _type_error(path, "date must include a timezone")
    elif isinstance(expected, type) and issubclass(expected, Enum):

        def check(value: Any, path: FieldPath, depth: int, records: list) -> None:
            if depth > 96:
                raise _type_error(path, "record nesting budget exceeded")
            if not isinstance(value, expected):
                raise _type_error(path, "unknown semantic value")
    elif origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            # This lookup is deferred because tuple[ExtraValue, ...] is recursive.
            def check(value: Any, path: FieldPath, depth: int, records: list) -> None:
                if depth > 96:
                    raise _type_error(path, "record nesting budget exceeded")
                # The observation schema requires the normalized built-in tuple.
                # pylint: disable-next=unidiomatic-typecheck
                if type(value) is not tuple:
                    raise _type_error(path, "expected tuple")
                if value:
                    item_check = _type_check(args[0])
                    for index, item in enumerate(value):
                        item_check(item, (*path, index), depth + 1, records)
        else:
            checks = tuple(_type_check(item_type) for item_type in args)

            def check(value: Any, path: FieldPath, depth: int, records: list) -> None:
                if depth > 96:
                    raise _type_error(path, "record nesting budget exceeded")
                # The observation schema requires the normalized built-in tuple.
                # pylint: disable-next=unidiomatic-typecheck
                if type(value) is not tuple:
                    raise _type_error(path, "expected tuple")
                if len(value) != len(checks):
                    raise _type_error(path, "tuple length differs from its contract")
                for index, (item, item_check) in enumerate(zip(value, checks)):
                    item_check(item, (*path, index), depth + 1, records)
    elif origin is Mapping:
        key_check = _type_check(args[0])

        def check(value: Any, path: FieldPath, depth: int, records: list) -> None:
            if depth > 96:
                raise _type_error(path, "record nesting budget exceeded")
            if not isinstance(value, Mapping):
                raise _type_error(path, "expected mapping")
            if value:
                item_check = _type_check(args[1])
                for key, item in value.items():
                    key_check(key, (*path, "<key>"), depth + 1, records)
                    item_check(item, (*path, key), depth + 1, records)
    elif isinstance(expected, type) and is_dataclass(expected):
        checks = tuple((name, _type_check(item_type)) for name, item_type in _record_fields(expected))

        def check(value: Any, path: FieldPath, depth: int, records: list) -> None:
            if depth > 96:
                raise _type_error(path, "record nesting budget exceeded")
            # A declared schema record has one concrete field layout.
            # pylint: disable-next=unidiomatic-typecheck
            if type(value) is not expected:
                raise _type_error(path, f"expected {expected.__name__}")
            records.append((path, value))
            for name, item_check in checks:
                item_check(getattr(value, name), (*path, name), depth + 1, records)
    else:

        def check(value: Any, path: FieldPath, depth: int, records: list) -> None:
            raise _type_error(path, "unsupported observation schema type")

    return check


def _check_type(
    value: Any,
    expected: Any,
    path: FieldPath = (),
    depth: int = 0,
    records: list[tuple[FieldPath, Any]] | None = None,
) -> None:
    _type_check(expected)(value, path, depth, [] if records is None else records)


def _nonempty(value: str, path: FieldPath) -> None:
    if not value:
        raise _type_error(path, "identity or semantic key is empty")


def _nonnegative(value: int | float | None, path: FieldPath) -> None:
    if value is not None and value < 0:
        raise _type_error(path, "value must not be negative")


def _field_path(path: tuple[str | int, ...], location: FieldPath) -> None:
    # pylint: disable-next=unidiomatic-typecheck
    if any(type(part) not in (str, int) or part == "" or type(part) is int and part < 0 for part in path):
        raise _type_error(location, "invalid field path")


def _node_path(node: NativeNode, path: tuple[str | int, ...]) -> Any:
    value: Any = node
    for part in path:
        if value is None:
            return None
        if is_dataclass(value) and isinstance(part, str) and part in record_types(type(value)):
            value = getattr(value, part)
        elif isinstance(value, Mapping) and part in value:
            value = value[part]
        # pylint: disable-next=unidiomatic-typecheck
        elif isinstance(value, tuple) and type(part) is int and 0 <= part < len(value):
            value = value[part]
        else:
            raise ValueError("observation: content_field does not identify a node field")
    return value


def validate_query_observation(observation: NativeObservation) -> None:
    """Check the normalized graph before it enters shared presentation.

    Args:
        observation: Immutable unified facts from one bounded acquisition.

    Raises:
        ValueError: Field types, graph identities, coordinate spaces, or query
            ranges violate the unified contract. Unknown fields and partial
            observations remain valid when represented by None and ReadIssue.
    """
    # Record the semantic-check destinations during the type traversal. A
    # second recursive walk over every primitive/tuple in a large capture adds
    # no evidence and can dominate the actual native collection.
    records: list[tuple[FieldPath, Any]] = []
    _check_type(observation, NativeObservation, records=records)
    _nonempty(observation.session, ("session",))
    _nonempty(observation.id, ("id",))
    began, ended = observation.interval_ns
    if began < 0 or ended < began:
        raise _type_error(("interval_ns",), "invalid capture interval")
    if len(observation.nodes) > 65536:
        raise _type_error(("nodes",), "node budget exceeded")
    for identity in observation.nodes:
        _nonempty(identity, ("nodes", identity))
    if len(set(observation.roots)) != len(observation.roots):
        raise _type_error(("roots",), "duplicate root")
    for root in observation.roots:
        if root not in observation.nodes:
            raise _type_error(("roots",), "undeclared node reference")
    for identity, space in observation.coordinate_spaces.items():
        _nonempty(identity, ("coordinate_spaces", identity))
        if space.owner is not None and space.owner not in observation.nodes:
            raise _type_error(("coordinate_spaces", identity, "owner"), "undeclared node reference")
        if space.parent is not None and space.parent not in observation.coordinate_spaces:
            raise _type_error(("coordinate_spaces", identity, "parent"), "undeclared coordinate space")
        if space.to_parent is not None:
            if space.parent is None or not all(math.isfinite(number) for number in space.to_parent):
                raise _type_error(("coordinate_spaces", identity), "invalid coordinate transform")
        seen, current = set(), identity
        while current is not None:
            if current in seen:
                raise _type_error(("coordinate_spaces", identity), "coordinate transforms form a cycle")
            seen.add(current)
            current = observation.coordinate_spaces[current].parent

    for path, value in records:
        if isinstance(value, ElementValue) and value.node not in observation.nodes:
            raise _type_error(path, "undeclared element-valued reference")
        elif isinstance(value, Geometry):
            if value.space not in observation.coordinate_spaces:
                raise _type_error(path, "undeclared coordinate space")
            for name in ("x", "y", "width", "height"):
                component = getattr(value, name)
                if component is not None and not math.isfinite(component):
                    raise _type_error((*path, name), "geometry component must be finite")
            # Negative extents can be reported by a provider. Keep the evidence
            # when explicitly invalid, rather than manufacturing a valid box.
            if value.valid is True and any(
                component is not None and component < 0 for component in (value.width, value.height)
            ):
                raise _type_error(path, "valid geometry has negative dimensions")
        elif isinstance(value, Relation):
            _nonempty(value.kind, (*path, "kind"))
            if any(target not in observation.nodes for target in value.targets):
                raise _type_error(path, "undeclared relationship reference")
            _nonnegative(value.total, (*path, "total"))
            _nonnegative(value.offset, (*path, "offset"))
            if value.offset is not None and value.total is not None and value.offset > value.total:
                raise _type_error(path, "relationship page starts beyond its total")
            gaps, inspected = value.unavailable_positions, value.inspected_count
            if inspected is not None or gaps:
                start, total = value.offset, value.total
                # Exact integer types keep booleans and arbitrary page ranges out.
                # Count agreement bounds the subsequent range by existing data.
                exact_range = type(start) is int and type(total) is int and type(inspected) is int
                if not exact_range or not gaps or inspected != len(value.targets) + len(gaps):
                    raise _type_error(path, "invalid relationship slot progress")
                if (
                    start + inspected > total
                    or value.enumeration != "sampled"
                    or any(type(slot) is not int or not start <= slot < start + inspected for slot in gaps)
                ):
                    raise _type_error(path, "invalid relationship slot progress")
                if gaps != tuple(sorted(set(gaps))):
                    raise _type_error(path, "invalid relationship slot progress")
                missing = set(gaps)
                if (
                    value.positions is None or any(type(slot) is not int for slot in value.positions)
                    or value.positions != tuple(slot for slot in range(start, start + inspected) if slot not in missing)
                ):
                    raise _type_error(path, "relationship targets do not match the inspected slots")
            if value.positions is not None:
                if len(value.positions) != len(value.targets):
                    raise _type_error(path, "positions and targets have different lengths")
                if any(
                    position < 0 or value.total is not None and position >= value.total for position in value.positions
                ):
                    raise _type_error(path, "relationship position is outside its query range")
                if value.offset is not None and any(position < value.offset for position in value.positions):
                    raise _type_error(path, "relationship position precedes its page start")
                if (
                    value.enumeration == "enumerated"
                    and value.offset is not None
                    and value.positions != tuple(range(value.offset, value.offset + len(value.positions)))
                ):
                    raise _type_error(path, "enumerated relationship positions disagree with its page start")
            invalid_value = (
                True
                if (
                    value.enumeration == "enumerated"
                    and value.offset is not None
                    and value.total is not None
                    and value.offset + len(value.targets) > value.total
                )
                else False
            )
            if invalid_value:
                raise _type_error(path, "relationship page extends beyond its total")
            if value.total is not None and len(value.targets) > value.total:
                raise _type_error(path, "relationship contains more slots than its total")
        elif isinstance(value, ReadRef):
            _nonempty(value.token, (*path, "token"))
            _nonnegative(value.total, (*path, "total"))
            if value.delivered is not None:
                start, end = value.delivered
                invalid_start = (
                    True if start < 0 or end < start or value.total is not None and end > value.total else False
                )
                if invalid_start:
                    raise _type_error(path, "continuation range is outside its content")
        elif isinstance(value, ReadIssue):
            _field_path(value.path, (*path, "path"))
            if value.path and value.path[0] not in record_types(NativeObservation):
                raise _type_error(path, "issue starts outside the observation")
            if len(value.path) > 1 and value.path[0] == "nodes" and value.path[1] not in observation.nodes:
                raise _type_error(path, "issue refers to an undeclared node")
        elif isinstance(value, TextSpan):
            _nonnegative(value.start, (*path, "start"))
            _nonnegative(value.end, (*path, "end"))
            if value.start is not None and value.end is not None and value.end < value.start:
                raise _type_error(path, "text range is reversed")
        elif isinstance(value, TextInfo):
            _nonempty(value.source, (*path, "source"))
            _nonnegative(value.character_count, (*path, "character_count"))
            if value.content is not None and value.content_field is not None:
                raise _type_error(path, "content and content_field are mutually exclusive")
            if value.content_field is not None:
                _field_path(value.content_field, (*path, "content_field"))
                if not value.content_field or value.content_field[0] == "text":
                    raise _type_error(path, "content_field must reference another text-bearing node field")
                node = observation.nodes[path[1]]
                content = _node_path(node, value.content_field)
                if content is not None and not isinstance(content, str):
                    raise _type_error(path, "content_field must resolve to text")
            for node, _ in value.embedded or ():
                if node not in observation.nodes:
                    raise _type_error(path, "undeclared embedded element")
        elif isinstance(value, model.Action):
            _nonempty(value.kind, (*path, "kind"))
            if value.key is not None:
                _nonempty(value.key, (*path, "key"))
            if value.target is not None:
                _field_path(value.target, (*path, "target"))
        elif isinstance(value, model.Supplement):
            _nonempty(value.key, (*path, "key"))
        elif isinstance(value, model.Structure):
            for name in (
                "heading_level",
                "level",
                "index",
                "set_size",
                "selected_count",
                "row_count",
                "column_count",
                "row_index",
                "column_index",
                "row_span",
                "column_span",
            ):
                _nonnegative(getattr(value, name), (*path, name))
