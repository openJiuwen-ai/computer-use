"""Bounded AT-SPI Table/Component sampling, independent of input and selection.

AT-SPI has no required visible-cells property. Hit testing supplies native cell
anchors; adjacent Table coordinates extend those anchors while their Component
bounds intersect the table. These are viewport *candidates*, never a claim of
complete visibility, ownership, keyboard focus, or hit-test authorization.
"""

import math
from typing import Any, NamedTuple


class TableViewportRead(NamedTuple):
    """Sampled viewport cells with coverage limits and geometry provenance."""

    objects: list[Any]
    total: int
    gap: str
    geometry: tuple[Any, Any, Any]
    detail: str


class TableViewport:
    def __init__(self, facts):
        self.facts = facts
        self.ax = facts.ax
        self.call = facts.call
        self.lookups = 0
        self.cache = {}
        self.notes = []

    def same(self, left, right):
        return left is not None and right is not None and self.ax.native_key(left) == self.ax.native_key(right)

    def rect(self, obj):
        value = self.call("Component", "get_extents", obj, self.ax.atspi.CoordType.SCREEN)
        rect = tuple(getattr(value, key, None) for key in ("x", "y", "width", "height"))
        if (
            any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 2147483647 for v in rect)
            or rect[2] <= 0
            or rect[3] <= 0
        ):
            return None
        return rect

    @staticmethod
    def intersects(a, b):
        return (
            a is not None
            and b is not None
            and a[0] < b[0] + b[2]
            and b[0] < a[0] + a[2]
            and a[1] < b[1] + b[3]
            and b[1] < a[1] + a[3]
        )

    def position(self, cell, table, shape):
        interfaces = self.facts.interfaces(cell, {})
        if "TableCell" not in interfaces or "Component" not in interfaces:
            return None
        if not self.same(self.call("TableCell", "get_table", cell), table):
            return None
        # AT-SPI defines TableCell positions only for VISIBLE + SHOWING cells.
        # These flags are necessary for coordinates, but do not by themselves
        # establish viewport coverage: actual hits and bounds are still used.
        states = self.call("Accessible", "get_state_set", cell)
        if states is None or any(
            not states.contains(getattr(self.ax.atspi.StateType, name)) for name in ("VISIBLE", "SHOWING")
        ):
            return None
        span = self.call("TableCell", "get_row_column_span", cell)
        # Exact types preserve scalar validation and identity contracts.
        # pylint: disable-next=unidiomatic-typecheck
        if not isinstance(span, (list, tuple)) or len(span) != 4 or any(type(v) is not int for v in span):
            return None
        if not 0 <= span[0] < shape[0] or not 0 <= span[1] < shape[1] or not 1 <= span[2] <= shape[0] - span[0]:
            return None
        if not 1 <= span[3] <= shape[1] - span[1]:
            return None
        return tuple(span)

    def anchor(self, table, point, shape, frame):
        current, seen = table, set()
        # Providers may hit an intermediate container rather than the cell.
        # Do not search siblings or substitute a label for a missing native hit.
        for _ in range(8):
            key = self.ax.native_key(current)
            if key in seen:
                break
            seen.add(key)
            interfaces = self.facts.interfaces(current, {})
            if "TableCell" in interfaces:
                span = self.position(current, table, shape)
                return span if span is not None and self.intersects(self.rect(current), frame) else None
            if "Component" not in interfaces:
                break
            child = self.call("Component", "get_accessible_at_point", current, *point, self.ax.atspi.CoordType.SCREEN)
            if child is None or self.same(child, current):
                break
            current = child
        # Some providers return a text descendant inside the actual TableCell.
        # Native parentage plus TableCell.get_table identifies that cell; no
        # role/name or geometric nearest-neighbour inference is used.
        seen = set()
        for _ in range(8):
            key = self.ax.native_key(current)
            if key in seen or self.same(current, table):
                break
            seen.add(key)
            if "TableCell" in self.facts.interfaces(current, {}):
                span = self.position(current, table, shape)
                return span if span is not None and self.intersects(self.rect(current), frame) else None
            current = self.call("Accessible", "get_parent", current)
            if current is None:
                break
        return None

    def cell(self, table, row, column):
        key = row, column
        if key not in self.cache:
            self.cache[key] = self.call("Table", "get_accessible_at", table, row, column)
        return self.cache[key]

    def extend(self, table, coordinate, direction, shape, frame):
        row, column = coordinate
        while self.lookups < 64:
            r, c = row + direction[0], column + direction[1]
            if not (0 <= r < shape[0] and 0 <= c < shape[1]):
                return row, column
            self.lookups += 1
            cell = self.cell(table, r, c)
            span = self.position(cell, table, shape) if cell is not None else None
            if span is None or not (span[0] <= r < span[0] + span[2] and span[1] <= c < span[1] + span[3]):
                self.notes.append("Adjacent native cell position is unavailable or inconsistent.")
                return row, column
            rect = self.rect(cell)
            if rect is None:
                self.notes.append("Adjacent native cell geometry is unavailable.")
                return row, column
            if not self.intersects(rect, frame):
                return row, column
            row, column = r, c
        self.notes.append("Viewport boundary sampling reached its 64-cell budget.")
        return row, column

    def read(self, table, limit, offset=0) -> TableViewportRead:
        from computer_use_linux.contracts.protocol import Failure
        from computer_use_linux.read.native_facts import FieldUnavailable, failure_status

        self.facts.require_interface(table, {}, "Table")
        self.facts.require_interface(table, {}, "Component")
        shape = self.facts.table_dimensions(table)
        frame = self.rect(table)
        if frame is None:
            raise FieldUnavailable("absent", "Native table viewport geometry is unavailable.")
        if not all(shape):
            return TableViewportRead([], 0, "", (shape, frame, None), "Native table has no coordinate slots.")

        xs = sorted(set((math.ceil(frame[0]), math.floor(frame[0] + frame[2] / 2), math.ceil(frame[0] + frame[2]) - 1)))
        ys = sorted(set((math.ceil(frame[1]), math.floor(frame[1] + frame[3] / 2), math.ceil(frame[1] + frame[3]) - 1)))
        anchors = []
        for y in ys:
            for x in xs:
                if frame[0] <= x < frame[0] + frame[2] and frame[1] <= y < frame[1] + frame[3]:
                    span = self.anchor(table, (x, y), shape, frame)
                    if span is not None:
                        anchors.append(span)
        if not anchors:
            raise FieldUnavailable(
                "absent", "Native viewport samples did not identify a TableCell; coverage is unknown."
            )

        # Anchor extents include merged cells. Each direction is explored along
        # an actually sampled row/column; no uniform row height is assumed.
        first_row = min(anchors, key=lambda s: s[0])
        last_row = max(anchors, key=lambda s: s[0] + s[2])
        first_col = min(anchors, key=lambda s: s[1])
        last_col = max(anchors, key=lambda s: s[1] + s[3])
        top = self.extend(table, first_row[:2], (-1, 0), shape, frame)[0]
        bottom = self.extend(table, (last_row[0] + last_row[2] - 1, last_row[1]), (1, 0), shape, frame)[0]
        left = self.extend(table, first_col[:2], (0, -1), shape, frame)[1]
        right = self.extend(table, (last_col[0], last_col[1] + last_col[3] - 1), (0, 1), shape, frame)[1]
        region = top, left, bottom, right
        total = (bottom - top + 1) * (right - left + 1)
        detail = (
            f"Viewport candidate coordinates: rows {top}..{bottom}, columns {left}..{right} (zero-based, row-major). "
            "Native Component samples and Table positions; full viewport coverage and occlusion are unknown. "
            "One target per coordinate; targets may repeat."
        )
        if self.notes:
            detail += " " + " ".join(dict.fromkeys(self.notes))
        objects, gap = [], ""
        try:
            for index in range(offset, min(total, offset + limit)):
                row, column = divmod(index, right - left + 1)
                cell = self.cell(table, top + row, left + column)
                if cell is None:
                    gap = "A sampled coordinate returned no native object."
                    break
                objects.append(cell)
            if self.facts.table_dimensions(table) != shape or self.rect(table) != frame:
                gap = "Native table geometry or dimensions changed during sampling."
        except (FieldUnavailable, Failure, self.ax.glib.Error) as error:
            status = failure_status(error)
            gap = "Viewport candidate prefix incomplete: " + status.get("detail", status["state"])
        return TableViewportRead(objects, total, gap, (shape, frame, region), detail)
