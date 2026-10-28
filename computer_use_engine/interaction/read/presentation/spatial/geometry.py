# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read-only comparable bounds for local presentation decisions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import SnapshotFacts
    from computer_use.protocol.observation import CoordinateSpace, Geometry, NativeObservation, ReadIssue
    from computer_use.protocol.observation.model import FieldPath


@dataclass(frozen=True, slots=True)
class Box:
    """A complete positive rectangle in one comparable, downward-Y space.

    Attributes:
        space: Declared coordinate-space identity after any exact transformation.
        x: Left edge in the space's own units.
        y: Top edge after normalizing the space's Y direction.
        width: Positive horizontal extent.
        height: Positive vertical extent.
    """

    space: str
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if (
            not self.space
            or not all(_finite(number) for number in (self.x, self.y, self.width, self.height))
            or self.width <= 0
            or self.height <= 0
            or self.area <= 0
            or not all(math.isfinite(number) for number in (self.right, self.bottom, self.area))
        ):
            raise ValueError("A presentation box requires finite positive bounds and a declared space")

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2

    @property
    def area(self) -> float:
        return self.width * self.height

    def comparable(self, other: Box) -> bool:
        """Return whether both bounds use the same declared comparison space."""
        return self.space == other.space

    def contains(self, other: Box) -> bool:
        """Return whether this box fully contains the other comparable box."""
        return (
            self.comparable(other)
            and self.x <= other.x
            and self.y <= other.y
            and self.right >= other.right
            and self.bottom >= other.bottom
        )

    def intersection_area(self, other: Box) -> float:
        """Return shared area, or zero for disjoint or incomparable boxes."""
        if not self.comparable(other):
            return 0.0
        return max(0.0, min(self.right, other.right) - max(self.x, other.x)) * max(
            0.0, min(self.bottom, other.bottom) - max(self.y, other.y)
        )

    def overlap(self, other: Box) -> float:
        """Return shared area divided by the smaller box's area, from zero to one."""
        return self.intersection_area(other) / min(self.area, other.area)

    def horizontal_overlap(self, other: Box) -> float:
        """Return shared horizontal extent relative to the smaller width."""
        if not self.comparable(other):
            return 0.0
        return max(0.0, min(self.right, other.right) - max(self.x, other.x)) / min(self.width, other.width)

    def vertical_overlap(self, other: Box) -> float:
        """Return shared vertical extent relative to the smaller height."""
        if not self.comparable(other):
            return 0.0
        return max(0.0, min(self.bottom, other.bottom) - max(self.y, other.y)) / min(self.height, other.height)


@dataclass(frozen=True, slots=True)
class GeometryIndex:
    """Immutable comparison bounds with fully resolved declared coordinate paths."""

    boxes: Mapping[str, Box]

    def __post_init__(self) -> None:
        object.__setattr__(self, "boxes", MappingProxyType(dict(self.boxes)))


@dataclass(frozen=True)
class _IssuePaths:
    """Index failed field paths and their prefixes for bounded local lookups."""

    exact: frozenset[FieldPath]
    ancestors: frozenset[FieldPath]

    @classmethod
    def collect(cls, issues: tuple[ReadIssue, ...]) -> _IssuePaths:
        # A partial node collection reports missing coverage, not a failure of
        # every field on nodes that were actually returned. Field-, node- and
        # geometry-specific failures continue to constrain their exact scope.
        paths = frozenset(issue.path for issue in issues if not (issue.path == ("nodes",) and issue.state == "partial"))
        return cls(paths, frozenset(path[:end] for path in paths for end in range(len(path) + 1)))

    def affects(self, path: FieldPath) -> bool:
        return path in self.ancestors or any(path[:end] in self.exact for end in range(len(path) + 1))


