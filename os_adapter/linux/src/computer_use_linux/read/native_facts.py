"""Bounded AT-SPI facts. No control classification, projection or input here."""

from __future__ import annotations

import json
import math
import time
from collections import deque

from computer_use_linux.contracts.native_observation import diagnostic_result
from computer_use_linux.contracts.observation import stamp
from computer_use_linux.contracts.protocol import Failure, now

RELATIONS = ("children", "parent", "labelled-by", "label-for", "TableCell.table")
# Ownership links are observable but are not traversal edges. In particular a
# cell read must not silently expand its table and all the table's other cells.
FOLLOW_RELATIONS = (
    "children",
    "labelled-by",
    "label-for",
    "Selection.selectedChildren",
    "TableCell.rowHeaderCells",
    "TableCell.columnHeaderCells",
    "Table.cells",
    "Table.viewportCandidates",
)
TABLE_COUNTS = {
    "Table.nRows": ("Table", "get_n_rows"),
    "Table.nColumns": ("Table", "get_n_columns"),
    "Selection.nSelectedChildren": ("Selection", "get_n_selected_children"),
}
STATES = dict(
    enabled="ENABLED",
    sensitive="SENSITIVE",
    selected="SELECTED",
    selectable="SELECTABLE",
    expanded="EXPANDED",
    focused="FOCUSED",
    modal="MODAL",
    editable="EDITABLE",
    focusable="FOCUSABLE",
    readOnly="READ_ONLY",
    showing="SHOWING",
    visible="VISIBLE",
    checked="CHECKED",
    indeterminate="INDETERMINATE",
    multiLine="MULTI_LINE",
)
ATTRIBUTES = dict(
    role="get_role_name",
    name="get_name",
    description="get_description",
    accessibleId="get_accessible_id",
    attributes="get_attributes",
    interfaces="get_interfaces",
)
VALUE_NUMBERS = {
    "Value.currentValue": "get_current_value",
    "Value.minimumValue": "get_minimum_value",
    "Value.maximumValue": "get_maximum_value",
    "Value.minimumIncrement": "get_minimum_increment",
}
MAX_COUNT = 9007199254740991
STRING_LIMIT = 16384
WIRE_BUDGET = 2 * 1024 * 1024
_NO_RAW_RESULT = object()


class FieldUnavailable(Exception):
    def __init__(self, state, detail="", *, raw_result=_NO_RAW_RESULT):
        self.state, self.detail, self.raw_result = state, detail, raw_result


def failure_status(error):
    if isinstance(error, Failure) and error.code in ("cancelled", "deadline_exceeded"):
        raise error
    if isinstance(error, FieldUnavailable):
        result = dict(state=error.state, **(dict(detail=error.detail) if error.detail else {}))
        if error.raw_result is not _NO_RAW_RESULT:
            result["rawResult"] = diagnostic_result(error.raw_result)
        return result
    return dict(state="error", detail=(str(error) or type(error).__name__)[:4096])


def record_gap(failures, detail, raw_result):
    """Retain one acquired relation result alongside its existing partial gap."""
    if failures is not None:
        failures.append(failure_status(FieldUnavailable("partial", detail, raw_result=raw_result)))


