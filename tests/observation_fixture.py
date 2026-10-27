"""Author driver fixtures and decode them through the production boundary.

Native field tables are concise fixture inputs. Tests explicitly choose the
unified observation, its scoped reply, or native input-resolution evidence.
"""

from copy import deepcopy
from dataclasses import replace

from fixtures.native_graph import observation

from computer_use.native.semantics import control_capabilities, input_container
from computer_use.protocol.observation import NativeObservation
from computer_use.protocol.observation.adapters import (
    DriverObservationReply,
    decode_driver_observation,
    decode_driver_reply,
    decode_query_facts,
)


def wire(record):
    """Encode a fixture through the current driver's transport contract."""
    if record.get("kind") == "observation":
        return record
    value = deepcopy(record)
    relations = value.get("relations", [value["relation"]] if "relation" in value else [])
    nodes = value.get("nodes", [])
    expanded = [node["node"] for node in nodes]
    value.setdefault("roots", expanded[:1] or [r["source"] for r in relations[:1]])
    value.setdefault("requestedRelations", list(dict.fromkeys(r["property"] for r in relations)))
    value.setdefault("unread", list(dict.fromkeys(t for r in relations for t in r["targets"] if t not in expanded)))
    if len(expanded + value["unread"]) != len(set(expanded + value["unread"])):
        raise ValueError("duplicate fixture node identity")
    for node in nodes:
        fields, values, statuses = node["requestedFields"], node["values"], node["fieldStatus"]
        if len(fields) != len(set(fields)) or set(values) | set(statuses) != set(fields):
            raise ValueError("fixture fields need distinct explicit query results")
        for key, status in statuses.items():
            if status["state"] == "truncated":
                if key not in values or not ("textRef" in status or status.get("detail")):
                    raise ValueError("incomplete fixture text needs coverage")
            elif key in values:
                raise ValueError("failed fixture property also has a value")
        if node.get("actions") is None:
            node.pop("actions", None)
    return observation(value)


def from_records(record):
    """Return only the unified accessibility graph."""
    if isinstance(record, NativeObservation):
        return record
    if isinstance(record, DriverObservationReply):
        return record.observation
    return decode_driver_observation(wire(record))


def reply(record, *, driver_features=(), scope_kind=None):
    """Return query scope metadata alongside the unified graph."""
    if isinstance(record, DriverObservationReply):
        return record
    payload = wire(record)
    result = decode_driver_reply(payload)
    facts = decode_query_facts(payload)
    hints = {
        node["node"]: control_capabilities(facts["platform"], node, driver_features, scope_kind=scope_kind)
        for node in facts["nodes"]
    }
    return replace(
        result,
        metadata=replace(
            result.metadata,
            operation_capabilities=hints,
            input_containers={node["node"]: input_container(facts["platform"], node) for node in facts["nodes"]},
        ),
    )


def normalize_fixture(value, references, **kwargs):
    from computer_use.interaction.read.facts.normalize import normalizeFacts

    scoped = (
        None
        if isinstance(value, NativeObservation)
        else reply(value, driver_features=kwargs.get("driver_features", ()), scope_kind=kwargs.get("scope_kind"))
    )
    if scoped is not None:
        kwargs["operation_capabilities"] = scoped.metadata.operation_capabilities
    result = normalizeFacts(value if scoped is None else scoped.observation, references, **kwargs)
    if scoped is not None:
        result.collection = scoped.metadata
    return result


def facts_fixture(value):
    """Return exact native selection evidence for target-resolution tests."""
    return decode_query_facts(wire(value))


def relation_fixture(value):
    batch = facts_fixture(value)
    if len(batch["relations"]) != 1:
        raise ValueError("Expected one relation result")
    return {k: batch[k] for k in ("session", "platform", "surface", "observed")} | dict(
        kind="relation", relation=batch["relations"][0]
    )
