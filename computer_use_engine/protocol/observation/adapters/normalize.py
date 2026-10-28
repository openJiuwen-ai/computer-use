# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterator
from dataclasses import fields, is_dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from computer_use.protocol.primitives import required_item

from ..model import (
    Action,
    ControlValue,
    CoordinateSpace,
    ElementValue,
    Geometry,
    NativeNode,
    NativeObservation,
    NodeId,
    NumericValue,
    ReadIssue,
    ReadRef,
    Relation,
    RelationKind,
    Role,
    ScrollAxis,
    Shortcut,
    SpaceId,
    SpecialValue,
    States,
    Structure,
    Supplement,
    TextInfo,
    TextSpan,
)
from ..validation import validate_query_observation
from . import macos_text, windows_text
from .records import DriverObservationReply, DriverReadMetadata
from .schema import (
    ACTION_KINDS,
    DISCARD_FIELDS,
    NUMBER_FIELDS,
    RELATIONS,
    ROLE_DETAILS,
    ROLE_FIELDS,
    STATE_FIELDS,
    STRUCTURE_FIELDS,
    SUPPLEMENTS,
    TEXT_FIELDS,
    role_name,
)
from .wire import DriverWire, query_facts
from .diagnostics import catalogue_fields, wrapped_status_code

_STATUS = {
    "not_read",
    "absent",
    "unsupported",
    "partial",
    "error",
    "timeout",
    "unavailable",
    "redacted",
    "stale",
    "conflict",
}
_ABSENT = object()
_AXSELECTED_TEXT_FIELDS = (
    "AXSelectedText",
    "AXSelectedTextRange",
    "AXSelectedTextRanges",
    "AXNumberOfCharacters",
)


def equal(left: Any, right: Any) -> bool:
    """Compare semantic values with stable NaN and container semantics."""
    matches_left = (
        # Exact types preserve scalar validation and identity contracts.
        # pylint: disable-next=unidiomatic-typecheck
        True if type(left) is float and type(right) is float and math.isnan(left) and math.isnan(right) else False
    )
    if matches_left:
        return True
    if type(left) is not type(right):
        return False
    return left == right


def enum_value(values: Mapping[Any, Any], value: Any) -> Any:
    """Treat malformed compound enum values as unknown provider declarations."""
    return values.get(value) if type(value) in (str, int, bool) else None