class NativeFacts:
    def __init__(self, ax, store, session):
        self.ax, self.store, self.session = ax, store, session
        self.until = 0

    def check(self):
        self.ax.ctx.check()
        if time.monotonic() >= self.until:
            raise FieldUnavailable("timeout", "Native collection budget exhausted.")

    def call(self, interface, method, obj, *args):
        self.check()
        fn = getattr(getattr(self.ax.A, interface, None), method, None)
        if fn is None:
            raise FieldUnavailable("unsupported", "This AT-SPI library does not expose " + method + ".")
        return self.ax.call(fn, obj, *args)

    def cached(self, cache, name, read):
        if name not in cache:
            try:
                cache[name] = read()
            except (FieldUnavailable, self.ax.GLib.Error) as error:
                cache[name] = error
        value = cache[name]
        if isinstance(value, Exception):
            raise value
        return value

    def interfaces(self, obj, cache):
        def read():
            value = self.call("Accessible", "get_interfaces", obj)
            if (
                not isinstance(value, (list, tuple))
                or len(value) > 64
                or any(not isinstance(v, str) or not v or len(v) > 1024 for v in value)
            ):
                raise FieldUnavailable("error", "Invalid native interface list.", raw_result=value)
            return list(value)

        return self.cached(cache, "interfaces", read)

    def require_interface(self, obj, cache, interface):
        if interface not in self.interfaces(obj, cache):
            raise FieldUnavailable("unsupported")

    def text(self, obj, cache):
        self.require_interface(obj, cache, "Text")
        role = self.cached(cache, "role", lambda: self.call("Accessible", "get_role_name", obj))
        if role == "password text":
            raise FieldUnavailable("unsupported", "Protected text is not captured.")

        def read():
            count = self.call("Text", "get_character_count", obj)
            # Exact types preserve scalar validation and identity contracts.
            # pylint: disable-next=unidiomatic-typecheck
            if type(count) is not int or not 0 <= count <= MAX_COUNT:
                raise FieldUnavailable("error", "Invalid native character count.", raw_result=count)
            end = min(count, 262144)
            value = self.call("Text", "get_text", obj, 0, end)
            if not isinstance(value, str):
                raise FieldUnavailable("error", "Native Text returned no string.", raw_result=value)
            after = self.call("Text", "get_character_count", obj)
            # pylint: disable-next=unidiomatic-typecheck
            if type(after) is not int or not 0 <= after <= MAX_COUNT:
                raise FieldUnavailable(
                    "error",
                    "Invalid native character count after reading text.",
                    raw_result=dict(text=value, characterCountBefore=count, characterCountAfter=after),
                )
            # Native offsets belong to the provider's Text interface. They
            # are not Python string indices (UTF-16 providers expose a larger
            # count for astral characters). Read the declared range rather
            # than comparing unlike units or switching on the toolkit name.
            complete = end == count and after == count
            detail = (
                (
                    f"Native Text covered offsets 0..{end} of {count}; current count {after}. "
                    "No complete immutable capture."
                )
                if not complete
                else ""
            )
            return value, complete, detail

        return self.cached(cache, "text", read)

    def field(self, obj, name, cache):
        if name == "interfaces":
            return self.interfaces(obj, cache)
        if name in ATTRIBUTES:
            value = self.cached(cache, name, lambda: self.call("Accessible", ATTRIBUTES[name], obj))
            if value is not None and name != "attributes" and not isinstance(value, str):
                raise FieldUnavailable("error", "Native string field returned a different type.", raw_result=value)
            invalid_name = (
                True
                if (
                    name == "attributes"
                    and value is not None
                    and (
                        not isinstance(value, dict)
                        or len(value) > 128
                        or any(not isinstance(k, str) or not k or not isinstance(v, str) for k, v in value.items())
                        or sum(len(k) + len(v) for k, v in value.items()) > 65536
                    )
                )
                else False
            )
            if invalid_name:
                raise FieldUnavailable(
                    "error", "Native attributes exceed the supported dictionary budget.", raw_result=value
                )
            return value
        if name in STATES:
            state = getattr(self.ax.A.StateType, STATES[name], None)
            if state is None:
                raise FieldUnavailable("unsupported")
            states = self.cached(cache, "states", lambda: self.call("Accessible", "get_state_set", obj))
            if states is None:
                raise FieldUnavailable("error", "Native state set is unavailable.", raw_result=states)
            return bool(states.contains(state))
        if name in TABLE_COUNTS:
            interface, method = TABLE_COUNTS[name]
            self.require_interface(obj, cache, interface)
            value = self.call(interface, method, obj)
            # pylint: disable-next=unidiomatic-typecheck
            if type(value) is not int or not 0 <= value <= MAX_COUNT:
                raise FieldUnavailable("error", "Invalid native table or selection count.", raw_result=value)
            return value
        if name == "TableCell.rowColumnSpan":
            self.require_interface(obj, cache, "TableCell")
            value = self.call("TableCell", "get_row_column_span", obj)
            # GI returns four out parameters; there is no success boolean.
            # Negative indices and zero spans are provider facts, not grounds
            # to invent a cell position. Python decides their presentation.
            if (
                not isinstance(value, (list, tuple))
                or len(value) != 4
                # pylint: disable-next=unidiomatic-typecheck
                or any(type(v) is not int or not -MAX_COUNT <= v <= MAX_COUNT for v in value)
            ):
                raise FieldUnavailable("error", "Invalid native row/column span result.", raw_result=value)
            return dict(zip(("row", "column", "rowSpan", "columnSpan"), value))
        if name == "Component.extents":
            self.require_interface(obj, cache, "Component")
            screen = getattr(getattr(self.ax.A, "CoordType", None), "SCREEN", None)
            if screen is None:
                raise FieldUnavailable("unsupported", "This AT-SPI library does not expose screen coordinates.")
            value = self.call("Component", "get_extents", obj, screen)
            if value is None:
                return None
            # AtspiRect consists of four signed gint values. Preserve zero
            # dimensions and negative coordinates; visibility and usability
            # remain separate native facts for consumers to interpret.
            rect = {key: getattr(value, key, None) for key in ("x", "y", "width", "height")}
            # pylint: disable-next=unidiomatic-typecheck
            if any(type(v) is not int or not -(2**31) <= v < 2**31 for v in rect.values()):
                raise FieldUnavailable(
                    "error",
                    "Invalid native Component extents.",
                    raw_result=(value if isinstance(value, (dict, list, tuple, str, int, float, bool)) else rect),
                )
            return rect
        if name in VALUE_NUMBERS:
            self.require_interface(obj, cache, "Value")
            value = self.call("Value", VALUE_NUMBERS[name], obj)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise FieldUnavailable("error", "Invalid native numeric value.", raw_result=value)
            return value
        if name == "Value.text":
            self.require_interface(obj, cache, "Value")
            value = self.call("Value", "get_text", obj)
            if value is not None and not isinstance(value, str):
                raise FieldUnavailable("error", "Native Value text returned a different type.", raw_result=value)
            return value
        if name == "Text.text":
            return self.text(obj, cache)[0]
        if name in ("Text.caretOffset", "Text.characterCount"):
            self.require_interface(obj, cache, "Text")
            method = "get_caret_offset" if name == "Text.caretOffset" else "get_character_count"
            value = self.call("Text", method, obj)
            # pylint: disable-next=unidiomatic-typecheck
            if type(value) is not int or not 0 <= value <= MAX_COUNT:
                raise FieldUnavailable("error", "Invalid native text offset or count.", raw_result=value)
            return value
        if name == "Text.selections":
            self.require_interface(obj, cache, "Text")
            count = self.call("Text", "get_n_selections", obj)
            # pylint: disable-next=unidiomatic-typecheck
            if type(count) is not int or not 0 <= count <= 64:
                raise FieldUnavailable(
                    "error", "Native selection count exceeds the supported query budget.", raw_result=count
                )
            ranges = []
            for index in range(count):
                bounds = self.call("Text", "get_selection", obj, index)
                start, end = getattr(bounds, "start_offset", None), getattr(bounds, "end_offset", None)
                # pylint: disable-next=unidiomatic-typecheck
                if type(start) is not int or type(end) is not int or not 0 <= start <= end <= MAX_COUNT:
                    raw = (
                        bounds
                        if bounds is None or isinstance(bounds, (dict, list, tuple, str, int, float, bool))
                        else dict(start_offset=start, end_offset=end)
                    )
                    raise FieldUnavailable(
                        "error",
                        "Invalid native selection offsets.",
                        raw_result=dict(selections=ranges, index=index, result=raw),
                    )
                ranges.append(dict(startOffset=start, endOffset=end))
            return ranges
        raise FieldUnavailable("unsupported")

    def node(self, reference, fields):
        cache, values, statuses = {}, {}, {}
        fields = list(fields)
        try:
            self.check()
            states = self.ax.live(reference.obj)
            if states is not None:
                cache["states"] = states
        except (FieldUnavailable, Failure, self.ax.GLib.Error) as error:
            status = failure_status(error)
            return dict(
                node=reference.ref,
                requestedFields=list(fields),
                values={},
                fieldStatus={name: dict(status) for name in fields},
                actions=dict(state="unavailable", items=[], detail=status.get("detail", status["state"])),
            )
        for name in fields:
            try:
                self.check()
                value = self.field(reference.obj, name, cache)
                if value is None:
                    raise FieldUnavailable("absent", raw_result=None)
                if name == "Text.text":
                    whole, complete, detail = self.text(reference.obj, cache)
                    preview = whole[:4096]
                    if not complete or len(whole) > len(preview):
                        status = dict(
                            state="truncated", detail="Native Text preview; immutable capture pages use UTF-16 units."
                        )
                        if complete:
                            ref = self.store.text(whole)
                            if ref:
                                status.update(textRef=ref, totalUnits=len(whole.encode("utf-16-le")) // 2)
                        else:
                            status["detail"] = detail
                        statuses[name] = status
                    value = preview
                elif isinstance(value, str) and len(value) > STRING_LIMIT:
                    value = value[:STRING_LIMIT]
                    statuses[name] = dict(
                        state="truncated",
                        detail=f"Native string exceeds {STRING_LIMIT} characters; no complete capture.",
                    )
                values[name] = value
            except (FieldUnavailable, Failure, self.ax.GLib.Error) as error:
                statuses[name] = failure_status(error)
        # libatspi has already obtained this StateSet while validating the
        # object or answering individual state fields. Preserve the entire
        # returned enum sequence, including bits unknown to common semantics.
        # These are GI StateSet values, not fabricated D-Bus bitmap words.
        states = cache.get("states")
        get_states = getattr(states, "get_states", None)
        if callable(get_states) and "Accessible.stateSet" not in fields:
            fields.append("Accessible.stateSet")
            try:
                self.check()
                raw_states = self.ax.call(get_states)
                if not isinstance(raw_states, (list, tuple)) or len(raw_states) > 4096:
                    raise FieldUnavailable("error", "Invalid native StateSet enumeration.", raw_result=raw_states)
                try:
                    values["Accessible.stateSet"] = [int(state) for state in raw_states]
                except (TypeError, ValueError) as error:
                    raise FieldUnavailable("error", "Invalid native StateSet member.", raw_result=raw_states) from error
            except (FieldUnavailable, Failure, self.ax.GLib.Error, TypeError, ValueError) as error:
                statuses["Accessible.stateSet"] = failure_status(error)
        return dict(
            node=reference.ref,
            requestedFields=list(fields),
            values=values,
            fieldStatus=statuses,
            actions=self.actions(reference.obj, cache),
        )

    def action_catalogue(self, obj, ctx):
        """The same raw action facts for a read batch or an input contract."""
        self.until = min(time.monotonic() + 5, ctx.deadline - 0.1)
        return self.actions(obj, {})

    def actions(self, obj, cache):
        items, total, description_budget = [], None, 65536
        try:
            self.check()
            if "Action" not in self.interfaces(obj, cache):
                return dict(state="complete", items=[], total=0)
            total = self.call("Action", "get_n_actions", obj)
            # pylint: disable-next=unidiomatic-typecheck
            if type(total) is not int or not 0 <= total <= MAX_COUNT:
                rejected = total
                total = None
                raise FieldUnavailable("error", "Invalid native action count.", raw_result=rejected)
            for index in range(min(128, total)):
                name = self.call("Action", "get_action_name", obj, index)
                if not isinstance(name, str) or not name.strip() or len(name) > 4096:
                    raise FieldUnavailable(
                        "error", "Native action has no usable exact name.", raw_result=dict(index=index, name=name)
                    )
                item = dict(name=name)
                try:
                    description = self.call("Action", "get_action_description", obj, index)
                    if description is None:
                        item["descriptionStatus"] = failure_status(FieldUnavailable("absent", raw_result=None))
                    elif not isinstance(description, str):
                        raise FieldUnavailable(
                            "error", "Native action description returned a different type.", raw_result=description
                        )
                    elif len(description) > min(STRING_LIMIT, description_budget):
                        item.update(
                            description=description[: min(STRING_LIMIT, description_budget)],
                            descriptionStatus=dict(
                                state="truncated", detail="Action description exceeds this collection budget."
                            ),
                        )
                    else:
                        item["description"] = description
                    description_budget -= len(item.get("description", ""))
                except (FieldUnavailable, Failure, self.ax.GLib.Error) as error:
                    item["descriptionStatus"] = failure_status(error)
                items.append(item)
            return dict(state="complete" if len(items) == total else "partial", items=items, total=total)
        except (FieldUnavailable, Failure, self.ax.GLib.Error) as error:
            status = failure_status(error)
            return dict(
                state="partial" if items or total is not None else "unavailable",
                items=items,
                **(dict(total=total) if total is not None else {}),
                detail=status.get("detail", status["state"]),
                **(dict(failure=status) if "rawResult" in status else {}),
            )

    def indexed_relation(self, obj, interface, count_method, item_method, limit, *, failures=None):
        total = self.call(interface, count_method, obj)
        # pylint: disable-next=unidiomatic-typecheck
        if type(total) is not int or not 0 <= total <= MAX_COUNT:
            label = "child" if interface == "Accessible" else "selected child"
            raise FieldUnavailable("error", "Invalid native " + label + " count.", raw_result=total)
        objects = []
        for index in range(min(total, limit)):
            try:
                child = self.call(interface, item_method, obj, index)
            except (FieldUnavailable, Failure, self.ax.GLib.Error) as error:
                status = failure_status(error)
                if failures is not None:
                    failures.append(status)
                return objects, total, "Relation prefix incomplete: " + status.get("detail", status["state"])
            if child is None:
                record_gap(failures, "Native relation target is unavailable.", dict(index=index, result=None))
                return objects, total, f"Native relation target {index} is unavailable."
            objects.append(child)
        try:
            after = self.call(interface, count_method, obj)
        except (FieldUnavailable, Failure, self.ax.GLib.Error) as error:
            status = failure_status(error)
            if failures is not None:
                failures.append(status)
            return objects, total, "Relation count could not be rechecked: " + status.get("detail", status["state"])
        # pylint: disable-next=unidiomatic-typecheck
        if type(after) is not int or after != total:
            record_gap(
                failures,
                "Native relation target count changed during collection.",
                dict(countBefore=total, countAfter=after),
            )
            return objects, total, "Native relation target count changed during collection."
        return objects, total, ""

    def related(self, obj, relation_property, limit, *, failures=None):
        """Read a native prefix. Holes never masquerade as a complete list."""
        if relation_property == "parent":
            parent = self.call("Accessible", "get_parent", obj)
            return ([parent] if parent is not None else []), (1 if parent is not None else 0), ""
        if relation_property == "children":
            return self.indexed_relation(
                obj, "Accessible", "get_child_count", "get_child_at_index", limit, failures=failures
            )
        if relation_property == "Selection.selectedChildren":
            self.require_interface(obj, {}, "Selection")
            return self.indexed_relation(
                obj, "Selection", "get_n_selected_children", "get_selected_child", limit, failures=failures
            )
        if relation_property.startswith("TableCell."):
            self.require_interface(obj, {}, "TableCell")
            if relation_property == "TableCell.table":
                table = self.call("TableCell", "get_table", obj)
                return ([table] if table is not None else []), (1 if table is not None else 0), ""
            method = {
                "TableCell.rowHeaderCells": "get_row_header_cells",
                "TableCell.columnHeaderCells": "get_column_header_cells",
            }.get(relation_property)
            if method is None:
                raise FieldUnavailable("unsupported")
            headers = self.call("TableCell", method, obj)
            if headers is None:
                raise FieldUnavailable("absent", raw_result=None)
            if not isinstance(headers, (list, tuple)) or len(headers) > MAX_COUNT:
                raise FieldUnavailable("error", "Invalid native table header list.", raw_result=headers)
            objects = []
            for index in range(min(len(headers), limit)):
                self.check()
                if headers[index] is None:
                    record_gap(failures, "Native table header is unavailable.", dict(index=index, result=None))
                    return objects, len(headers), f"Native table header {index} is unavailable."
                objects.append(headers[index])
            return objects, len(headers), ""
        if relation_property not in ("labelled-by", "label-for"):
            raise FieldUnavailable("unsupported")
        kind = getattr(
            self.ax.A.RelationType, "LABELLED_BY" if relation_property == "labelled-by" else "LABEL_FOR", None
        )
        if kind is None:
            raise FieldUnavailable("unsupported")
        relations = self.call("Accessible", "get_relation_set", obj)
        if not isinstance(relations, (list, tuple)) or len(relations) > 128:
            raise FieldUnavailable("error", "Invalid native relation set.", raw_result=relations)
        selected = [r for r in relations if r.get_relation_type() == kind]
        counts = [r.get_n_targets() for r in selected]
        # pylint: disable-next=unidiomatic-typecheck
        if any(type(n) is not int or not 0 <= n <= MAX_COUNT for n in counts) or sum(counts) > MAX_COUNT:
            raise FieldUnavailable("error", "Invalid native relation target count.", raw_result=counts)
        total, objects = sum(counts), []
        for relation, count in zip(selected, counts):
            for index in range(min(count, max(0, limit - len(objects)))):
                self.check()
                try:
                    obj = self.ax.call(relation.get_target, index)
                except (FieldUnavailable, Failure, self.ax.GLib.Error) as error:
                    status = failure_status(error)
                    if failures is not None:
                        failures.append(status)
                    return objects, total, "Relation prefix incomplete: " + status.get("detail", status["state"])
                if obj is None:
                    record_gap(failures, "Native relation target is unavailable.", dict(index=index, result=None))
                    return objects, total, "Native relation target is unavailable."
                objects.append(obj)
        return objects, total, ""

    def table_dimensions(self, obj):
        shape = tuple(self.call("Table", method, obj) for method in ("get_n_rows", "get_n_columns"))
        # pylint: disable-next=unidiomatic-typecheck
        if any(type(n) is not int or not 0 <= n <= 2147483647 for n in shape) or shape[0] * shape[1] > MAX_COUNT:
            raise FieldUnavailable("error", "Invalid native table dimensions.", raw_result=shape)
        return shape

    def table_cells(self, obj, limit, offset=0, *, failures=None):
        """A bounded row-major Table query, independent of native Children.

        Positions, including repeated references for merged cells, are retained.
        This is neither a viewport query nor proof that every slot is available.
        """
        self.require_interface(obj, {}, "Table")

        shape = self.table_dimensions(obj)
        total, objects, gap = shape[0] * shape[1], [], ""
        try:
            for index in range(offset, min(total, offset + limit)):
                row, column = divmod(index, shape[1])
                cell = self.call("Table", "get_accessible_at", obj, row, column)
                if cell is None:
                    gap = f"Native table cell at row {row}, column {column} is unavailable."
                    record_gap(failures, gap, dict(row=row, column=column, result=None))
                    break
                objects.append(cell)
            current_shape = self.table_dimensions(obj)
            if current_shape != shape:
                gap = "Native table dimensions changed during collection."
                record_gap(failures, gap, dict(dimensionsBefore=shape, dimensionsAfter=current_shape))
        except (FieldUnavailable, Failure, self.ax.GLib.Error) as error:
            status = failure_status(error)
            if failures is not None:
                failures.append(status)
            gap = "Table coordinate prefix incomplete: " + status.get("detail", status["state"])
        return objects, total, gap, shape

    def relation(self, node, relation_property, *, limit=128, cursor=None, retain=None):
        base = dict(source=node.ref, property=relation_property, targets=[], certainty="confirmed")
        failures = []
        page = None
        if cursor is not None:
            page = self.store.pages.get(cursor)
            if page is None or (page.node, page.property) != (node.ref, relation_property):
                raise Failure("Relation continuation expired or belongs to another target.", "snapshot_expired")
        offset = page.offset if page else 0
        coordinates = relation_property in ("Table.viewportCandidates", "Table.cells")
        try:
            if not coordinates and offset + limit > 8192:
                raise FieldUnavailable("error", "Relation prefix verification exceeds 8192 targets.")
            if relation_property == "Table.viewportCandidates":
                from computer_use_linux.read.native_table_viewport import TableViewport

                objects, total, gap, basis, detail = TableViewport(self).read(node.obj, limit, offset)
                base["detail"] = detail
            elif relation_property == "Table.cells":
                objects, total, gap, basis = self.table_cells(node.obj, limit, offset, failures=failures)
                # Equal products are not equal tables: 3x2 -> 2x3 changes every
                # coordinate after the first row even if proxies are reused.
                base["detail"] = (
                    f"Row-major Table coordinates: {basis[0]} rows, {basis[1]} columns. "
                    "One target per coordinate; targets may repeat."
                )
            else:
                objects, total, gap = self.related(node.obj, relation_property, offset + limit, failures=failures)
                keys = [self.ax.native_key(obj) for obj in objects]
            if coordinates:
                # Table getters address row/column slots. A provider may return
                # fresh objects for those slots on every read. Continue the
                # coordinate range, not the old object sequence; old element
                # references remain bound to their original native objects.
                if page and (page.total != total or page.coordinate_basis != basis):
                    raise Failure("Native table coordinate space changed since the observed page.", "snapshot_expired")
                base["detail"] += " Live coordinate page; values and native objects may change between reads."
            else:
                invalid_page = (
                    True
                    if page and (page.total != total or tuple(keys[:offset]) != page.prefix or len(keys) < offset)
                    else False
                )
                if invalid_page:
                    raise Failure("Native relation changed since the observed page.", "snapshot_expired")
                objects = objects[offset:]
            targets = [self.store.keep(node.surface, obj) for obj in objects]
            if retain is not None:
                retain.update(targets)
            base.update(
                targets=targets,
                state="complete" if len(objects) == total and offset == 0 and not gap else "partial",
                total=total,
            )
            if offset:
                base["offset"] = offset
            if not objects and not total and not gap:
                base["state"] = "absent" if relation_property in ("parent", "TableCell.table") else "complete"
            if gap:
                base["detail"] = (base.get("detail", "") + (" " if "detail" in base else "") + gap)[:4096]
                if failures:
                    base["failures"] = failures
            elif offset + len(objects) < total:
                if coordinates:
                    base["cursor"] = self.store.coordinate_page(
                        node.ref, relation_property, basis, total, offset + len(objects)
                    )
                elif len(keys) < 8192:
                    base["cursor"] = self.store.page(node.ref, relation_property, keys, total)
            return base
        except (FieldUnavailable, self.ax.GLib.Error) as error:
            status = failure_status(error)
            return dict(
                base,
                state=status["state"],
                certainty="unknown",
                **(dict(detail=status["detail"]) if status.get("detail") else {}),
                **(dict(failure=status) if "rawResult" in status else {}),
            )

    def collect(self, target, query, ctx):
        start = int(now())
        self.until = min(time.monotonic() + 5, ctx.deadline - 0.1)
        root = self.store.get(target["node"], target["surface"])
        nodes, relations, seen, unread, reasons = [], [], set(), set(), []
        edge_count, wire_size = 0, 0
        pending = deque([(root.ref, 0)])
        properties = list(RELATIONS) + [p for p in query.get("followRelations", []) if p not in RELATIONS]
        followed = set(("children", "labelled-by", "label-for")) | set(query.get("followRelations", []))
        page = query.get("relationPage")
        if page is None and "childrenPage" in query:
            page = dict(property="children", **query["childrenPage"])
        if page is not None:
            properties = [page["property"]] + [p for p in properties if p != page["property"]]
        with self.store.retaining() as retained:
            retained.add(root.ref)
            while pending:
                ref, depth = pending.popleft()
                if ref in seen:
                    continue
                matches_nodes = (
                    True
                    if nodes
                    and (
                        len(nodes) >= query["maxNodes"]
                        or time.monotonic() >= self.until
                        or wire_size >= WIRE_BUDGET
                        or len(relations) + len(properties) > 16384
                    )
                    else False
                )
                if matches_nodes:
                    unread.add(ref)
                    continue
                node = self.store.get(ref, target["surface"])
                seen.add(ref)
                collected = self.node(node, query["fields"])
                nodes.append(collected)
                wire_size += len(json.dumps(collected, ensure_ascii=True))
                for property_name in properties:
                    # Global graph limits include relation-only references.
                    remaining = min(8000 - len(retained), 24000 - edge_count)
                    if remaining <= 0:
                        edge = dict(
                            source=ref,
                            property=property_name,
                            targets=[],
                            certainty="unknown",
                            state="partial",
                            detail="Native graph reference budget exhausted.",
                        )
                    else:
                        selected = (
                            page if page is not None and ref == root.ref and property_name == page["property"] else {}
                        )
                        edge = self.relation(
                            node,
                            property_name,
                            limit=min(remaining, selected.get("limit", 128)),
                            cursor=selected.get("cursor"),
                            retain=retained,
                        )
                    relations.append(edge)
                    edge_count += len(edge["targets"])
                    wire_size += len(json.dumps(edge, ensure_ascii=True))
                    unread.update(edge["targets"])
                    is_page = page is not None and ref == root.ref and property_name == page["property"]
                    if depth < query["maxDepth"] and (property_name in followed or is_page):
                        children = [(child, depth + 1) for child in edge["targets"] if child not in seen]
                        if property_name in ("Selection.selectedChildren", "Table.viewportCandidates") and not is_page:
                            # Selected objects can lie far beyond the bounded
                            # children prefix; observe them before that prefix.
                            pending.extendleft(reversed(children))
                        else:
                            pending.extend(children)
                if len(nodes) % 32 == 0:
                    self.ax.pump()
        unread.difference_update(seen)
        if unread:
            reasons.append("Related objects remain unread within this node/depth/time budget.")
        if time.monotonic() >= self.until:
            reasons.append("Native collection time budget exhausted.")
        if len(relations) + len(properties) > 16384:
            reasons.append("Native relation record budget exhausted.")
        if wire_size >= WIRE_BUDGET:
            reasons.append("Native collection wire budget exhausted.")
        return dict(
            kind="nodes",
            session=self.session,
            surface=target["surface"],
            platform="linux",
            observed=stamp(start),
            roots=[root.ref],
            nodes=nodes,
            requestedRelations=properties,
            relations=relations,
            unread=sorted(unread),
            stopReasons=reasons,
        )

    def read_relation(self, target, query, ctx):
        start = int(now())
        self.until = min(time.monotonic() + 5, ctx.deadline - 0.1)
        node = self.store.get(target["node"], target["surface"])
        with self.store.retaining() as retained:
            retained.add(node.ref)
            relation = self.relation(
                node, query["property"], limit=query["limit"], cursor=query["cursor"], retain=retained
            )
        return dict(
            kind="relation",
            session=self.session,
            surface=target["surface"],
            platform="linux",
            observed=stamp(start),
            relation=relation,
        )
