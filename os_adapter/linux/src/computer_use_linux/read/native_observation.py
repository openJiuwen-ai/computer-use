# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Publish collected AT-SPI facts as a single typed NativeObservation graph."""

from __future__ import annotations

from typing import Any

from computer_use_linux.contracts.native_observation import details, reference, struct, value

SOURCE = "linux.atspi"
_MISSING = object()


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


def _fields(node: dict[str, Any], requested: tuple[str, ...]) -> list[dict[str, Any]]:
    values, statuses = node["values"], node["fieldStatus"]
    fields = []
    for name in requested:
        item = values.get(name, _MISSING)
        status = statuses.get(name)
        result = {"key": [SOURCE, name], "value": None if item is _MISSING else _field_value(name, item)}
        if status is not None:
            result["state"] = status["state"] if item is _MISSING else "value"
            result["read"] = {"complete": False, "issues": [_issue(status)]}
        fields.append(result)
    return fields


def _actions(catalogue: dict[str, Any]) -> dict[str, Any]:
    qualifiers = {key: catalogue[key] for key in ("state", "total", "detail") if key in catalogue}
    complete = catalogue["state"] == "complete"
    items = []
    for action in catalogue["items"]:
        fields = [
            [key, _status_value(item) if key == "descriptionStatus" else value(item)] for key, item in action.items()
        ]
        items.append(dict(nativeType="toolkit.action", data=dict(fields=fields)))
    read = dict(complete=complete, details=details(**qualifiers))
    if not complete:
        status = catalogue.get("failure", {key: catalogue[key] for key in ("state", "detail") if key in catalogue})
        read["issues"] = [_issue(status)]
    return dict(key=["toolkit", "actions"], value=dict(nativeType="toolkit.actions", data=dict(items=items)), read=read)


def _relation(edge: dict[str, Any]) -> dict[str, Any]:
    state, targets = edge["state"], edge["targets"]
    has_payload = bool(targets) or state in ("complete", "partial")
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
        value=dict(nativeType="AtspiAccessible[]", data=dict(items=[reference(ref) for ref in targets]))
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
    requested_details: dict[tuple[str, ...], list[list[Any]]] = {}

    def declare(ref: str) -> dict[str, Any]:
        node = nodes.get(ref)
        if node is None:
            node = nodes[ref] = dict(
                id=ref, source=SOURCE, kind="element", nativeType="AtspiAccessible", properties=None
            )
        return node

    def read_details(requested: tuple[str, ...]) -> dict[str, Any]:
        # The encoded qualifiers are immutable until this graph is written.
        # Keep their wire representation as a list, even though the lookup
        # uses an immutable copy of the validated field inventory.
        qualifiers = requested_details.get(requested)
        if qualifiers is None:
            qualifiers = requested_details[requested] = details(requested_fields=list(requested))
        return {"api": "libatspi", "details": qualifiers}

    for row in rows:
        node = declare(row["node"])
        requested = tuple(row["requestedFields"])
        node["properties"] = _fields(row, requested)
        node["properties"].append(_actions(row["actions"]))
        node["read"] = read_details(requested)
    for edge in edges:
        node = declare(edge["source"])
        if node["properties"] is None:
            node["properties"] = []
            node["read"] = read_details(())
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