class NodeBuilder:
    """Accumulate one node's semantic fields and precisely located read gaps."""

    def __init__(self, owner: Adapter, identity: str, row: Mapping[str, Any]) -> None:
        self.owner, self.identity, self.row = owner, identity, row
        self.values = row["values"]
        self.data: dict[str, Any] = {}
        self.groups: dict[str, dict[str, Any]] = {}
        self.supplements: list[Supplement] = []
        self.actions: list[Action] = []
        self.relations: dict[str, Relation] = {}
        self.geometry: dict[str, Geometry] = {}
        self.paths: dict[str, tuple[str | int, ...]] = {}
        self.conflicts: set[tuple[str, ...]] = set()
        self.role: str | None = None
        self.catalogues = catalogue_fields(owner.wire.nodes[identity]["properties"] or (), row)
        self.methods = {}
        for index, prop in enumerate(owner.wire.nodes[identity]["properties"]):
            if prop.get("arguments"):
                method = f"{prop['key'][0]}::{prop['key'][1]}#{index}"
                self.methods[method] = (
                    tuple(prop["key"]),
                    tuple(owner.wire.value(item) for item in prop["arguments"]),
                )
        self.method_reads = {
            f"{prop['key'][0]}::{prop['key'][1]}#{index}": owner.wire.read_info(prop.get("read"))
            for index, prop in enumerate(owner.wire.nodes[identity]["properties"])
            if prop.get("arguments")
        }

    @property
    def backend(self) -> str:
        return self.owner.wire.platform

    def issue(
        self,
        path: tuple[str | int, ...],
        state: str,
        detail: str | None = None,
        continuation: ReadRef | None = None,
        alternatives: tuple[Any, ...] = (),
    ) -> None:
        """Attach a gap to the field whose interpretation it limits."""
        state = {"truncated": "partial", "notProbed": "not_read", "complete": "partial"}.get(state, state)
        if state not in _STATUS:
            detail = detail or state
            state = "error"
        self.owner.issues.append(ReadIssue(("nodes", self.identity, *path), state, detail, continuation, alternatives))

    def extra(self, value: Any, path: tuple[str | int, ...]) -> Any:
        """Convert useful residual values without retaining native runtime objects."""
        if value is None:
            self.issue(path, "absent")
            return _ABSENT
        if type(value) in (str, bool, int, float):
            return value
        if isinstance(value, (datetime, ElementValue, SpecialValue)):
            return value
        if is_dataclass(value):
            return {
                field.name: self.extra(getattr(value, field.name), (*path, field.name))
                for field in fields(value)
                if getattr(value, field.name) is not None
            }
        if isinstance(value, list) or isinstance(value, tuple):
            items = []
            for index, item in enumerate(value):
                converted = self.extra(item, (*path, index))
                if converted is _ABSENT:
                    # Preserve positions with a public description of the missing slot.
                    items.append({"unavailable": True})
                else:
                    items.append(converted)
            return tuple(items)
        if isinstance(value, dict):
            if set(value) == {"nativeType", "node"}:
                identity = value["node"]
                if self.owner.wire.nodes[identity]["kind"] == "element":
                    return ElementValue(NodeId(identity))
                self.issue(path, "unavailable", "Native resource has no supported public observation query.")
                return _ABSENT
            if value.get("nativeType") == "CFDate" and type(value.get("absoluteTime")) in (int, float):
                try:
                    return datetime(2001, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=value["absoluteTime"])
                except (ValueError, OverflowError):
                    self.issue(path, "error", "Date cannot be represented by a timezone-aware datetime.")
                    return {"date_seconds_since_2001": value["absoluteTime"]}
            if value.get("value") in ("NaN", "+Infinity", "-Infinity") and "nativeType" in value:
                return {"NaN": math.nan, "+Infinity": math.inf, "-Infinity": -math.inf}[value["value"]]
            if set(value) == {"issue"}:
                issue = value["issue"]
                self.issue(path, issue.get("reason", "unavailable"), issue.get("detail"))
                return _ABSENT
            if "base64" in value and "nativeType" in value:
                self.issue(path, "unavailable", "Binary provider content has no registered semantic decoder.")
                return _ABSENT
            result = {}
            for key, item in value.items():
                if key == "nativeType":
                    continue
                converted = self.extra(item, (*path, str(key)))
                if converted is not _ABSENT:
                    result[str(key)] = converted
            return result
        self.issue(path, "error", "Unrecognized provider value.")
        return _ABSENT

    def supplement(self, name: str, value: Any, *, semantic_key: str | None = None, diagnostic: bool = False) -> None:
        """Retain meaningful unnormalized facts in a named public supplement."""
        key = semantic_key or f"provider.{self.backend}.{name}"
        path = ("supplements", len(self.supplements), "value")
        converted = self.extra(value, path)
        self.paths[name] = path
        if converted is not _ABSENT:
            if diagnostic or name in self.catalogues:
                self.owner.diagnostic_fields.setdefault(NodeId(self.identity), []).append(path[:-1])
            self.supplements.append(Supplement(key, converted))

    def put(self, name: str, path: tuple[str, ...], value: Any, expected: Any = None) -> None:
        """Set a typed field while preserving conflicting provider declarations."""
        self.paths[name] = path
        self.owner.field_sources[(NodeId(self.identity), path)] = name
        if value is None:
            self.issue(path, "absent")
            return
        if expected is not None and not expected(value):
            self.issue(path, "error", "Provider value does not satisfy the public field type.")
            self.supplement(name, value)
            return
        target = self.data if len(path) == 1 else self.groups.setdefault(path[0], {})
        key = path[-1]
        if key in target and not equal(target[key], value):
            old = target.pop(key)
            self.conflicts.add(path)
            self.issue(
                path,
                "conflict",
                "Provider declarations disagree.",
                alternatives=(self.extra(old, path), self.extra(value, path)),
            )
            return
        # A prior conflict remains unresolved even if a later property repeats one alternative.
        if path in self.conflicts:
            return
        target[key] = value

    def number(self, name: str, path: tuple[str, ...], raw: Any) -> None:
        value = self.extra(raw, path)
        if value is not _ABSENT:
            self.put(name, path, value, lambda item: type(item) in (int, float))

    def text_info(self) -> dict[str, Any]:
        """Create a text-space identity without asserting document completeness."""
        return self.groups.setdefault(
            "text",
            {
                "source": f"{self.owner.capture_id}:{self.identity}:text",
                "unit": "provider" if self.backend == "linux" else "utf16_code_unit",
            },
        )

    def span(self, raw: Any, path: tuple[str | int, ...]) -> TextSpan:
        """Normalize only proven positions; preserve unlocated text and geometry."""
        if not isinstance(raw, dict):
            self.issue(path, "error", "Text range is not a record.")
            return TextSpan()
        values = raw.get("values", raw)
        if not isinstance(values, dict):
            self.issue(path, "error", "Text range values are unavailable.")
            return TextSpan()
        positions = values.get("range", values)
        result: dict[str, Any] = {}
        if isinstance(positions, dict):
            start = positions.get("start", positions.get("startOffset", positions.get("location")))
            end = positions.get("end", positions.get("endOffset"))
            # pylint: disable-next=unidiomatic-typecheck
            if end is None and type(start) is int and type(positions.get("length")) is int:
                end = start + positions["length"]
            # pylint: disable-next=unidiomatic-typecheck
            if type(start) is int and type(end) is int and 0 <= start <= end:
                result.update(start=start, end=end)
            elif start is not None or end is not None:
                self.issue(path, "error", "Text range endpoints are invalid or incomplete.")
        for key, target in (("text", "text"), ("degenerate", "collapsed"), ("collapsed", "collapsed")):
            if key in values:
                expected = str if key == "text" else bool
                # pylint: disable-next=unidiomatic-typecheck
                if type(values[key]) is expected:
                    result[target] = values[key]
        if "start" in result and "collapsed" not in result:
            result["collapsed"] = result["start"] == result["end"]
        if "readOnly" in values:
            item = values["readOnly"]
            # pylint: disable-next=unidiomatic-typecheck
            if type(item) is bool or item == "mixed":
                result["attributes"] = {"read_only": SpecialValue.MIXED if item == "mixed" else item}
        bounds = values.get("bounds", values.get("rectangles"))
        if isinstance(bounds, list):
            result["geometry"] = tuple(
                self.bounds(item, (*path, "geometry", index))
                for index, item in enumerate(bounds)
                if isinstance(item, dict)
            )
        statuses = raw.get("fieldStatus", {})
        if not isinstance(statuses, dict):
            self.issue(path, "error", "Text range status is not a record.")
            self.supplement("text_range.failed_status", statuses)
            statuses = {}
        for key, status in statuses.items():
            if not isinstance(status, dict):
                self.issue((*path, key), "error", "Text range field status is not a record.")
                self.supplement("text_range.failed_status." + key, status)
                continue
            self.issue(
                (*path, {"degenerate": "collapsed", "readOnly": "attributes"}.get(key, key)),
                status.get("state", "error"),
                status.get("detail"),
            )
            if key == "position":
                result.pop("start", None)
                result.pop("end", None)
        return TextSpan(**result)

    def bounds(self, value: Mapping[str, Any], path: tuple[str | int, ...], *, kind: str = "bounds") -> Geometry:
        """Keep reported units and partial bounds without inventing screen transforms."""
        space = SpaceId(f"{self.backend}:screen")
        if self.backend == "macos":
            descriptor = CoordinateSpace("point", "primary_screen_top_left", "down")
        elif self.backend == "windows":
            descriptor = CoordinateSpace("physical_pixel", "primary_screen_top_left", "down")
        else:
            descriptor = CoordinateSpace("provider", "desktop_top_left", "down")
        self.owner.spaces[space] = descriptor
        result = {}
        for key in ("x", "y", "width", "height"):
            item = value.get(key)
            if type(item) in (int, float) and math.isfinite(item):
                result[key] = item
            elif item is not None:
                self.issue((*path, key), "error", "Geometry component is not finite.")
        # pylint: disable-next=unidiomatic-typecheck
        if type(value.get("valid")) is bool:
            result["valid"] = value["valid"]
        elif all(key in result for key in ("x", "y", "width", "height")):
            result["valid"] = required_item(result, "width") >= 0 and required_item(result, "height") >= 0
        residual = {key: item for key, item in value.items() if key not in result}
        if residual:
            self.supplement(
                ".".join(map(str, path)) + ".components", residual, semantic_key="geometry.provider_components"
            )
        return Geometry(space, **result)

    def status(self, name: str, status: Mapping[str, Any]) -> None:
        """Attach field status and usable continuation to its normalized location."""
        path = self.paths.get(name, ("supplements", f"provider.{self.backend}.{name}"))
        continuation = None
        token = status.get("textRef")
        if isinstance(token, str) and token:
            value = self.values.get(name)
            delivered = len(value.encode("utf-16le", "surrogatepass")) // 2 if isinstance(value, str) else None
            total = status.get("totalUnits")
            matches_total = (
                True
                # pylint: disable-next=unidiomatic-typecheck
                if total is None or type(total) is int and total >= 0 and (delivered is None or delivered <= total)
                else False
            )
            if matches_total:
                continuation = ReadRef(
                    token,
                    "text",
                    "captured",
                    "utf16_code_unit",
                    total,
                    (0, delivered) if delivered is not None else None,
                )
            else:
                self.issue(path, "error", "Text continuation coverage is invalid.")
        elif token is not None:
            self.issue(path, "error", "Text continuation token is invalid.")
        self.issue(path, status.get("state", "error"), status.get("detail"), continuation)
        for key in ("rawResult", "wrappedNativeValue"):
            if key in status:
                self.supplement(
                    name + ".failed_payload",
                    status[key],
                    semantic_key=f"provider.{self.backend}.{name}.failed_payload",
                    diagnostic=(
                        key == "wrappedNativeValue" and wrapped_status_code(status, has_value=name in self.values)
                    ),
                )

    def build(self) -> NativeNode:
        """Publish one immutable element after its declarations have been mapped."""
        for group, cls in (
            ("states", States),
            ("control_value", ControlValue),
            ("numeric_value", NumericValue),
            ("structure", Structure),
        ):
            if group in self.groups:
                self.data[group] = cls(**self.groups[group])
        if "text" in self.groups:
            self.data["text"] = TextInfo(**self.text_info())
        if self.actions or self.row["actions"]["state"] == "complete":
            self.data["actions"] = tuple(self.actions)
        return NativeNode(
            **self.data, geometry=self.geometry, relations=self.relations, supplements=tuple(self.supplements)
        )