def _finite(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _usable_space(identity: str, spaces: Mapping[str, CoordinateSpace], issues: _IssuePaths) -> bool:
    """Require the coordinate declaration used to compare local bounds."""
    space = spaces.get(identity)
    return (
        space is not None
        and space.y_direction in ("up", "down")
        and all(
            not issues.affects(("coordinate_spaces", identity, field))
            for field in ("unit", "origin", "owner", "y_direction")
        )
    )


def _bounds(geometry: Geometry) -> tuple[float, float, float, float] | None:
    """Accept complete numerical bounds while preserving unknown visibility."""
    values = geometry.x, geometry.y, geometry.width, geometry.height
    if geometry.valid is False or not all(_finite(value) for value in values):
        return None
    x, y, width, height = (float(value) for value in values)
    if (
        width <= 0
        or height <= 0
        or width * height <= 0
        or not all(math.isfinite(value) for value in (x + width, y + height, width * height))
    ):
        return None
    return x, y, width, height


def _transformed(
    bounds: tuple[float, float, float, float], matrix: tuple[float, float, float, float, float, float]
) -> tuple[float, float, float, float] | None:
    """Apply a declared affine transform that keeps rectangles axis aligned.

    The observation protocol defines ``x'=a*x+c*y+tx`` and
    ``y'=b*x+d*y+ty``. Axis swaps, reflections, scaling and translation preserve
    the exact rectangle; a general rotation or shear requires a richer shape.
    """
    if len(matrix) != 6 or not all(_finite(value) for value in matrix):
        return None
    a, b, c, d, tx, ty = matrix
    if not ((b == 0 and c == 0 and a != 0 and d != 0) or (a == 0 and d == 0 and b != 0 and c != 0)):
        return None
    x, y, width, height = bounds
    points = tuple(
        (a * px + c * py + tx, b * px + d * py + ty)
        for px, py in ((x, y), (x + width, y), (x, y + height), (x + width, y + height))
    )
    if not all(math.isfinite(value) for point in points for value in point):
        return None
    left, top = min(point[0] for point in points), min(point[1] for point in points)
    right, bottom = max(point[0] for point in points), max(point[1] for point in points)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0 or width * height <= 0 or not math.isfinite(width * height):
        return None
    return left, top, width, height


def _comparable_box(geometry: Geometry, spaces: Mapping[str, CoordinateSpace], issues: _IssuePaths) -> Box | None:
    """Resolve every declared parent transform before using a box's direction.

    A failed declared edge cannot make an intermediate local axis a display
    direction. Original rectangles remain available in the native observation.
    """
    bounds = _bounds(geometry)
    identity = geometry.space
    if bounds is None or not _usable_space(identity, spaces, issues):
        return None
    seen = {identity}
    while True:
        space = spaces[identity]
        parent = space.parent
        if parent is None and space.to_parent is None:
            break
        if (
            parent is None
            or space.to_parent is None
            or issues.affects(("coordinate_spaces", identity, "parent"))
            or issues.affects(("coordinate_spaces", identity, "to_parent"))
            or not _usable_space(parent, spaces, issues)
        ):
            return None
        if parent in seen:
            return None
        transformed = _transformed(bounds, space.to_parent)
        if transformed is None:
            return None
        bounds, identity = transformed, parent
        seen.add(identity)
    x, y, width, height = bounds
    if spaces[identity].y_direction == "up":
        y = -(y + height)
    return Box(identity, x, y, width, height)


def _visual_scope_exclusions(native: NativeObservation) -> frozenset[str]:
    """Exclude explicitly hidden scopes and their declared child branches.

    The source states and geometry remain unchanged. Candidate child relations
    do not establish ancestry, and missing geometry does not imply hiddenness.
    """
    excluded = {
        token
        for token, node in native.nodes.items()
        if node.states.offscreen is True or node.states.visible is False or node.states.showing is False
    }
    pending = list(excluded)
    while pending:
        source = native.nodes.get(pending.pop())
        if source is None:
            continue
        for relation in source.relations.values():
            if relation.kind != "children" or relation.evidence == "candidate":
                continue
            for target in relation.targets:
                if target not in excluded:
                    excluded.add(target)
                    pending.append(target)
    return frozenset(excluded)


def collect_geometry(snapshot: SnapshotFacts) -> GeometryIndex:
    """Collect reliable bounds without changing observed facts or source bindings.

    Follow complete declared paths of exact axis-aligned transforms. A missing
    or unsupported declared edge excludes that box from spatial inference.
    Independent spaces remain distinct even when they share units or origins.
    Native bounds and failure records remain unchanged in the snapshot.

    Args:
        snapshot: Captured native observation and its existing display aliases.

    Returns:
        Complete positive bounds keyed by display reference. Spatial consumers
        compare boxes only through their declared comparison-space identity.
    """
    native = snapshot.native
    issues = _IssuePaths.collect(native.issues)
    excluded = _visual_scope_exclusions(native)
    boxes: dict[str, Box] = {}
    for ref, token in snapshot.refs.items():
        node = native.nodes.get(token)
        if node is None or token in excluded or issues.affects(("nodes", token, "geometry", "bounds")):
            continue
        geometry = node.geometry.get("bounds")
        if geometry is None:
            continue
        box = _comparable_box(geometry, native.coordinate_spaces, issues)
        if box is not None:
            boxes[ref] = box
    return GeometryIndex(boxes)
