"""Publish collected AT-SPI facts as a single typed NativeObservation graph."""

from __future__ import annotations

from typing import Any

from computer_use_linux.contracts.native_observation import details, reference, struct, value

SOURCE = "linux.atspi"


def _field_value(name: str, item: Any) -> dict[str, Any]:
    if name == "Accessible.stateSet" and isinstance(item, list):
        return dict(
            nativeType="AtspiStateSet",
            data=dict(items=[value(state, "AtspiStateType") for state in item]),
            metadata=details(representation="enum_sequence", api="libatspi.StateSet.get_states"),
        )
    if name == "Component.extents" and isinstance(item, dict):
        return struct(
            item,
            "AtspiRect",
            metadata=details(
                coordinate_space="linux.atspi.screen",
                unit="atspi_pixel",
                coordinate_type="SCREEN",
                coordinate_metadata_source="api_contract",
            ),
        )
    if name == "Text.selections" and isinstance(item, list):
        return dict(
            nativeType="AtspiRange[]",
            data=dict(
                items=[
                    struct(bounds, "AtspiRange", metadata=details(index_unit="atspi.text.offset")) for bounds in item
                ]
            ),
        )
    if name == "attributes" and isinstance(item, dict):
        return dict(
            nativeType="a{ss}",
            data=dict(entries=[[value(key, "gchararray"), value(text, "gchararray")] for key, text in item.items()]),
        )
    return value(item)


def _issue(status: dict[str, Any]) -> dict[str, Any]:
    result = dict(reason=status["state"], nativeStatus=_status_value(status))
    if status.get("detail") is not None:
        result["detail"] = status["detail"]
    continuation = {key: status[key] for key in ("textRef", "totalUnits") if key in status}
    if continuation:
        result["continuation"] = struct(continuation, "toolkit.immutable_text_capture")
    return result


def _status_value(status: dict[str, Any]) -> dict[str, Any]:
    """Embed a rejected raw result as its typed value or explicit issue slot."""
    return dict(
        nativeType="toolkit.field_status",
        data=dict(fields=[[key, item if key == "rawResult" else value(item)] for key, item in status.items()]),
    )


def _field(name: str, node: dict[str, Any]) -> dict[str, Any]:
    status = node["fieldStatus"].get(name)
    result = dict(
        key=[SOURCE, name], value=(_field_value(name, node["values"][name]) if name in node["values"] else None)
    )
    if status is not None:
        result["state"] = "value" if name in node["values"] else status["state"]
        result["read"] = dict(complete=False, issues=[_issue(status)])
    return result


def _actions(catalogue: dict[str, Any]) -> dict[str, Any]:
    qualifiers = {key: catalogue[key] for key in ("state", "total", "detail") if key in catalogue}
    complete = catalogue["state"] == "complete"
    items = [
        dict(
            nativeType="toolkit.action",
            data=dict(
                fields=[
                    [key, _status_value(item) if key == "descriptionStatus" else value(item)]
                    for key, item in action.items()
                ]
            ),
        )
        for action in catalogue["items"]
    ]
    read = dict(complete=complete, details=details(**qualifiers))
    if not complete:
        status = catalogue.get("failure", {key: catalogue[key] for key in ("state", "detail") if key in catalogue})
        read["issues"] = [_issue(status)]
    return dict(key=["toolkit", "actions"], value=dict(nativeType="toolkit.actions", data=dict(items=items)), read=read)


def _relation(edge: dict[str, Any]) -> dict[str, Any]:
    state = edge["state"]
    has_payload = bool(edge["targets"]) or state in ("complete", "partial")
    qualifiers = {
        key: edge[key] for key in ("certainty", "state", "offset", "total", "cursor", "detail") if key in edge
    }
    complete = state in ("complete", "absent")
    read = dict(complete=complete, details=details(relation=True, **qualifiers))
    if not complete:
        status = edge.get("failure", {key: edge[key] for key in ("state", "detail") if key in edge})
        issue = _issue(status)
        if "cursor" in edge:
            issue["continuation"] = struct(
                {key: edge[key] for key in ("property", "cursor", "offset", "total") if key in edge},
                "toolkit.live_relation_page",
            )
        read["issues"] = [issue]
    elif "failure" in edge:
        read["issues"] = [_issue(edge["failure"])]
    if edge.get("failures"):
        read.setdefault("issues", []).extend(_issue(status) for status in edge["failures"])
    return dict(
        key=[SOURCE, edge["property"]],
        value=dict(nativeType="AtspiAccessible[]", data=dict(items=[reference(ref) for ref in edge["targets"]]))
        if has_payload
        else None,
        state="value" if has_payload else state,
        read=read,
    )


def encode_observation(result: dict[str, Any]) -> dict[str, Any]:
    """Convert one request-local node batch or relation page into a graph.

    References are declared once, including targets outside this read budget.
    Ordered attributes, status payloads, continuations and relation qualifiers
    remain independent from semantic projection performed by the public SDK.

    Args:
        result: A completed local acquisition record whose objects are retained.

    Returns:
        The sole accessibility graph wire representation for this driver.
    """
    rows = result.get("nodes", [])
    edges = result.get("relations", [result["relation"]] if "relation" in result else [])
    roots = result.get("roots", [result["relation"]["source"]] if "relation" in result else [])
    nodes: dict[str, dict[str, Any]] = {}

    def declare(ref: str) -> dict[str, Any]:
        if ref not in nodes:
            nodes[ref] = dict(id=ref, source=SOURCE, kind="element", nativeType="AtspiAccessible", properties=None)
        return nodes[ref]

    for row in rows:
        node = declare(row["node"])
        node["properties"] = [_field(name, row) for name in row["requestedFields"]]
        node["properties"].append(_actions(row["actions"]))
        node["read"] = dict(api="libatspi", details=details(requested_fields=row["requestedFields"]))
    for edge in edges:
        node = declare(edge["source"])
        if node["properties"] is None:
            node["properties"] = []
            node["read"] = dict(api="libatspi", details=details(requested_fields=[]))
        node["properties"].append(_relation(edge))
        for ref in edge["targets"]:
            declare(ref)
    for ref in (*roots, *result.get("unread", [])):
        declare(ref)
    reasons = result.get("stopReasons", [])
    observed = result["observed"]
    requested = result.get("requestedRelations", [result["relation"]["property"]] if "relation" in result else [])
    return dict(
        kind="observation",
        session=result["session"],
        platform="linux",
        roots=roots,
        nodes=list(nodes.values()),
        read=dict(
            api="libatspi",
            intervalNs=[observed["startedAt"] * 1_000_000, observed["completedAt"] * 1_000_000],
            complete=not reasons and not result.get("unread"),
            issues=[dict(reason=reason) for reason in reasons],
            details=details(surface=result["surface"], observation_id=observed["id"], requested_relations=requested),
        ),
    )
