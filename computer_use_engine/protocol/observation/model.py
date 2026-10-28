"""Immutable, platform-normalized facts consumed by shared presentation passes.

Element identities are driver-owned strings. Graph relationships may be cyclic;
record containers are detached and frozen, and native objects stay in drivers.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal, NewType, TypeAlias

Backend: TypeAlias = Literal["macos", "windows", "linux"]
NodeId = NewType("NodeId", str)
SpaceId = NewType("SpaceId", str)
Role = NewType("Role", str)  # Platform-neutral role vocabulary.
RelationKind = NewType("RelationKind", str)  # Platform-neutral relation vocabulary.
FieldPath: TypeAlias = tuple[str | int, ...]
TextUnit: TypeAlias = Literal["unicode_codepoint", "utf16_code_unit", "provider"]


def _freeze(value: Any, active: set[int] | None = None) -> Any:
    """Detach public containers so published observations cannot change in place."""
    if not isinstance(value, (Mapping, tuple, list)):
        return value
    active = set() if active is None else active
    if id(value) in active:
        raise ValueError("observation: container values must be acyclic")
    active.add(id(value))
    try:
        if isinstance(value, Mapping):
            return MappingProxyType({key: _freeze(item, active) for key, item in value.items()})
        return tuple(_freeze(item, active) for item in value)
    finally:
        active.remove(id(value))


def _equal(left: Any, right: Any) -> bool:
    """Compare observation values with stable equality for reported NaN values."""
    matches_left = (
        # Exact types preserve scalar validation and identity contracts.
        # pylint: disable-next=unidiomatic-typecheck
        True if type(left) is float and type(right) is float and math.isnan(left) and math.isnan(right) else False
    )
    if matches_left:
        return True
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, tuple) and isinstance(right, tuple):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    # pylint: disable-next=unidiomatic-typecheck
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    return left == right


class _Immutable:
    """Freeze each record's collections once construction has finished."""

    __slots__ = ()

    def __eq__(self, other: Any) -> bool:
        if type(self) is not type(other):
            return NotImplemented
        return all(_equal(getattr(self, item.name), getattr(other, item.name)) for item in fields(self))

    __hash__ = None

    def __deepcopy__(self, memo: dict[int, Any]) -> Any:
        memo[id(self)] = self
        return self

    def __post_init__(self) -> None:
        for item in fields(self):
            object.__setattr__(self, item.name, _freeze(getattr(self, item.name)))


class SpecialValue(Enum):
    MIXED = "mixed"  # 已观察到混合值，与未知／失败不同


@dataclass(frozen=True, slots=True, eq=False)
class ElementValue(_Immutable):
    """An element-valued property, without implying containment or selection."""

    node: NodeId


Scalar: TypeAlias = str | bool | int | float | datetime | SpecialValue
Value: TypeAlias = Scalar | ElementValue
ExtraValue: TypeAlias = Value | tuple["ExtraValue", ...] | Mapping[str, "ExtraValue"]


@dataclass(frozen=True, slots=True, eq=False)
class ReadRef(_Immutable):
    """An opaque handle accepted by an existing bounded observation query."""

    token: str
    kind: Literal["text", "text_range", "relation", "property"]
    consistency: Literal["captured", "live"]
    unit: TextUnit | None = None  # 文本续读接口的单位，可与 TextInfo.unit 不同
    total: int | None = None
    delivered: tuple[int, int] | None = None  # 已交付文本在续读空间中的半开区间


@dataclass(frozen=True, slots=True, eq=False)
class ReadIssue(_Immutable):
    """A non-success or partial result at one observation-relative field path."""

    path: FieldPath
    state: Literal[
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
    ]
    detail: str | None = None
    continuation: ReadRef | None = None
    alternatives: tuple[ExtraValue, ...] = ()  # 冲突证据，非自动择优结果


@dataclass(frozen=True, slots=True, eq=False)
class CoordinateSpace(_Immutable):
    """Units and origin used to interpret geometry from this capture."""

    unit: Literal["point", "physical_pixel", "logical_pixel", "provider"]
    origin: Literal[
        "primary_screen_top_left",
        "desktop_top_left",
        "window_top_left",
        "element_top_left",
        "provider",
    ]
    y_direction: Literal["down", "up"]
    owner: NodeId | None = None
    parent: SpaceId | None = None
    to_parent: tuple[float, float, float, float, float, float] | None = None


@dataclass(frozen=True, slots=True, eq=False)
class Geometry(_Immutable):
    """Collected position and size; missing components remain unknown."""

    space: SpaceId
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    valid: bool | None = None


@dataclass(frozen=True, slots=True, eq=False)
class Shortcut(_Immutable):
    """A declared shortcut and the context in which it applies."""

    text: str
    kind: Literal["access_key", "accelerator", "menu_sequence", "custom"]


@dataclass(frozen=True, slots=True, eq=False)
class Action(_Immutable):
    """A declared operation bound to the exact source element."""

    kind: str  # 公共动作目录；无法归一时为 custom
    key: str | None = None  # 仅已有驱动端口可按选择符执行的动作填写
    label: str | None = None
    description: str | None = None
    target: FieldPath | None = None  # 相对当前节点，如 ("control_value", "value")
    available: bool | None = None
    shortcuts: tuple[Shortcut, ...] = ()


@dataclass(frozen=True, slots=True, eq=False)
class ControlValue(_Immutable):
    """A control's value, independent of document text and numeric range data."""

    value: Value | None = None
    description: str | None = None
    read_only: bool | None = None


@dataclass(frozen=True, slots=True, eq=False)
class NumericValue(_Immutable):
    """A numeric value together with its declared range and increments."""

    value: int | float | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    small_step: int | float | None = None
    large_step: int | float | None = None
    description: str | None = None
    read_only: bool | None = None
    allowed_values: tuple[Value, ...] | None = None


