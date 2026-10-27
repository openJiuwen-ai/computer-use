"""Publish a fixture's local acquisition records using the current graph wire.

Fixture scenarios describe fields and edges before serialization so malformed
coverage can be tested independently of a native OS. No SDK decoder accepts
these local records as protocol responses.
"""

from copy import deepcopy


def typed(data):
    """Preserve fixture payload content while adding native value tags."""
    if isinstance(data, dict):
        if set(data) == {"nativeType", "node"}:
            return dict(nativeType=data["nativeType"], data=dict(ref=data["node"]))
        return dict(nativeType="fixture.record", data=dict(fields=[[key, typed(value)] for key, value in data.items()]))
    if isinstance(data, (list, tuple)):
        return dict(nativeType="fixture.array", data=dict(items=[typed(value) for value in data]))
    return dict(nativeType="fixture." + type(data).__name__, data=data)


def details(values):
    return [[key, typed(value)] for key, value in values.items()]


def observation(record):
    """Encode the fixture's declared nodes, references and query coverage.

    Duplicate declared identities and undeclared edge targets remain malformed
    on purpose, allowing protocol validation tests to observe those errors.
    """
    source = {"macos": "macos.ax", "windows": "windows.uia", "linux": "linux.atspi"}[record["platform"]]
    edges = record.get("relations", [record["relation"]] if "relation" in record else [])
    roots = record.get("roots", [record["relation"]["source"]] if "relation" in record else [])
    rows = record.get("nodes", [])
    if record["kind"] == "relation":
        rows = [dict(node=record["relation"]["source"], requestedFields=[], values={}, fieldStatus={})]
    nodes = []
    for row in rows:
        properties = []
        for name in row["requestedFields"]:
            prop = dict(key=[source, name], value=typed(row["values"][name]) if name in row["values"] else None)
            status = row["fieldStatus"].get(name)
            if status is not None:
                prop["state"] = "value" if name in row["values"] else status["state"]
                issue = dict(reason=status["state"], nativeStatus=typed(status))
                if "detail" in status:
                    issue["detail"] = status["detail"]
                continuation = {key: status[key] for key in ("textRef", "totalUnits") if key in status}
                if continuation:
                    issue["continuation"] = typed(continuation)
                prop["read"] = dict(complete=False, issues=[issue])
            properties.append(prop)
        if "actions" in row:
            actions = row["actions"]
            properties.append(
                dict(
                    key=["toolkit", "actions"],
                    value=typed(actions["items"]),
                    read=dict(
                        complete=actions["state"] == "complete",
                        details=details({key: val for key, val in actions.items() if key != "items"}),
                    ),
                )
            )
        properties.extend(_relation(edge, source) for edge in edges if edge["source"] == row["node"])
        nodes.append(
            dict(
                id=row["node"],
                source=source,
                kind="element",
                nativeType="fixture.element",
                properties=properties,
                read=dict(details=details(dict(requested_fields=row["requestedFields"]))),
            )
        )
    unread = (
        record.get("unread", []) if record["kind"] != "relation" else list(dict.fromkeys(record["relation"]["targets"]))
    )
    declared = {node["id"] for node in nodes}
    for ref in unread:
        if ref not in declared:
            nodes.append(dict(id=ref, source=source, kind="element", nativeType="fixture.element", properties=None))
            declared.add(ref)
    reasons = record.get("stopReasons", [])
    qualifiers = dict(
        surface=record["surface"],
        observation_id=record["observed"]["id"],
        requested_relations=record.get(
            "requestedRelations", [record["relation"]["property"]] if "relation" in record else []
        ),
    )
    if "changeToken" in record:
        qualifiers["change_token"] = record["changeToken"]
    return dict(
        kind="observation",
        session=record["session"],
        platform=record["platform"],
        roots=roots,
        nodes=nodes,
        read=dict(
            complete=not reasons and not unread,
            intervalNs=[record["observed"]["startedAt"] * 1_000_000, record["observed"]["completedAt"] * 1_000_000],
            details=details(qualifiers),
            issues=[dict(reason=reason) for reason in reasons],
        ),
    )


def _relation(edge, source):
    state = edge["state"]
    payload = bool(edge["targets"]) or state in ("complete", "partial")
    read = dict(
        complete=state in ("complete", "absent"),
        details=details(
            dict(
                relation=True,
                **{
                    key: edge[key]
                    for key in ("certainty", "state", "offset", "total", "cursor", "detail")
                    if key in edge
                },
            )
        ),
    )
    if not read["complete"]:
        read["issues"] = [dict(reason=state, nativeStatus=typed(deepcopy(edge)))]
    return dict(
        key=[source, edge["property"]],
        value=dict(
            nativeType="fixture.references",
            data=dict(items=[dict(nativeType="fixture.element", data=dict(ref=ref)) for ref in edge["targets"]]),
        )
        if payload
        else None,
        state="value" if payload else state,
        read=read,
    )
