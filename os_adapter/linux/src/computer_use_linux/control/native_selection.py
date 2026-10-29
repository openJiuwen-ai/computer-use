# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Operation-local facts used by Python to select a native input recipient.

The driver preserves and rechecks facts; it never searches for an editor or
classifies a container. Text, focus, selection and labels are deliberately not
structural dependencies. The addressed input unit still checks live ownership
after these provider calls and immediately before mutation.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace

from computer_use_linux.contracts.protocol import Failure, fields, require, string
from computer_use_linux.read.native_facts import FieldUnavailable, failure_status

STRUCTURAL_FIELDS = frozenset(("role", "enabled", "sensitive", "editable", "readOnly", "focusable", "interfaces"))
STRUCTURAL_RELATIONS = frozenset(("children", "parent"))
MAX_FACTS = 8192
MAX_DEPENDENCIES = 512
MAX_BYTES = 8 * 1024 * 1024


class Changed(Failure):
    def __init__(self):
        super().__init__(
            "The structural facts used to select this input recipient changed.",
            "input_unavailable",
            target="input recipient",
        )


def _name(value):
    require(len(string(value)) <= 4096, "Selection reference or property is too long.")
    return value


def decode_selection_request(query):
    """Validate only the optional selection portion of an inputContext query."""
    require(isinstance(query, dict), "Invalid input selection query.")
    if "actuator" not in query and "basis" not in query:
        return None, []
    raw_actuator = query.get("actuator")
    scrolling = isinstance(raw_actuator, dict) and raw_actuator.get("relation") == "scrollPointer"
    actuator = fields(
        raw_actuator,
        ("node", "relation", "region") if scrolling else ("node", "relation"),
        ("contentPath",) if scrolling else (),
    )
    _name(actuator["node"])
    require(
        actuator["relation"] in ("containedEditor", "controlPart", "scrollPointer"),
        "Unsupported selected input relation.",
    )
    if scrolling:
        _name(actuator["region"])
        if "contentPath" in actuator:
            path = actuator["contentPath"]
            require(
                isinstance(path, list)
                and 2 <= len(path) <= 40
                and all(isinstance(node, str) and node for node in path)
                and len(set(path)) == len(path)
                and path[0] == actuator["node"],
                "A scroll content path must start at its hit and contain distinct nodes.",
            )
            for node in path:
                _name(node)
    basis = query.get("basis")
    require(
        isinstance(basis, list) and 1 <= len(basis) <= MAX_DEPENDENCIES,
        "A selected input member requires bounded native selection evidence.",
    )
    output, seen = [], set()
    for raw in basis:
        fields(raw, ("observed", "node", "fields", "actions", "relations"))
        observed, node = _name(raw["observed"]), _name(raw["node"])
        require((observed, node) not in seen, "Duplicate native selection source.")
        seen.add((observed, node))
        # Exact types preserve scalar validation and identity contracts.
        # pylint: disable-next=unidiomatic-typecheck
        require(type(raw["actions"]) is bool, "Invalid action dependency flag.")
        for key, allowed in (("fields", STRUCTURAL_FIELDS), ("relations", STRUCTURAL_RELATIONS)):
            names = raw[key]
            require(
                isinstance(names, list)
                and len(names) <= len(allowed)
                and all(isinstance(name, str) and name in allowed for name in names)
                and len(names) == len(set(names)),
                "Unsupported or duplicate structural dependency.",
            )
        require(raw["fields"] or raw["actions"] or raw["relations"], "Empty native selection dependency.")
        output.append(
            dict(
                observed=observed,
                node=node,
                fields=list(raw["fields"]),
                actions=raw["actions"],
                relations=list(raw["relations"]),
            )
        )
    require(len(json.dumps(output).encode("utf-8")) <= MAX_BYTES, "Selection request budget exhausted.")
    return dict(actuator), output


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


@dataclass(frozen=True)
class _Fact:
    data: str
    keys: tuple = ()
    objects: tuple = field(default=(), compare=False, repr=False)
    unavailable: bool = False


@dataclass(frozen=True)
class _Check:
    node: str
    obj: object = field(compare=False, repr=False)
    key: tuple
    fact: _Fact