class Adapter:
    """Convert a single transient driver reply to the shared fact model."""

    def __init__(self, wire: DriverWire, facts: Mapping[str, Any]) -> None:
        self.wire, self.facts = wire, facts
        self.capture_id = wire.details["observation_id"]
        self.issues: list[ReadIssue] = []
        self.spaces: dict[SpaceId, CoordinateSpace] = {}
        self.field_sources: dict[tuple[NodeId, tuple[str | int, ...]], str] = {}
        self.relation_fields: dict[tuple[NodeId, str], str] = {}
        self.diagnostic_fields: dict[NodeId, list[tuple[str | int, ...]]] = {}
        self.text_read_fields: dict[NodeId, tuple[str | int, ...]] = {}

    def normalize(self) -> DriverObservationReply:
        """Map all acquired fields without retaining the transient query record."""
        builders = {row["node"]: NodeBuilder(self, row["node"], row) for row in self.facts["nodes"]}
        for node in builders.values():
            issue_start = len(self.issues)
            self.fields(node)
            self.actions(node)
            self.method_results(node, issue_start)
            for name, status in node.row["fieldStatus"].items():
                if name not in DISCARD_FIELDS[node.backend]:
                    node.status(name, status)
        for edge in self.facts["relations"]:
            self.relation(builders[edge["source"]], edge)
        nodes = {NodeId(identity): node.build() for identity, node in builders.items()}
        for identity, raw in self.wire.nodes.items():
            if raw["kind"] == "element" and identity not in nodes:
                nodes[NodeId(identity)] = NativeNode()
                self.issues.append(ReadIssue(("nodes", identity), "not_read", "Referenced element was not expanded."))
        for reason in self.facts["stopReasons"]:
            self.issues.append(ReadIssue(("nodes",), "partial", reason))
        observation = NativeObservation(
            self.wire.platform,
            self.wire.session,
            self.capture_id,
            tuple(self.wire.interval),
            tuple(NodeId(x) for x in self.facts["roots"]),
            nodes,
            self.spaces,
            tuple(self.issues),
        )
        validate_query_observation(observation)
        metadata = DriverReadMetadata(
            self.facts["surface"],
            self.facts.get("changeToken"),
            self.relation_fields,
            {NodeId(row["node"]): tuple(row["requestedFields"]) for row in self.facts["nodes"]},
            tuple(self.facts["requestedRelations"]),
            self.field_sources,
            diagnostic_fields={identity: tuple(paths) for identity, paths in self.diagnostic_fields.items()},
            text_read_fields=self.text_read_fields,
        )
        return DriverObservationReply(observation, metadata)

    def fields(self, node: NodeBuilder) -> None:
        """Interpret platform declarations using domain-specific public fields."""
        backend, values = node.backend, node.values
        raw_role = values.get(ROLE_FIELDS[backend])
        if isinstance(raw_role, str):
            node.role = role_name(backend, raw_role)
            node.put(ROLE_FIELDS[backend], ("role",), Role(node.role))
            if node.role == "unknown" and not (backend == "macos" and raw_role == "AXUnknown"):
                node.supplement(ROLE_FIELDS[backend], raw_role, semantic_key="provider.role")
            if backend == "windows" and raw_role.removeprefix("ControlType.") == "Header":
                node.put("ControlType", ("role_detail",), "header")
            if backend == "linux" and raw_role in ROLE_DETAILS:
                node.put("role", ("role_detail",), ROLE_DETAILS[raw_role])
                if raw_role == "password text":
                    node.put("role", ("states", "protected"), True)
        elif ROLE_FIELDS[backend] in values:
            if raw_role is None:
                node.issue(("role",), "absent")
            else:
                node.issue(("role",), "error", "Provider role is not a string.")
                node.supplement(ROLE_FIELDS[backend], raw_role)
        for name in node.row["requestedFields"]:
            if name in node.methods:
                continue
            if name == ROLE_FIELDS[backend]:
                node.paths.setdefault(name, ("role",))
                continue
            if name in DISCARD_FIELDS[backend]:
                continue
            value = values.get(name, _ABSENT)
            if name in TEXT_FIELDS[backend]:
                path = (TEXT_FIELDS[backend][name],)
                node.paths[name] = path
                if value is not _ABSENT:
                    node.put(name, path, value, lambda item: isinstance(item, str))
            elif name in STATE_FIELDS[backend]:
                path = ("states", STATE_FIELDS[backend][name])
                node.paths[name] = path
                if value is not _ABSENT:
                    # pylint: disable-next=unidiomatic-typecheck
                    node.put(name, path, value, lambda item: type(item) is bool)
            elif name in STRUCTURE_FIELDS[backend]:
                path = ("structure", STRUCTURE_FIELDS[backend][name])
                node.paths[name] = path
                if value is not _ABSENT:
                    # pylint: disable-next=unidiomatic-typecheck
                    if name == "HeadingLevel" and type(value) is int:
                        value = value - 80050 if 80051 <= value <= 80059 else None if value == 80050 else value
                    # pylint: disable-next=unidiomatic-typecheck
                    elif name == "PositionInSet" and type(value) is int:
                        value = value - 1 if value > 0 else None if value == 0 else value
                    expected = (
                        # pylint: disable-next=unidiomatic-typecheck
                        (lambda x: type(x) is bool)
                        if path[-1] in ("can_select_multiple", "selection_required")
                        else (
                            (lambda x: isinstance(x, str))
                            if path[-1] == "landmark_description"
                            else lambda x, *, path=path: (
                                # pylint: disable-next=unidiomatic-typecheck
                                type(x) is int and x >= (1 if path[-1] in ("row_span", "column_span") else 0)
                            )
                        )
                    )
                    node.put(name, path, value, expected)
            elif name in NUMBER_FIELDS[backend]:
                field = NUMBER_FIELDS[backend][name]
                path = ("numeric_value", field)
                node.paths[name] = path
                if value is not _ABSENT:
                    if field in ("description", "read_only", "allowed_values"):
                        converted = node.extra(value, path)
                        if converted is not _ABSENT:
                            expected = {
                                "description": lambda x: isinstance(x, str),
                                # pylint: disable-next=unidiomatic-typecheck
                                "read_only": lambda x: type(x) is bool,
                                "allowed_values": lambda x: (
                                    isinstance(x, tuple)
                                    and all(
                                        type(item) in (str, bool, int, float)
                                        or isinstance(item, (datetime, ElementValue, SpecialValue))
                                        for item in x
                                    )
                                ),
                            }[field]
                            node.put(name, path, converted, expected)
                    else:
                        node.number(name, path, value)
            elif name in SUPPLEMENTS[backend]:
                if value is not _ABSENT:
                    if (
                        name in ("IsControlElement", "IsContentElement", "IsRequiredForForm", "sensitive", "multiLine")
                        # pylint: disable-next=unidiomatic-typecheck
                        and type(value) is not bool
                    ):
                        node.issue(
                            ("supplements", len(node.supplements), "value"),
                            "error",
                            "Provider declaration is not a boolean.",
                        )
                    node.supplement(name, value, semantic_key=SUPPLEMENTS[backend][name])
            elif name == "AXValueDescription":
                group = (
                    "numeric_value"
                    if any(key in values for key in ("AXMinValue", "AXMaxValue", "AXValueIncrement"))
                    else "control_value"
                )
                node.paths[name] = (group, "description")
                if value is not _ABSENT:
                    node.put(name, (group, "description"), value, lambda item: isinstance(item, str))
            elif value is not _ABSENT:
                if not self.special(node, name, value):
                    node.supplement(name, value)
        # Some settable/state-only reads contain no value; statuses still use their semantic paths.
        for name in node.row["fieldStatus"]:
            if name in DISCARD_FIELDS[backend]:
                continue
            node.paths.setdefault(name, self.special_path(name))

    @staticmethod
    def special_path(name: str) -> tuple[str, ...]:
        """Name the public destination even when a platform query has no value."""
        return {
            "AXValue": ("control_value", "value"),
            "Value.Value": ("control_value", "value"),
            "Value.IsReadOnly": ("control_value", "read_only"),
            "Text.DocumentRange": ("text", "content"),
            "Text.text": ("text", "content"),
            "Text.IsReadOnly": ("text", "read_only"),
            "Text.Selection": ("text", "selections"),
            "Text.selections": ("text", "selections"),
            "AXSelectedTextRange": ("text", "selections"),
            "AXSelectedTextRanges": ("text", "selections"),
            "AXSelectedText": ("text", "selected_text"),
            "Text.VisibleRanges": ("text", "visible_ranges"),
            "Text.CaretRange": ("text", "caret"),
            "Text.LineRanges": ("text", "line_ranges"),
            "Text.ObjectRanges": ("text", "embedded"),
            "Text.characterCount": ("text", "character_count"),
            "AXPosition": ("geometry", "bounds"),
            "AXSize": ("geometry", "bounds"),
            "Toggle.ToggleState": ("states", "checked"),
            "ExpandCollapse.ExpandCollapseState": ("states", "expanded"),
            "BoundingRectangle": ("geometry", "bounds"),
            "Component.extents": ("geometry", "bounds"),
        }.get(name, ("supplements", name))

    def special(self, node: NodeBuilder, name: str, value: Any) -> bool:
        """Handle compound declarations that cannot be reduced to a field rename."""
        if name == "AXSubrole":
            if not isinstance(value, str):
                return False
            if value in ("AXDialog", "AXSystemDialog"):
                node.data["role"] = Role("dialog")
                node.role = "dialog"
            if value in ROLE_DETAILS:
                node.put(name, ("role_detail",), ROLE_DETAILS[value])
                if value == "AXSecureTextField":
                    node.put(name, ("states", "protected"), True)
            elif value == "AXDialog":
                pass
            else:
                node.supplement(name, value, semantic_key="provider.role_detail")
            return True
        if name in ("AXValue", "Value.Value"):
            converted = node.extra(value, ("control_value", "value"))
            if (
                isinstance(converted, dict)
                and isinstance(converted.get("text"), str)
                and isinstance(converted.get("runs"), tuple)
            ):
                node.put(name, ("control_value", "value"), converted["text"])
                info = node.text_info()
                info["content_field"] = ("control_value", "value")
                info["runs"] = tuple(
                    self.attributed_run(node, item, index) for index, item in enumerate(converted["runs"])
                )
            elif converted is not _ABSENT and (
                type(converted) in (str, bool, int, float)
                or isinstance(converted, (datetime, ElementValue, SpecialValue))
            ):
                node.put(name, ("control_value", "value"), converted)
                if (
                    node.backend == "macos"
                    and isinstance(converted, str)
                    and any(key in node.values for key in _AXSELECTED_TEXT_FIELDS)
                ):
                    node.text_info()["content_field"] = ("control_value", "value")
                if node.backend == "macos" and node.role in ("checkbox", "radio_button", "switch", "toggle_button"):
                    state = {0: "off", 1: "on", 2: "mixed"}.get(converted) if type(converted) in (bool, int) else None
                    if state is not None:
                        node.put(name, ("states", "checked"), state)
                if (
                    node.backend == "macos"
                    and type(converted) in (int, float)
                    and any(key in node.values for key in ("AXMinValue", "AXMaxValue", "AXValueIncrement"))
                ):
                    node.put(name, ("numeric_value", "value"), converted)
            else:
                node.supplement(name, value)
            return True
        if name in ("Value.IsReadOnly", "readOnly"):
            # pylint: disable-next=unidiomatic-typecheck
            node.put(name, ("control_value", "read_only"), value, lambda item: type(item) is bool)
            return True
        if name == "AXValueDescription":
            group = (
                "numeric_value"
                if any(key in node.values for key in ("AXMinValue", "AXMaxValue", "AXValueIncrement"))
                else "control_value"
            )
            node.put(name, (group, "description"), value, lambda item: isinstance(item, str))
            return True
        if name in ("AXExpanded", "expanded", "ExpandCollapse.ExpandCollapseState"):
            if value is None:
                node.put(name, ("states", "expanded"), None)
                return True
            if node.backend == "linux" and name == "expanded" and value is False:
                actions = {item["name"].casefold() for item in node.row["actions"]["items"]}
                if node.role not in ("tree", "tree_item", "combo_box", "row", "disclosure_triangle") and not actions & {
                    "expand",
                    "collapse",
                }:
                    return False  # An absent AT-SPI flag does not establish an expandable control.
            if name == "ExpandCollapse.ExpandCollapseState" and not isinstance(value, str):
                return False
            normalized = (
                ("expanded" if value else "collapsed")
                # pylint: disable-next=unidiomatic-typecheck
                if type(value) is bool
                else {
                    "Expanded": "expanded",
                    "Collapsed": "collapsed",
                    "PartiallyExpanded": "partial",
                    "LeafNode": "leaf",
                }.get(value)
                if isinstance(value, str)
                else None
            )
            if normalized is None:
                return False
            node.put(name, ("states", "expanded"), normalized)
            return True
        if name in ("Toggle.ToggleState", "checked", "indeterminate"):
            if value is None:
                node.put(name, ("states", "checked"), None)
                return True
            if name == "Toggle.ToggleState" and not isinstance(value, str):
                return False
            if name == "indeterminate" and value is False:
                return True
            if node.backend == "linux" and name == "checked" and value is False:
                actions = {item["name"].casefold() for item in node.row["actions"]["items"]}
                if node.role not in ("checkbox", "radio_button", "switch", "toggle_button") and not (
                    node.data.get("role_detail") in ("check", "radio")
                    or "toggle" in actions
                    or node.values.get("indeterminate") is True
                ):
                    return False
            normalized = (
                "mixed"
                if name == "indeterminate" and value is True
                else ("on" if value else "off")
                # pylint: disable-next=unidiomatic-typecheck
                if type(value) is bool
                else enum_value({"On": "on", "Off": "off", "Indeterminate": "mixed"}, value)
            )
            if normalized is None:
                return False
            if name == "checked" and node.values.get("indeterminate") is True:
                return True
            node.put(name, ("states", "checked"), normalized)
            return True
        if name == "Window.WindowVisualState":
            normalized = enum_value({"Normal": "normal", "Minimized": "minimized", "Maximized": "maximized"}, value)
            if normalized is None:
                return False
            node.put(name, ("states", "window_state"), normalized)
            return True
        if name in ("AXOrientation", "Orientation"):
            if value in ("AXUnknownOrientation", "None"):
                node.paths[name] = ("structure", "orientation")
                node.issue(("structure", "orientation"), "absent", "Provider declares no orientation.")
                return True
            normalized = enum_value(
                {
                    "AXHorizontalOrientation": "horizontal",
                    "AXVerticalOrientation": "vertical",
                    "Horizontal": "horizontal",
                    "Vertical": "vertical",
                },
                value,
            )
            if normalized is None:
                return False
            node.put(name, ("structure", "orientation"), normalized)
            return True
        if name == "LandmarkType":
            normalized = enum_value(
                {80000: "custom", 80001: "form", 80002: "main", 80003: "navigation", 80004: "search"}, value
            )
            if normalized is None:
                if value == 0:
                    node.put(name, ("structure", "landmark"), None)
                    return True
                return False
            node.put(name, ("structure", "landmark"), normalized)
            return True
        if name in ("AXPosition", "AXSize", "BoundingRectangle", "Win32.ClientRectangle", "Component.extents"):
            if not isinstance(value, dict):
                return False
            kind = "client" if name == "Win32.ClientRectangle" else "bounds"
            combined = dict(value)
            if node.backend == "macos":

                def _iter_field(_source_items: Iterator[Any]) -> Iterator[Any]:
                    for field in _source_items:
                        if isinstance(node.values.get(field), dict):
                            for key, item in node.values[field].items():
                                yield (key, item)

                combined = dict(_iter_field(iter(("AXPosition", "AXSize"))))
            node.geometry[kind] = node.bounds(combined, ("geometry", kind))
            node.paths[name] = ("geometry", kind)
            return True
        if name.startswith("Scroll."):
            return self.scroll(node, name, value)
        if name in ("AXRowIndexRange", "AXColumnIndexRange"):
            if (
                not isinstance(value, dict)
                or set(value) != {"location", "length"}
                # pylint: disable-next=unidiomatic-typecheck
                or any(type(value[key]) is not int or value[key] < 0 for key in ("location", "length"))
            ):
                return False
            if value["location"] + value["length"] > 9007199254740991:
                return False
            prefix = "row" if name == "AXRowIndexRange" else "column"
            node.put(name, ("structure", f"{prefix}_index"), value["location"], lambda x: x >= 0)
            node.put(name, ("structure", f"{prefix}_span"), value["length"])
            return True
        if name == "TableCell.rowColumnSpan":
            if not isinstance(value, dict):
                return False
            keys = ("row", "column", "rowSpan", "columnSpan")
            valid = set(value) == set(keys) and all(
                # pylint: disable-next=unidiomatic-typecheck
                type(value[key]) is int
                and value[key] >= (1 if key.endswith("Span") else 0)
                and value[key] <= 9007199254740991
                for key in keys
            )
            if valid:
                valid = (
                    value["row"] + value["rowSpan"] <= 9007199254740991
                    and value["column"] + value["columnSpan"] <= 9007199254740991
                )
            if not valid:
                node.supplement(name, value)
                node.issue(
                    ("structure",), "error", "Provider cell position is not a complete valid row/column/span record."
                )
                return True
            for source, target in (
                ("row", "row_index"),
                ("column", "column_index"),
                ("rowSpan", "row_span"),
                ("columnSpan", "column_span"),
            ):
                if source in value:
                    node.put(
                        name,
                        ("structure", target),
                        value[source],
                        # pylint: disable-next=unidiomatic-typecheck
                        lambda x, *, target=target: type(x) is int and x >= (1 if target.endswith("_span") else 0),
                    )
            if not all(
                node.values.get(key) is True and key not in node.row["fieldStatus"] for key in ("visible", "showing")
            ):
                node.issue(
                    ("structure",), "partial", "Cell coordinates were reported, but viewport visibility is unconfirmed."
                )
            return True
        if name == "UIA.Collection" and isinstance(value, dict):
            # pylint: disable-next=unidiomatic-typecheck
            if type(value.get("isVirtualizedItem")) is bool:
                node.put(name, ("states", "virtualized"), value["isVirtualizedItem"])
            if value.get("mayContainVirtualizedItems") is True:
                node.put(name, ("structure", "collection_mode"), "virtualized")
            elif value.get("logicalContentComplete") is True:
                node.put(name, ("structure", "collection_mode"), "materialized")

            def _iter_key(_source_items: Iterator[Any]) -> Iterator[Any]:
                for key, item in _source_items:
                    if key not in (
                        "view",
                        "isVirtualizedItem",
                        "mayContainVirtualizedItems",
                        "logicalContentComplete",
                        "detail",
                    ):
                        yield (key, item)

            residual = dict(_iter_key(iter(value.items())))
            for key in ("isVirtualizedItem", "mayContainVirtualizedItems", "logicalContentComplete"):
                # pylint: disable-next=unidiomatic-typecheck
                if key in value and value[key] is not None and type(value[key]) is not bool:
                    residual[key] = value[key]
                    node.issue(
                        ("structure", "collection_mode"), "error", "Collection coverage declaration is not a boolean."
                    )
            if residual:
                node.supplement(name + ".extra", residual)
            if value.get("view") not in (None, "raw"):
                node.supplement(name + ".view", value["view"], semantic_key="collection.view")
            if value.get("mayContainVirtualizedItems") is True or value.get("logicalContentComplete") is False:
                node.issue(
                    ("structure", "collection_mode"),
                    "partial",
                    value.get("detail") or "Exposed objects do not establish complete logical item coverage.",
                )
            elif value.get("detail") is not None:
                node.supplement(name + ".detail", value["detail"], semantic_key="collection.detail")
            return True
        if name in ("SupportedPatterns", "interfaces") or name.startswith("settable."):
            return True  # These declarations are consumed by actions() after all domain values.
        if name in ("AccessKey", "AcceleratorKey"):
            if not isinstance(value, str):
                return False
            if value:
                node.data["shortcuts"] = (
                    *node.data.get("shortcuts", ()),
                    Shortcut(value, "access_key" if name == "AccessKey" else "accelerator"),
                )
            return True
        if name in ("AXMenuItemCmdChar", "AXMenuItemCmdModifiers", "AXMenuItemCmdVirtualKey", "AXMenuItemCmdGlyph"):
            return True  # Processed together to avoid guessing a glyph or keyboard layout.
        if name == "attributes" and isinstance(value, dict):
            for key, item in value.items():
                if key in ("placeholder-text", "placeholder") and isinstance(item, str):
                    node.put(name, ("placeholder",), item)
                elif key in ("level", "posinset", "setsize") and isinstance(item, str) and item.isdigit():
                    field = {"level": "level", "posinset": "index", "setsize": "set_size"}[key]
                    number = int(item) - (1 if key == "posinset" else 0)
                    if number < 0:
                        node.issue(("structure", field), "error", "Provider position-in-set must be positive.")
                        node.supplement(f"attributes.{key}", item, semantic_key=f"provider.attribute.{key}")
                    else:
                        node.put(name, ("structure", field), number)
                else:
                    node.supplement(f"attributes.{key}", item, semantic_key=f"provider.attribute.{key}")
            return True
        return self.text_field(node, name, value)

    @staticmethod
    def attributed_run(node: NodeBuilder, value: Any, index: int) -> TextSpan:
        """Preserve collected rich-text semantics without requiring font wrappers."""
        if not isinstance(value, dict):
            node.issue(("text", "runs", index), "error", "Attributed run is unavailable.")
            return TextSpan()
        bounds = value.get("range", {})
        span = node.span(bounds, ("text", "runs", index))
        attributes = {}
        raw_attributes = value.get("attributes", {})
        if not isinstance(raw_attributes, dict):
            node.supplement(f"attributed_run.{index}.attributes", raw_attributes)
            node.issue(("text", "runs", index, "attributes"), "error", "Attributed text attributes are not a record.")
            raw_attributes = {}
        for key, item in raw_attributes.items():
            target = {
                "AXLanguage": "language",
                "AXHeadingLevel": "heading_level",
                "AXLink": "link",
                "AXMisspelled": "misspelled",
                "AXMarkedMisspelled": "misspelled",
            }.get(key)
            if target is not None and type(item) in (str, bool, int, float):
                attributes[target] = item
            elif key not in ("AXFont", "AXForegroundColor", "AXBackgroundColor", "AXUnderline", "AXStrikethrough"):
                node.supplement(f"attributed_run.{index}.{key}", item)
        return TextSpan(span.start, span.end, span.text, span.collapsed, span.geometry, attributes)

    def text_field(self, node: NodeBuilder, name: str, value: Any) -> bool:
        """Normalize text scopes, ranges and continuation-independent metadata."""
        validator = {
            "Text.CaretRange": windows_text.caret_range,
            "Text.ObjectRanges": windows_text.object_ranges,
            "Text.LineRanges": windows_text.line_ranges,
        }.get(name)
        if validator is not None and not validator(value):
            node.supplement(name, value)
            node.issue(
                self.special_path(name), "error", "Malformed text-range evidence cannot establish confirmed offsets."
            )
            return True
        if name in (
            "Text.DocumentRange",
            "Text.text",
            "AXSelectedText",
            "Text.characterCount",
            "AXNumberOfCharacters",
            "Text.IsReadOnly",
            "Text.SupportedTextSelection",
        ):
            info = node.text_info()
            field = {
                "Text.DocumentRange": "content",
                "Text.text": "content",
                "AXSelectedText": "selected_text",
                "Text.characterCount": "character_count",
                "AXNumberOfCharacters": "character_count",
                "Text.IsReadOnly": "read_only",
                "Text.SupportedTextSelection": "supported_selection",
            }[name]
            if name == "Text.IsReadOnly" and value == "mixed":
                value = SpecialValue.MIXED
            if name == "Text.SupportedTextSelection":
                original = value
                value = enum_value({"None": "none", "Single": "single", "Multiple": "multiple"}, value)
                if value is None:
                    node.issue(("text", field), "error", "Unrecognized text selection support declaration.")
                    node.supplement(name, original)
                    return True
            expected = {
                "content": lambda x: isinstance(x, str),
                "selected_text": lambda x: isinstance(x, str),
                # pylint: disable-next=unidiomatic-typecheck
                "character_count": lambda x: type(x) is int and x >= 0,
                # pylint: disable-next=unidiomatic-typecheck
                "read_only": lambda x: type(x) is bool or x is SpecialValue.MIXED,
            }.get(field)
            node.put(name, ("text", field), value, expected)
            return True
        if name in ("AXSelectedTextRange", "AXSelectedTextRanges", "Text.selections", "AXVisibleCharacterRange"):
            field = "visible_ranges" if name == "AXVisibleCharacterRange" else "selections"
            items = value if isinstance(value, list) else [value]
            if node.backend == "macos" and name.startswith("AX"):
                # Cocoa's 64-bit NSNotFound is not a character offset. Keep
                # the native range as evidence without manufacturing a text
                # space or a caret on controls whose getter returns it.
                unlocated = [item for item in items if isinstance(item, dict) and item.get("location") == 2**63 - 1]
                if unlocated:
                    items = [item for item in items if item not in unlocated]
                    node.supplement(name, value, semantic_key="text.unlocated_range")
                    state = "partial"
                    if not items:
                        state = "absent" if all(item.get("length") == 0 for item in unlocated) else "error"
                    node.issue(("text", field), state, "AX reports NSNotFound; no character position is available.")
                    if not items:
                        return True
            node.text_info()
            node.put(name, ("text", field), tuple(node.span(item, ("text", field, i)) for i, item in enumerate(items)))
            return True
        if name == "Text.caretOffset":
            info = node.text_info()
            # pylint: disable-next=unidiomatic-typecheck
            if type(value) is int and value >= 0:
                node.put(name, ("text", "caret"), TextSpan(value, value, collapsed=True))
            else:
                node.issue(("text", "caret"), "error", "Caret offset is not a nonnegative integer.")
            return True
        if name in ("Text.Selection", "Text.SelectionBounds", "Text.VisibleRanges") and isinstance(value, dict):
            info = node.text_info()
            field = "visible_ranges" if name == "Text.VisibleRanges" else "selections"
            ranges = value.get("ranges")
            if not isinstance(ranges, list):
                return False
            spans = tuple(node.span(item, ("text", field, i)) for i, item in enumerate(ranges))
            if name == "Text.SelectionBounds" and field in info:
                node.supplement(name, value, semantic_key="text_selection_geometry")
            else:
                node.put(name, ("text", field), spans)
            if value.get("state") not in (None, "complete"):
                node.issue(("text", field), "partial", value.get("detail"))
            # pylint: disable-next=unidiomatic-typecheck
            if type(value.get("total")) is int and value["total"] >= 0:
                node.supplement(name + ".coverage", {"total": value["total"]}, semantic_key=f"text.{field}.coverage")
            return True
        if name == "Text.CaretRange" and isinstance(value, dict):
            node.text_info()
            if value.get("state") == "notProbed":
                node.issue(("text", "caret"), "not_read", value.get("detail"))
                return True
            observed = value.get("values", {})
            # pylint: disable-next=unidiomatic-typecheck
            if type(observed.get("isActive")) is bool:
                node.put(name, ("text", "caret_active"), observed["isActive"])
            if isinstance(observed.get("range"), dict):
                node.put(
                    name,
                    ("text", "caret"),
                    self.uia_span(
                        node, name, value, observed["range"], ("text", "caret"), windows_text.caret_range(value)
                    ),
                )
            for key, status in value.get("fieldStatus", {}).items():
                node.issue(("text", "caret"), status.get("state", "error"), status.get("detail"))
            return True
        if name == "Text.LineRanges" and isinstance(value, dict):
            node.text_info()
            if value.get("state") == "notProbed":
                node.issue(("text", "line_ranges"), "not_read", value.get("detail"))
                return True
            ranges = value.get("ranges")
            if not isinstance(ranges, list):
                return False
            node.put(
                name,
                ("text", "line_ranges"),
                tuple(
                    self.uia_span(node, name, value, item, ("text", "line_ranges", i), windows_text.line_ranges(value))
                    for i, item in enumerate(ranges)
                ),
            )
            self.uia_coverage(node, name, value)
            if value.get("state") != "complete":
                node.issue(("text", "line_ranges"), "partial", value.get("detail"))
            return True
        if name == "Text.ObjectRanges" and isinstance(value, dict):
            node.text_info()
            objects = value.get("objects")
            if not isinstance(objects, list):
                return False
            embedded = []
            for index, item in enumerate(objects):
                identity = item.get("node") if isinstance(item, dict) else None
                if identity in self.wire.nodes and self.wire.nodes[identity]["kind"] == "element":
                    embedded.append(
                        (
                            NodeId(identity),
                            self.uia_span(
                                node,
                                name,
                                value,
                                item,
                                ("text", "embedded", index, 1),
                                windows_text.object_ranges(value),
                            ),
                        )
                    )
                else:
                    node.issue(
                        ("text", "embedded", index), "unavailable", "Embedded object reference was not obtained."
                    )
            node.put(name, ("text", "embedded"), tuple(embedded))
            self.uia_coverage(node, name, value)
            if value.get("state") != "complete":
                node.issue(("text", "embedded"), "partial", value.get("detail"))
            return True
        return False

    @staticmethod
    def uia_coverage(node: NodeBuilder, name: str, value: Mapping[str, Any]) -> None:
        """Retain provider-unit and coverage limitations outside proven spans."""

        def _iter_source(_source_items: Iterator[Any]) -> Iterator[Any]:
            for source, target in _source_items:
                if source in value:
                    yield (target, value[source])

        semantic = dict(
            _iter_source(
                iter(
                    (
                        ("unitSupport", "unit_support"),
                        ("requestedUnit", "requested_unit"),
                        ("documentEndReached", "document_end_observed"),
                        ("order", "order"),
                        ("total", "total"),
                        ("detail", "detail"),
                    )
                )
            )
        )
        if semantic:
            node.supplement(
                name + ".coverage",
                semantic,
                semantic_key="text."
                + {"Text.LineRanges": "line_ranges", "Text.ObjectRanges": "embedded"}.get(name, "ranges")
                + ".coverage",
            )
        for key in ("orderStatus", "anchorStatus"):
            if isinstance(value.get(key), dict):
                status = value[key]
                node.issue(
                    ("text", "line_ranges" if name == "Text.LineRanges" else "embedded"),
                    status.get("state", "error"),
                    status.get("detail"),
                )

    @staticmethod
    def uia_span(
        node: NodeBuilder,
        name: str,
        outer: Mapping[str, Any],
        raw: Mapping[str, Any],
        path: tuple[str | int, ...],
        structurally_valid: bool,
    ) -> TextSpan:
        """Publish endpoints only when their captured text anchor still agrees."""
        span = node.span(raw, path)
        anchor = outer.get("anchor", outer.get("values", {}).get("anchor"))
        detail = (
            "Text range evidence is malformed."
            if not structurally_valid
            else (
                windows_text.text_agreement(anchor, node.values, node.row["fieldStatus"])
                if anchor
                else "Text anchor is unavailable."
            )
        )
        fields_value = raw.get("values", raw)
        if not detail and "anchorStatus" in outer:
            detail = "The document anchor changed during collection."
        if not detail and span.start is not None and span.end is not None:
            if not windows_text.valid_text_boundaries({"start": span.start, "end": span.end}, node.values):
                detail = "Text endpoints split a UTF-16 character."
            elif name == "Text.LineRanges" and windows_text.line_text_conflict(fields_value, node.values):
                detail = "Text span conflicts with the captured document."
        if detail:
            node.issue(path, "conflict" if structurally_valid else "error", detail)
            # Preserve disputed provider positions as evidence rather than usable offsets.
            if span.start is not None or span.end is not None:
                node.supplement(
                    name + ".position",
                    {key: value for key, value in (("start", span.start), ("end", span.end)) if value is not None},
                    semantic_key="text.unconfirmed_position",
                )
            return replace(span, start=None, end=None)
        return span

    @staticmethod
    def scroll(node: NodeBuilder, name: str, value: Any) -> bool:
        """Convert UIA percentages to fractions while preserving scroll availability."""
        fields_value_local = {
            "HorizontallyScrollable": ("scroll_x", "scrollable"),
            "VerticallyScrollable": ("scroll_y", "scrollable"),
            "HorizontalScrollPercent": ("scroll_x", "position"),
            "VerticalScrollPercent": ("scroll_y", "position"),
            "HorizontalViewSize": ("scroll_x", "viewport"),
            "VerticalViewSize": ("scroll_y", "viewport"),
        }
        pair = fields_value_local.get(name.removeprefix("Scroll."))
        if pair is None:
            return False
        axis, field = pair
        structure = node.groups.setdefault("structure", {})
        current = structure.get(axis)
        values = (
            {key: getattr(current, key) for key in ("scrollable", "position", "minimum", "maximum", "viewport", "unit")}
            if current
            else {}
        )
        # pylint: disable-next=unidiomatic-typecheck
        if field == "scrollable" and type(value) is bool:
            values[field] = value
        elif field != "scrollable" and type(value) in (int, float) and math.isfinite(value):
            if field == "position" and value == -1:
                node.issue(("structure", axis, field), "absent", "No scroll position is declared on this axis.")
            else:
                values.update({field: value / 100, "unit": "fraction"})
        else:
            return False
        structure[axis] = ScrollAxis(**values)
        node.paths[name] = ("structure", axis, field)
        return True

    def actions(self, node: NodeBuilder) -> None:
        """Keep executable selectors and derive separate parameterized declarations."""
        catalogue = node.row["actions"]
        for index, raw in enumerate(catalogue["items"]):
            name = raw["name"]
            kind = ACTION_KINDS[node.backend].get(name, "custom")
            description = raw.get("description") if isinstance(raw.get("description"), str) else None
            node.actions.append(Action(kind, name, name, description))
            if "description" in raw and raw["description"] is not None and description is None:
                node.issue(("actions", index, "description"), "error", "Provider action description is not text.")
                node.supplement("action." + name + ".description", raw["description"])
            if isinstance(raw.get("descriptionStatus"), dict):
                status = raw["descriptionStatus"]
                node.issue(("actions", index, "description"), status.get("state", "error"), status.get("detail"))
        if catalogue["state"] != "complete":
            queried = any(
                prop["key"] == ["toolkit", "actions"] for prop in self.wire.nodes[node.identity]["properties"]
            )
            detail = catalogue.get("detail")
            if catalogue.get("total") is not None:
                detail = f"Observed {len(catalogue['items'])} of {catalogue['total']} declared actions." + (
                    " " + detail if detail else ""
                )
            node.issue(("actions",), catalogue["state"] if queried else "not_read", detail)
        values = node.values
        if node.backend == "macos":
            for name, kind, target in (
                ("settable.AXFocused", "focus", None),
                ("settable.AXValue", "set_value", ("control_value", "value")),
                ("settable.AXSelectedText", "replace_selection", ("text", "selections")),
            ):
                # pylint: disable-next=unidiomatic-typecheck
                if type(values.get(name)) is bool:
                    node.actions.append(Action(kind, target=target, available=values[name]))
                    node.paths[name] = ("actions", len(node.actions) - 1)
                elif name in values:
                    node.supplement(name, values[name])
                    node.issue(node.paths[name], "error", "Settable capability is not a boolean.")
            self.mac_shortcut(node)
        elif node.backend == "windows":
            patterns = values.get("SupportedPatterns", [])
            if not isinstance(patterns, list):
                node.supplement("SupportedPatterns", patterns)
                patterns = []
            elif any(not isinstance(item, str) for item in patterns):
                node.supplement("SupportedPatterns", patterns)
            names = (
                {item.split("Pattern")[0] for item in patterns if isinstance(item, str)}
                if isinstance(patterns, list)
                else set()
            )
            for pattern, target, readonly in (
                ("Value", ("control_value", "value"), "Value.IsReadOnly"),
                ("RangeValue", ("numeric_value", "value"), "RangeValue.IsReadOnly"),
            ):
                if pattern in names:
                    state = values.get(readonly)
                    node.actions.append(
                        # pylint: disable-next=unidiomatic-typecheck
                        Action("set_value", target=target, available=not state if type(state) is bool else None)
                    )
            if "Scroll" in names:
                node.actions.append(Action("scroll", target=("structure",), available=None))
            if "SelectionItem" in names:
                node.put("SupportedPatterns", ("states", "selectable"), True)
            # pylint: disable-next=unidiomatic-typecheck
            if type(values.get("IsKeyboardFocusable")) is bool:
                node.actions.append(Action("focus", available=values["IsKeyboardFocusable"]))
            consumed = {
                "Invoke",
                "Toggle",
                "SelectionItem",
                "ExpandCollapse",
                "ScrollItem",
                "Window",
                "Value",
                "RangeValue",
                "Scroll",
                "Text",
                "Text2",
                "TextChild",
                "Grid",
                "GridItem",
                "Table",
                "TableItem",
                "Selection",
                "Selection2",
                "ItemContainer",
                "VirtualizedItem",
            }
            unknown = tuple(
                item for item in patterns if isinstance(item, str) and item.split("Pattern")[0] not in consumed
            )
            if unknown:
                node.supplement("SupportedPatterns", unknown, semantic_key="provider.windows.patterns")
        else:
            interfaces = values.get("interfaces", [])
            if isinstance(interfaces, list):
                if "EditableText" in interfaces:
                    node.actions.extend(
                        (
                            Action("insert_text", target=("text", "content")),
                            Action("set_value", target=("text", "content")),
                        )
                    )
                if "Value" in interfaces:
                    node.actions.append(Action("set_value", target=("numeric_value", "value")))
                if values.get("focusable") is True:
                    node.actions.append(Action("focus", available=True))

                def _iter_item(_source_items: Iterator[Any]) -> Iterator[Any]:
                    for item in _source_items:
                        if item not in (
                            "Accessible",
                            "Action",
                            "Component",
                            "Text",
                            "EditableText",
                            "Value",
                            "Selection",
                            "Table",
                            "TableCell",
                            "Document",
                            "Hypertext",
                            "Hyperlink",
                            "Image",
                        ):
                            yield item

                unknown = tuple(_iter_item(iter(interfaces)))
                if unknown:
                    node.supplement("interfaces", unknown, semantic_key="provider.linux.interfaces")
            else:
                node.supplement("interfaces", interfaces)

    def method_results(self, node: NodeBuilder, issue_start: int) -> None:
        """Keep method parameters with results and attach complete action descriptions."""
        for name, (key, arguments) in node.methods.items():
            value = node.values.get(name, _ABSENT)
            if key == ("toolkit", "text_character_count") and arguments == ("AXNumberOfCharacters",):
                evidence = {"arguments": arguments, "read": node.method_reads[name]}
                if value is not _ABSENT:
                    evidence["result"] = value
                node.supplement(name, evidence, semantic_key="provider.macos.text_capture_count", diagnostic=True)
                continue
            if key == ("macos.ax", "AXStringForRange"):
                read = node.method_reads[name]
                complete = macos_text.captured_content(node, name, arguments, value, read)
                if complete:
                    self.text_read_fields[NodeId(node.identity)] = ("text", "content")
                evidence = {"arguments": arguments, "read": read}
                if value is not _ABSENT:
                    evidence["result"] = value
                node.supplement(
                    name + ".evidence" if complete else name,
                    evidence,
                    semantic_key="provider.macos.parameterized_text",
                    diagnostic=complete,
                )
                continue
            matches_key = (
                True
                if (
                    key == ("toolkit", "action_description")
                    and len(arguments) == 1
                    and isinstance(arguments[0], str)
                    and isinstance(value, str)
                )
                else False
            )
            if matches_key:
                index = next((index for index, action in enumerate(node.actions) if action.key == arguments[0]), None)
                if index is not None:
                    previous = node.actions[index].description
                    if previous is None or value.startswith(previous):
                        path = ("actions", index, "description")
                        node.actions[index] = replace(node.actions[index], description=value)
                        node.paths[name] = path
                        self.field_sources[(NodeId(node.identity), path)] = name
                        if name not in node.row["fieldStatus"]:
                            self.issues[issue_start:] = [
                                issue
                                for issue in self.issues[issue_start:]
                                if not (issue.path == ("nodes", node.identity, *path) and issue.state == "partial")
                            ]
                        continue
            record = {"arguments": arguments}
            if value is not _ABSENT:
                record["result"] = value
            node.supplement(name, record, semantic_key=f"provider.{node.backend}.{key[0]}.{key[1]}")

    @staticmethod
    def mac_shortcut(node: NodeBuilder) -> None:
        """Interpret declared AX modifier bits without guessing virtual-key glyphs."""
        values = node.values
        character, modifiers = values.get("AXMenuItemCmdChar"), values.get("AXMenuItemCmdModifiers")
        known = node.role == "menu_item" and not any(
            name in node.row["fieldStatus"] for name in ("AXRole", "AXMenuItemCmdChar", "AXMenuItemCmdModifiers")
        )
        matches_known = (
            True
            if (
                known
                and isinstance(character, str)
                and len(character) == 1
                # pylint: disable-next=unidiomatic-typecheck
                and type(modifiers) is int
                and 0 <= modifiers <= 15
            )
            else False
        )
        if matches_known:
            scalar = ord(character)
            key = (
                f"F{scalar - 0xF704 + 1}"
                if 0xF704 <= scalar <= 0xF70F
                else {"\uf700": "↑", "\uf701": "↓", "\uf702": "←", "\uf703": "→", " ": "Space"}.get(
                    character, character
                )
            )
            if not unicodedata.category(key[0]).startswith(("C", "Z")):
                flags = "".join(label for bit, label in ((4, "⌃"), (2, "⌥"), (1, "⇧")) if modifiers & bit)
                node.data["shortcuts"] = (Shortcut(flags + ("" if modifiers & 8 else "⌘") + key, "accelerator"),)
                return
        for name in ("AXMenuItemCmdChar", "AXMenuItemCmdModifiers", "AXMenuItemCmdVirtualKey", "AXMenuItemCmdGlyph"):
            if name in values and values[name] not in ("", 0):
                node.supplement(name, values[name], semantic_key=f"provider.macos.shortcut.{name}")

    def relation(self, node: NodeBuilder, edge: Mapping[str, Any]) -> None:
        """Retain relation views, target order, page slots and native query selectors."""
        name = str(edge["property"])
        kind = RELATIONS[node.backend].get(name, f"provider.{node.backend}.{name}")
        key = kind
        index = 2
        while key in node.relations:
            key = f"{kind}:{index}"
            index += 1
        targets = tuple(NodeId(x) for x in edge["targets"])
        offset = edge.get("offset")
        # pylint: disable-next=unidiomatic-typecheck
        positions = tuple(range(offset, offset + len(targets))) if type(offset) is int else None
        evidence = {
            "confirmed": "reported",
            "inferred": "candidate",
            "unknown": "candidate",
            "conflicting": "candidate",
        }[edge["certainty"]]
        mode = "sampled" if kind == "viewport_candidates" else "enumerated"
        node.relations[key] = Relation(
            RelationKind(kind),
            targets,
            "raw" if node.backend == "windows" and name.startswith("RawView") else None,
            positions,
            edge.get("total"),
            mode,
            evidence,
            offset=offset,
        )
        self.relation_fields[(NodeId(node.identity), key)] = name
        if edge["state"] != "complete":
            continuation = ReadRef(edge["cursor"], "relation", "live") if isinstance(edge.get("cursor"), str) else None
            node.issue(("relations", key), edge["state"], edge.get("detail"), continuation)
        elif mode == "sampled":
            node.issue(("relations", key), "partial", edge.get("detail"))
        elif edge.get("detail"):
            node.supplement(name + ".detail", {"relation": key, "text": edge["detail"]}, semantic_key="relation_detail")
        if edge["certainty"] == "conflicting":
            node.issue(("relations", key), "conflict", "Provider relationship declarations conflict.")


def decode_driver_reply(value: Any) -> DriverObservationReply:
    """Decode a driver response into an observation and independent query metadata.

    Args:
        value: Parsed current driver JSON using kind ``observation``.

    Returns:
        A frozen normalized graph and selectors needed for bounded follow-up reads.

    Raises:
        ValueError: The driver response violates identity, type or coverage contracts.
    """
    wire = DriverWire(value, semantic_values=True)
    return Adapter(wire, query_facts(wire)).normalize()


def decode_driver_observation(value: Any) -> NativeObservation:
    """Return the normalized graph when native follow-up routing is unnecessary."""
    return decode_driver_reply(value).observation