@dataclass(frozen=True, slots=True, eq=False)
class TextSpan(_Immutable):
    """A range in its owning TextInfo, with optional proven offsets."""

    start: int | None = None
    end: int | None = None
    text: str | None = None
    collapsed: bool | None = None
    geometry: tuple[Geometry, ...] | None = None
    attributes: Mapping[str, Scalar] = field(default_factory=dict)
    read_ref: ReadRef | None = None


@dataclass(frozen=True, slots=True, eq=False)
class TextInfo(_Immutable):
    """Text, selection and caret facts belonging to one provider text space."""

    source: str  # 本观察中的文本范围身份，不能按相同字符串合并
    unit: TextUnit | None = None
    content: str | None = None
    content_field: FieldPath | None = None
    character_count: int | None = None
    selected_text: str | None = None
    read_only: bool | SpecialValue | None = None
    runs: tuple[TextSpan, ...] | None = None  # 有语义属性的范围，不复制整段正文
    selections: tuple[TextSpan, ...] | None = None
    caret: TextSpan | None = None
    caret_active: bool | None = None
    visible_ranges: tuple[TextSpan, ...] | None = None
    line_ranges: tuple[TextSpan, ...] | None = None
    embedded: tuple[tuple[NodeId, TextSpan], ...] | None = None
    supported_selection: Literal["none", "single", "multiple"] | None = None


@dataclass(frozen=True, slots=True, eq=False)
class States(_Immutable):
    """Independent state declarations; None never means false."""

    enabled: bool | None = None
    focusable: bool | None = None
    focused: bool | None = None
    selectable: bool | None = None
    selected: bool | None = None
    editable: bool | None = None
    protected: bool | None = None
    busy: bool | None = None
    modal: bool | None = None
    has_popup: bool | None = None
    active: bool | None = None
    main_window: bool | None = None
    visible: bool | None = None
    showing: bool | None = None
    offscreen: bool | None = None
    virtualized: bool | None = None
    checked: Literal["on", "off", "mixed"] | None = None
    expanded: Literal["expanded", "collapsed", "partial", "leaf"] | None = None
    window_state: Literal["normal", "minimized", "maximized"] | None = None


@dataclass(frozen=True, slots=True, eq=False)
class Relation(_Immutable):
    """Ordered targets and coverage of one bounded collection view.

    ``offset`` records a requested page start even when no target was returned.
    ``positions`` records the original slot of each acquired target.
    """

    kind: RelationKind
    targets: tuple[NodeId, ...]
    view: str | None = None  # 必要时区分公共查询视图，如 raw/control/content
    positions: tuple[int, ...] | None = None
    total: int | None = None
    enumeration: Literal["enumerated", "sampled", "provider_managed"] = "enumerated"
    evidence: Literal["reported", "verified", "candidate"] = "reported"
    offset: int | None = None


@dataclass(frozen=True, slots=True, eq=False)
class ScrollAxis(_Immutable):
    """Declared scroll state on one axis."""

    scrollable: bool | None = None
    position: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    viewport: float | None = None
    unit: Literal["fraction", "logical", "physical_pixel", "point", "provider"] | None = None


@dataclass(frozen=True, slots=True, eq=False)
class Structure(_Immutable):
    """Optional document, collection, table and scroll semantics."""

    heading_level: int | None = None
    landmark: str | None = None
    landmark_description: str | None = None
    level: int | None = None
    index: int | None = None
    set_size: int | None = None
    selected_count: int | None = None
    row_count: int | None = None
    column_count: int | None = None
    row_index: int | None = None
    column_index: int | None = None
    row_span: int | None = None
    column_span: int | None = None
    can_select_multiple: bool | None = None
    selection_required: bool | None = None
    orientation: Literal["horizontal", "vertical"] | None = None
    collection_mode: Literal["materialized", "virtualized", "provider_managed"] | None = None
    scroll_x: ScrollAxis | None = None
    scroll_y: ScrollAxis | None = None


@dataclass(frozen=True, slots=True, eq=False)
class Supplement(_Immutable):
    """A relevant fact without an equivalent core representation."""

    key: str  # 已登记语义键，或保留提供者未知事实的命名空间键
    value: ExtraValue
    label: str | None = None


@dataclass(frozen=True, slots=True, eq=False)
class NativeNode(_Immutable):
    """Unified facts about one observed UI element, before presentation folding."""

    role: Role | None = None
    role_detail: str | None = None
    role_description: str | None = None
    name: str | None = None
    description: str | None = None
    help: str | None = None
    identifier: str | None = None
    placeholder: str | None = None
    url: str | None = None
    control_value: ControlValue | None = None
    numeric_value: NumericValue | None = None
    text: TextInfo | None = None
    states: States = field(default_factory=States)
    geometry: Mapping[str, Geometry] = field(default_factory=dict)
    actions: tuple[Action, ...] | None = None
    relations: Mapping[str, Relation] = field(default_factory=dict)
    structure: Structure | None = None
    shortcuts: tuple[Shortcut, ...] | None = None
    supplements: tuple[Supplement, ...] = ()


@dataclass(frozen=True, slots=True, eq=False)
class NativeObservation(_Immutable):
    """A platform-normalized accessibility graph for shared presentation passes."""

    backend: Backend
    session: str
    id: str
    interval_ns: tuple[int, int]
    roots: tuple[NodeId, ...]
    nodes: Mapping[NodeId, NativeNode]
    coordinate_spaces: Mapping[SpaceId, CoordinateSpace] = field(default_factory=dict)
    issues: tuple[ReadIssue, ...] = ()