@dataclass(frozen=True)
class SelectedReceiver:
    _owner: object = field(repr=False, compare=False)
    _operation: str
    _requested: tuple
    _actuator: tuple
    _basis: str
    _checks: tuple = field(repr=False)

    @property
    def requested(self):
        return dict(self._requested)

    @property
    def recipient(self):
        return dict(self._requested, node=dict(self._actuator)["node"])

    @property
    def actuator(self):
        result = dict(self._actuator)
        if "contentPath" in result:
            result["contentPath"] = list(result["contentPath"])
        return result

    @property
    def basis(self):
        return json.loads(self._basis)

    def check(self, ctx):
        self._owner.check(self, ctx)

    def check_scroll_content(self, hit, ctx):
        self._owner.check_scroll_content(self, hit, ctx)

    @property
    def signature(self):
        """Hashable route evidence; new observation IDs alone do not change it."""
        return self._requested, self._actuator, self._checks

    def equivalent(self, other):
        return (
            isinstance(other, SelectedReceiver)
            # The pipeline shares retained bindings with their owning service.
            # pylint: disable-next=protected-access
            and self._operation == other._operation
            # pylint: disable-next=protected-access
            and self._requested == other._requested
            # pylint: disable-next=protected-access
            and self._actuator == other._actuator
            # pylint: disable-next=protected-access
            and self._checks == other._checks
        )


