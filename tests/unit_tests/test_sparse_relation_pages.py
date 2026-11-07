# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native relationship holes preserve later targets and exact continuation slots."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.reader import captured_children
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.pages import ContentPages
from computer_use.native.contracts import Binding, HostPolicy
from computer_use.native.query.graph import NativeGraph
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.observation import decode_observation, encode_observation
from computer_use.protocol.observation.adapters import decode_driver_reply, decode_query_facts
from computer_use.protocol.observation.validation import validate_query_observation
from unit_tests.test_observation_reuse import wire

TARGET = Binding("session", "app", "window", "surface", "root")


def native_value(value):
    if isinstance(value, list):
        return {"nativeType": "CFArray", "data": {"items": [native_value(item) for item in value]}}
    return {"nativeType": "CFString" if isinstance(value, str) else "nativeScalar", "data": value}


def page(targets, **overrides):
    raw = wire()
    detail = {
        "relation": True,
        "certainty": "confirmed",
        "state": "partial",
        "offset": 0,
        "total": 5,
        "detail": "a native slot was unavailable",
        "inspectedCount": 3,
        "unavailablePositions": [1],
        "cursor": "after-three",
    }
    detail.update(overrides)
    detail = {key: value for key, value in detail.items() if value is not None}
    raw["nodes"][0]["properties"].append(
        {
            "key": ["macos.ax", "AXChildren"],
            "value": {
                "nativeType": "CFArray",
                "data": {"items": [{"nativeType": "AXUIElement", "data": {"ref": target}} for target in targets]},
            },
            "read": {"complete": False, "details": [[key, native_value(value)] for key, value in detail.items()]},
        }
    )
    raw["nodes"].extend(
        {"id": target, "source": "macos.ax", "kind": "element", "nativeType": "AXUIElement", "properties": None}
        for target in targets
    )
    return raw


@pytest.mark.parametrize("pooled", [False, True])
def test_sparse_page_retains_exact_positions_and_next_native_slot(pooled):
    raw = page(["first", "third"])
    if pooled:
        details = raw["nodes"][0]["properties"][-1]["read"]["details"]
        gap = next(pair for pair in details if pair[0] == "unavailablePositions")
        raw["metadataValues"] = [gap[1]]
        gap[1] = {"metadataValue": 0}
    query = decode_query_facts(raw)["relations"][0]
    assert query["targets"] == ["first", "third"] and query["inspectedCount"] == 3
    assert query["unavailablePositions"] == [1]
    decoded = decode_driver_reply(raw)
    relation = decoded.observation.nodes["root"].relations["children"]
    assert relation.positions == (0, 2) and relation.enumeration == "sampled"
    assert decode_observation(encode_observation(decoded.observation)) == decoded.observation
    snapshot = normalize_facts(decoded.observation, DisplayReferences("session"))
    snapshot.collection = decoded.metadata
    (reference,) = [key for key, token in snapshot.refs.items() if token == "root"]
    assert captured_children(snapshot, reference).offset == 3
    pages = ContentPages()
    pages.remember(TARGET, snapshot)
    assert pages.resolve("p1", TARGET)["cursor"] == "after-three"
    assert "next offset 3 of 5" in pages.hints(TARGET, snapshot)
    rendered = snapshot.render(compact=True)["text"]
    assert "2/5 read" in rendered and "3/5 read" not in rendered


@pytest.mark.parametrize(
    "changes",
    [
        {"inspectedCount": True},
        {"inspectedCount": 4},
        {"inspectedCount": None},
        {"unavailablePositions": None},
        {"unavailablePositions": [True]},
        {"unavailablePositions": [3]},
        {"unavailablePositions": [1, 1]},
        {"state": "complete"},
        {"detail": ""},
    ],
)
def test_sparse_wire_rejects_fabricated_or_ambiguous_slot_evidence(changes):
    with pytest.raises(ValueError):
        decode_query_facts(page(["first", "third"], **changes))


def test_connection_uses_checked_slot_count_for_continuation_and_limit():
    first = page(["first", "third"])
    last = page(["fifth"], offset=3, inspectedCount=2, unavailablePositions=[3], cursor=None)
    connection = SimpleNamespace(
        protocol_version=6,
        session_identity=(1, "session"),
        platform="macos",
        request=AsyncMock(side_effect=[first, last, first]),
    )
    port = ConnectionPort(connection, HostPolicy())
    port._operations["read"] = (connection.session_identity, "read", None)

    async def collect():
        first_page = await port.query_relation("read", TARGET, "AXChildren", None, 3, 500)
        last_page = await port.query_relation("read", TARGET, "AXChildren", first_page["relation"]["cursor"], 2, 500)
        assert last_page["relation"]["offset"] == 3 and "cursor" not in last_page["relation"]
        with pytest.raises(ValueError, match="budget"):
            await port.query_relation("read", TARGET, "AXChildren", None, 2, 500)

    asyncio.run(collect())


@pytest.mark.parametrize(
    "invalid",
    [
        {"positions": (0, 1)},
        {"positions": (False, 2)},
        {"offset": False},
        {"inspected_count": True},
        {"inspected_count": 10**30},
        {"unavailable_positions": (True,)},
    ],
)
def test_canonical_graph_rejects_invalid_slot_evidence(invalid):
    original = decode_driver_reply(page(["first", "third"])).observation
    source = original.nodes["root"]
    invalid_relation = replace(source.relations["children"], **invalid)
    changed = replace(
        original, nodes={**original.nodes, "root": replace(source, relations={"children": invalid_relation})}
    )
    with pytest.raises((TypeError, ValueError)):
        validate_query_observation(changed)


@pytest.mark.parametrize("initial_gap", [False, True])
def test_unique_native_lookup_refuses_only_when_an_enumerated_page_has_a_gap(initial_gap):
    first = (
        page(["first", "third"])
        if initial_gap
        else page(["first", "second", "third"], inspectedCount=None, unavailablePositions=None)
    )
    last = decode_query_facts(page(["fifth"], offset=3, inspectedCount=2, unavailablePositions=[3], cursor=None))
    suffix = {
        "session": "session",
        "surface": "surface",
        "platform": "macos",
        "observed": last["observed"],
        "relation": last["relations"][0],
    }
    native = SimpleNamespace(platform="macos", capabilities=(), query_relation=AsyncMock(return_value=suffix))
    graph = NativeGraph(native, "read", TARGET, lambda: 500, fields=())
    graph._remember_nodes(decode_query_facts(first))
    with pytest.raises(ComputerUseError, match="unavailable object slots"):
        asyncio.run(graph._relation("root", "children"))
    assert native.query_relation.await_count == (0 if initial_gap else 1)
