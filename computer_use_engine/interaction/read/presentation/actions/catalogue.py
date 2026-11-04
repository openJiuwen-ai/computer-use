# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Complete native action evidence for source-preserving layout grouping."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
import json
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.actions.menu_mechanisms import MenuMechanism
    from computer_use.interaction.read.presentation.actions.control_raises import ControlRaise

from computer_use.interaction.read.presentation.compact import display_actions
from computer_use.interaction.read.presentation.compression.policy import secondary_actions
from computer_use.interaction.read.presentation.records import NodeView
from computer_use.protocol.observation import Action, NativeObservation, ReadIssue
from computer_use.protocol.records import FactNode


@dataclass(frozen=True)
class ActionCatalogue:
    """Native catalogue and its current inline presentation for one exact element.

    Attributes:
        actions: Complete immutable native records, including availability and targets.
        names: Callable keys in the same order as the displayed secondary actions.
        labels: Existing rendered labels, including any ambiguity qualifiers.
        issues: Explicitly unsupported descriptions, retained in observation diagnostics.
        scope: Capture and original window/menu scope; sharing cannot cross this boundary.
    """

    actions: tuple[Action, ...]
    names: tuple[str, ...]
    labels: tuple[str, ...]
    issues: tuple[ReadIssue, ...] = ()
    scope: tuple[str, str, str] | None = None

    @property
    def key(self) -> str:
        """Compare complete records and read evidence for the shared inline actions.

        Primary and supporting actions keep their individual presentation. Their
        records stay in ``actions`` but do not distinguish otherwise identical
        secondary declarations. Description evidence follows the native action
        key, so a different catalogue position does not change its meaning.
        """
        selected = {index: action for index, action in enumerate(self.actions) if action.key in self.names}
        by_name = {action.key: action for action in selected.values()}
        if (
            len(selected) == len(by_name) == len(self.names) == len(set(self.names))
            and set(by_name) == set(self.names)
            and all(
                len(issue.path) >= 4 and type(issue.path[3]) is int and 0 <= issue.path[3] < len(self.actions)
                for issue in self.issues
            )
        ):
            issues = [
                {**asdict(issue), "path": (selected[issue.path[3]].key, *issue.path[4:])}
                for issue in self.issues
                if issue.path[3] in selected
            ]
            return json.dumps(
                (self.scope, [asdict(by_name[name]) for name in self.names], self.names, self.labels, issues),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        # Retain the conservative full-catalogue comparison if a source cannot
        # map every displayed name and metadata index to one native record.
        issues = [{**asdict(issue), "path": issue.path[2:]} for issue in self.issues]
        return json.dumps(
            (self.scope, [asdict(action) for action in self.actions], self.names, self.labels, issues),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )


def capture_catalogues(
    observation: NativeObservation,
    refs: Mapping[str, str],
    facts: Mapping[str, FactNode],
    roots: Sequence[NodeView],
    folded_sources: Sequence[tuple[str, NodeView]],
    render_actions: Callable[[NodeView], list[str]],
    *,
    source_scopes: Mapping[str, str],
    menu_mechanisms: tuple[MenuMechanism, ...] | None = None,
    control_raises: tuple[ControlRaise, ...] = (),
) -> Mapping[str, ActionCatalogue]:
    """Capture complete action sources without adding native snapshots to layout state.

    Incomplete catalogues and composed action owners retain their individual
    presentation. Passive folded captions leave the owner's action source intact.
    Unsupported descriptions join only when their exact evidence also agrees;
    their per-element diagnostics remain in the observation.
    """
    issues: dict[str, list[ReadIssue]] = {}
    for issue in observation.issues:
        if len(issue.path) >= 3 and issue.path[0] == "nodes" and issue.path[2] == "actions":
            issues.setdefault(issue.path[1], []).append(issue)

    def complete(ref: str) -> bool:
        """Require known catalogue membership and resolved description support."""
        native_id = refs.get(ref)
        native = observation.nodes.get(native_id)
        if (
            native is None
            or native.actions is None
            or facts.get(native_id, {}).get("actions", {}).get("state") != "complete"
        ):
            return False
        for issue in issues.get(native_id, ()):
            path = issue.path
            if not (
                issue.state == "unsupported"
                and len(path) == 5
                and type(path[3]) is int
                and 0 <= path[3] < len(native.actions)
                and path[4] == "description"
                and native.actions[path[3]].description is None
                and issue.continuation is None
                and not issue.alternatives
            ):
                return False
        return True

    composed = set()
    for owner, source in folded_sources:
        native = observation.nodes.get(refs.get(source["ref"]))
        if (
            not complete(source["ref"])
            or source.get("actionsUnavailable")
            or render_actions(source)
            or any(action.available is not False for action in native.actions)
        ):
            composed.add(owner)
    result: dict[str, ActionCatalogue] = {}
    pending = list(roots)
    while pending:
        node = pending.pop()
        pending.extend(node["children"])
        ref = node["ref"]
        native_id = refs.get(ref)
        native = observation.nodes.get(native_id)
        if (
            not complete(ref)
            or ref in composed
            or node.get("actionsUnavailable")
            or node.get("actions") != node.get("native", node).get("actions")
            or ref not in source_scopes
        ):
            continue
        actions = display_actions(
            node, secondary_actions(node), menu_mechanisms=menu_mechanisms or (), control_raises=control_raises
        )
        labels = tuple(render_actions(node))
        names = tuple(action["name"] for action in actions)
        if labels and len(labels) == len(names) and len(set(names)) == len(names):
            scope = (observation.session, observation.id, source_scopes[ref])
            result[ref] = ActionCatalogue(native.actions, names, labels, tuple(issues.get(native_id, ())), scope)
    return MappingProxyType(result)