class NativeSelection:
    def __init__(self, current, session, ax, store, facts):
        self.current, self.session, self.ax, self.store, self.facts = current, session, ax, store, facts
        self.operation = None
        self.snapshots, self.bytes = {}, 0
        self.closed = False

    def _operation(self):
        op = self.current()
        require(
            not self.closed and op is not None and op.writable and not op.closed,
            "Native selection evidence is outside an active write operation.",
            "stale_target",
        )
        if self.operation is None:
            self.operation = op.token
        require(self.operation == op.token, "Native selection evidence belongs to another operation.", "stale_target")
        return op

    @staticmethod
    def _scalar(name, value=None, status=None):
        if status is not None:
            state = status.get("state")
            require(isinstance(state, str), "Invalid structural field status.")
            return _Fact(_json(dict(state=state)), unavailable=state not in ("absent", "unsupported"))
        require(value is not None, "Uncollected structural field.")
        if name == "interfaces":
            require(
                isinstance(value, (list, tuple)) and len(value) <= 64 and all(isinstance(v, str) and v for v in value),
                "Invalid structural interface list.",
            )
            value = sorted(value)
        elif name == "role":
            require(isinstance(value, str), "Invalid structural role.")
        else:
            # pylint: disable-next=unidiomatic-typecheck
            require(type(value) is bool, "Invalid structural boolean.")
        return _Fact(_json(dict(value=value)))

    @staticmethod
    def _actions(value):
        state, items = value.get("state"), value.get("items")
        require(
            state in ("complete", "partial", "unavailable") and isinstance(items, list) and len(items) <= 128,
            "Invalid native action dependency.",
        )
        names = [item.get("name") for item in items]
        require(all(isinstance(name, str) and name for name in names), "Invalid native action name.")
        return _Fact(
            _json(dict(state=state, names=sorted(names), total=value.get("total"))), unavailable=state != "complete"
        )

    def _relation(self, value, objects):
        state = value.get("state", "error")
        return _Fact(
            _json(
                dict(
                    state=state,
                    certainty=value.get("certainty", "unknown"),
                    offset=value.get("offset", 0),
                    total=value.get("total"),
                )
            ),
            tuple(self.ax.native_key(obj) for obj in objects),
            tuple(objects),
            state not in ("complete", "partial", "absent", "unsupported") or value.get("certainty") != "confirmed",
        )

    def retain(self, batch):
        """Retain the exact returned facts; never replace them with a new sample."""
        op = self._operation()
        require(
            batch.get("session") == self.session
            and batch.get("platform") == "linux"
            and batch.get("surface") == op.target.get("surface"),
            "Selection evidence belongs to another session or surface.",
            "stale_target",
        )
        observed = _name(batch.get("observed", {}).get("id"))
        surface = batch["surface"]
        pending, retained = {}, set()

        def snapshot(ref):
            _name(ref)
            if ref not in pending:
                node = self.store.get(ref, surface)
                pending[ref] = dict(obj=node.obj, fields={}, relations={}, actions=None)
                retained.add(ref)
            return pending[ref]

        for node in batch.get("nodes", []):
            value = snapshot(node["node"])
            for name in node["requestedFields"]:
                if name in STRUCTURAL_FIELDS:
                    value["fields"][name] = self._scalar(name, node["values"].get(name), node["fieldStatus"].get(name))
            value["actions"] = self._actions(node["actions"])
        relations = batch.get("relations", [batch["relation"]] if "relation" in batch else [])
        for relation in relations:
            if relation["property"] not in STRUCTURAL_RELATIONS:
                continue
            value = snapshot(relation["source"])
            refs = relation["targets"]
            objects = [self.store.get(ref, surface).obj for ref in refs]
            retained.update(refs)
            value["relations"][relation["property"]] = self._relation(relation, objects)
        added = 0
        for ref, value in pending.items():
            require((observed, ref) not in self.snapshots, "Native collection identity was reused.")
            facts = [*value["fields"].values(), *value["relations"].values()]
            if value["actions"] is not None:
                facts.append(value["actions"])
            added += 512 + sum(len(fact.data.encode("utf-8")) + 64 * len(fact.objects) for fact in facts)
        require(
            len(self.snapshots) + len(pending) <= MAX_FACTS and self.bytes + added <= MAX_BYTES,
            "Operation selection evidence budget exhausted.",
            "observation_incomplete",
        )
        self.snapshots.update({(observed, ref): value for ref, value in pending.items()})
        self.bytes += added
        self.store.pins.update(retained)

    def select(self, target, actuator, basis):
        op = self._operation()
        actuator, basis = decode_selection_request(dict(actuator=actuator, basis=basis))
        require(
            not op.dispatched
            and target.get("node") is not None
            and target.get("capture") is None
            and (actuator["node"] != target["node"] or actuator["relation"] == "scrollPointer")
            and all(target.get(key) == value for key, value in op.target.items() if key != "node")
            and (op.target.get("node") is None or op.target == target),
            "Selected recipient is outside the original requested binding.",
            "stale_target",
        )
        self.store.get(target["node"], target["surface"])
        self.store.get(actuator["node"], target["surface"])
        if "region" in actuator:
            self.store.get(actuator["region"], target["surface"])
        checks = []
        for entry in basis:
            snapshot = self.snapshots.get((entry["observed"], entry["node"]))
            require(snapshot is not None, "Selection evidence was not retained by this operation.")
            self.store.get(entry["node"], target["surface"])
            selected = [("field", name, snapshot["fields"].get(name)) for name in entry["fields"]]
            if entry["actions"]:
                selected.append(("actions", "", snapshot["actions"]))
            selected.extend(("relation", name, snapshot["relations"].get(name)) for name in entry["relations"])
            for kind, name, fact in selected:
                require(fact is not None, "Selection dependency was not collected.")
                require(
                    not fact.unavailable, "Selection requires unavailable native evidence.", "observation_incomplete"
                )
                metadata = json.loads(fact.data) if kind == "relation" else {}
                key = (entry["node"], kind, name, metadata.get("offset", 0), len(fact.objects))
                checks.append(_Check(entry["node"], snapshot["obj"], key, fact))
        checks.sort(key=lambda check: check.key)
        path = actuator.get("contentPath", ())
        for index, node in enumerate(path):
            self.store.get(node, target["surface"])
            own = [check for check in checks if check.node == node]
            require(
                any(c.key[1:3] == ("field", "role") for c in own),
                "Scroll content needs the observed role of every path node.",
            )
            if index + 1 == len(path):
                continue
            parent_key = self.ax.native_key(self.store.get(path[index + 1], target["surface"]).obj)
            require(
                any(c.key[1] == "actions" for c in own), "Scroll content needs complete observed action declarations."
            )
            parents = [c for c in own if c.key[1:3] == ("relation", "parent")]
            require(
                parents
                and all(
                    c.fact.keys == (parent_key,)
                    and json.loads(c.fact.data) == dict(state="complete", certainty="confirmed", offset=0, total=1)
                    for c in parents
                ),
                "Scroll content needs its exact observed parent path.",
            )
            require(
                all(c.key[1] != "relation" or c.key[2] == "parent" for c in own),
                "Moving scroll content cannot replace other structural relationships.",
            )
        self.store.pins.update((target["node"], actuator["node"]))
        if "region" in actuator:
            self.store.pins.add(actuator["region"])
        self.store.pins.update(path)
        frozen_actuator = {key: tuple(value) if key == "contentPath" else value for key, value in actuator.items()}
        return SelectedReceiver(
            self,
            op.token,
            tuple(sorted(target.items())),
            tuple(sorted(frozen_actuator.items())),
            _json(basis),
            tuple(checks),
        )

    def _current(self, check):
        kind, name = check.key[1:3]
        if kind == "field":
            try:
                value = self.facts.field(check.obj, name, {})
                return self._scalar(name, value, dict(state="absent") if value is None else None)
            except (FieldUnavailable, self.ax.glib.Error) as error:
                return self._scalar(name, status=failure_status(error))
        if kind == "actions":
            return self._actions(self.facts.actions(check.obj, {}))
        metadata = json.loads(check.fact.data)
        offset, count = metadata["offset"], len(check.fact.objects)
        try:
            objects, total, gap = self.facts.related(check.obj, name, offset + count)
            actual = dict(
                state="complete" if offset == 0 and len(objects) == total and not gap else "partial",
                certainty="unknown" if gap else "confirmed",
                offset=offset,
                total=total,
            )
            matches_name = True if name == "parent" and not objects and total == 0 and not gap else False
            if matches_name:
                actual["state"] = "absent"
            return self._relation(actual, objects[offset:])
        except (FieldUnavailable, self.ax.glib.Error) as error:
            status = failure_status(error)
            return self._relation(dict(state=status["state"], certainty="unknown"), [])

    def check_scroll_content(self, selected, hit, ctx):
        """Compare a new item path with Python's already qualified prototype.

        Only identities below the fixed anchor may differ. This native unit
        interprets no roles/actions; all raw structural facts must still match.
        """
        self._begin_check(selected, ctx)
        path = selected.actuator.get("contentPath")
        require(
            selected.actuator["relation"] == "scrollPointer" and path,
            "This selection has no moving scroll content contract.",
        )
        current, substitutions, seen = hit, {}, set()
        for index, node in enumerate(path):
            ctx.check()
            self.ax.live(current)
            key = self.ax.native_key(current)
            require(key not in seen, "The scrolling content parent path became cyclic.", "input_unavailable")
            seen.add(key)
            if index + 1 == len(path):
                anchor = self.store.get(node, selected.requested["surface"]).obj
                require(
                    key == self.ax.native_key(anchor),
                    "The scrolling content left its fixed collection.",
                    "input_unavailable",
                )
                break
            substitutions[node] = current
            objects, total, gap = self.facts.related(current, "parent", 1)
            require(
                not gap and total == 1 and len(objects) == 1,
                "The scrolling content parent path is unavailable.",
                "input_unavailable",
            )
            current = objects[0]
        self.check(selected, ctx, substitutions=substitutions)

    def _begin_check(self, selected, ctx):
        op = self._operation()
        require(
            # pylint: disable-next=protected-access
            selected._owner is self and selected._operation == op.token,
            "Selected recipient belongs to another operation.",
            "stale_target",
        )
        self.facts.until = min(time.monotonic() + 5, ctx.deadline - 0.1)

    def check(self, selected, ctx, *, substitutions=None):
        self._begin_check(selected, ctx)
        substitutions = substitutions or {}
        if substitutions:
            require(
                selected.actuator["relation"] == "scrollPointer"
                and set(substitutions) == set(selected.actuator.get("contentPath", ())[:-1]),
                "Only the selected scrolling item path may change identities.",
            )
        replacement_keys = {
            self.ax.native_key(check.obj): self.ax.native_key(substitutions[check.node])
            # pylint: disable-next=protected-access
            for check in selected._checks
            if check.node in substitutions
        }
        seen = set()
        # pylint: disable-next=protected-access
        for check in selected._checks:
            ctx.check()
            obj = substitutions.get(check.node, check.obj)
            if check.node not in seen:
                if check.node not in substitutions:
                    retained = self.store.get(check.node, selected.requested["surface"])
                    require(
                        self.ax.native_key(retained.obj) == self.ax.native_key(check.obj),
                        "Retained structural object changed.",
                        "stale_target",
                    )
                self.ax.live(obj)
                seen.add(check.node)
            actual = self._current(replace(check, obj=obj))
            expected = replace(check.fact, keys=tuple(replacement_keys.get(key, key) for key in check.fact.keys))
            if actual != expected:
                if actual.unavailable:
                    raise Failure("Structural selection evidence cannot currently be read.", "observation_incomplete")
                raise Changed()

    def clear(self):
        self.snapshots.clear()
        self.bytes = 0
        self.closed = True
