# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Express the retained live7 refresh evidence through the AX wire fixture."""

import gzip
import json
from dataclasses import replace
from pathlib import Path

from compat_read_fixtures import children, element, scalar, snapshot
from computer_use.native.contracts import Binding
from computer_use.protocol.observation import decode_observation


AX_ROLES = {
    "window": "AXWindow",
    "button": "AXButton",
    "static_text": "AXStaticText",
    "group": "AXGroup",
}
AX_STATES = {"focused": "AXFocused", "active": "AXMain", "busy": "AXElementBusy", "modal": "AXModal"}


def attribute(key, value, **fields):
    return {"key": ["macos.ax", key], "value": scalar(value), **fields}


def refresh_snapshot(evidence, surface="surface"):
    """Preserve the identities, provider fields, and coverage used by refresh."""
    target = Binding(evidence.session, "instance", "window", surface, evidence.roots[0])
    result = []
    for token, fact in evidence.nodes.items():
        native = element(token, AX_ROLES.get(fact.role, "AXUnknown"), "")
        properties = native["properties"][:1] if fact.role is not None else []
        for name, value in (
            ("AXTitle", fact.name), ("AXDescription", fact.description), ("AXIdentifier", fact.identifier),
            ("AXValue", fact.control_value.value if fact.control_value is not None else None),
            *((key, getattr(fact.states, state)) for state, key in AX_STATES.items()),
        ):
            if value is not None:
                properties.append(attribute(name, value))
        if fact.text is not None and fact.text.content is not None:
            properties.append(attribute("AXSelectedText", fact.text.content))
        relation = fact.relations.get("children")
        if relation is not None and relation.evidence != "candidate":
            properties.append(children(list(relation.targets)))
        if fact.actions == ():
            properties.append({
                "key": ["toolkit", "actions"], "value": {"nativeType": "CFArray", "data": {"items": []}},
                "read": {"complete": True, "details": [["state", scalar("complete")]]},
            })
        for issue in evidence.issues:
            if issue.path == ("nodes", token, "control_value", "value"):
                properties = [row for row in properties if row["key"][1] != "AXValue"]
                properties.append({"key": ["macos.ax", "AXValue"], "value": None, "state": issue.state})
        native["properties"] = properties
        result.append(native)
    issues = tuple(
        {"reason": issue.state, "path": list(issue.path), "detail": issue.detail}
        for issue in evidence.issues if tuple(issue.path) == ("nodes",)
    )
    return snapshot(target, nodes=result, observation_id=evidence.id, interval=evidence.interval_ns, issues=issues)


def recorded_snapshot(key, *, session=None):
    path = Path(__file__).parent / "fixtures/recorded/live7-window-observations.json.gz"
    evidence = decode_observation(json.loads(gzip.decompress(path.read_bytes()))[key])
    if session is not None:
        evidence = replace(evidence, session=session)
    return refresh_snapshot(evidence)
