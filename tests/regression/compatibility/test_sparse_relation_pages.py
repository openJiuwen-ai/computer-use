# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native relationship holes preserve later targets and exact continuation slots."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.accessibility.preparation import prepare_snapshot
from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot
from computer_use.interaction.read.pages import ContentPages
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.native.contracts import Binding, HostPolicy
from computer_use.native.query.graph import NativeGraph
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.observation import NativeNode, NativeObservation, Relation, decode_observation, encode_observation
from computer_use.protocol.observation.adapters import decode_query_facts
from computer_use.protocol.observation.validation import validate_query_observation

TARGET = Binding("session", "app", "window", "surface", "root")


def wire():
    return {
        "kind": "observation",
        "session": "session",
        "platform": "macos",
        "roots": ["root"],
        "nodes": [{
            "id": "root",
            "source": "macos.ax",
            "kind": "element",
            "nativeType": "AXUIElement",
            "properties": [{"key": ["macos.ax", "AXRole"], "value": native_value("AXWindow")}],
        }],
        "read": {
            "complete": True,
            "intervalNs": [1000000, 2000000],
            "details": [["surface", native_value("surface")], ["observation_id", native_value("capture")]],
        },
    }


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
def test_sparse_page_retains_exact_positions_and_next_native_slot(pooled, native_read):
    raw = page(["first", "third"])
    if pooled:
        details = raw["nodes"][0]["properties"][-1]["read"]["details"]
        gap = next(pair for pair in details if pair[0] == "unavailablePositions")
        raw["metadataValues"] = [gap[1]]
        gap[1] = {"metadataValue": 0}
    query = decode_query_facts(raw)["relations"][0]
    assert query["targets"] == ["first", "third"] and query["inspectedCount"] == 3
    assert query["unavailablePositions"] == [1]
    frame = native_read.open_read_frame("json", json.dumps(raw).encode(), 8192)
    capture = frame.capture("window", [])
    relation = capture.node_view("root", ["relations"])["relations"]["children"]
    assert relation["positions"] == [0, 2] and relation["enumeration"] == "sampled"
    assert capture.lookup_evidence([])["relations"][0] == query
    snapshot = NativeSnapshot.number(capture, {token: str(index) for index, token in enumerate(capture.source_tokens)})
    (children,) = snapshot.public_pages(TARGET)
    assert children["offset"] == 3
    pages = ContentPages()
    pages.remember(TARGET, snapshot)
    assert pages.resolve("p1", TARGET)["cursor"] == "after-three"
    assert "next offset 3 of 5" in pages.hints(TARGET, snapshot)
    rendered = DisplayDocument.tree(prepare_snapshot(snapshot)).text
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
    original = NativeObservation(
        "macos", "session", "capture", (1, 2), ("root",),
        {
            "root": NativeNode(role="window", relations={
                "children": Relation(
                    "children", ("first", "third"), total=5, positions=(0, 2),
                    enumeration="sampled", offset=0, inspected_count=3, unavailable_positions=(1,),
                ),
            }),
            "first": NativeNode(),
            "third": NativeNode(),
        },
    )
    assert decode_observation(encode_observation(original)) == original
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


@pytest.mark.parametrize(
    ("platform", "provider", "native_type"),
    [
        ("windows", "windows.uia", "IUIAutomationElement"),
        ("linux", "linux.atspi", "AtspiAccessible"),
    ],
)
def test_portable_node_and_relation_queries_share_exact_cursor_scope_and_range(platform, provider, native_type):
    property_key = 32001
    start = 2**66

    def portable_payload(targets, **changes):
        raw = page(targets, **changes)
        raw["platform"] = platform
        for node in raw["nodes"]:
            node["source"], node["nativeType"] = provider, native_type
        relation = raw["nodes"][0]["properties"][-1]
        relation["key"] = [provider, property_key]
        for item in relation["value"]["data"]["items"]:
            item["nativeType"] = native_type
        return raw

    first = portable_payload(["first", "third"], offset=start, total=start + 5, unavailablePositions=[start + 1])
    last = portable_payload(
        ["fifth"], offset=start + 3, total=start + 5, inspectedCount=2, unavailablePositions=[start + 3], cursor=None
    )
    request = AsyncMock(side_effect=[first, last, last])
    connection = SimpleNamespace(
        protocol_version=6,
        session_identity=(1, "session"),
        platform=platform,
        capabilities=("nativeRelationPages",),
        request=request,
    )
    port = ConnectionPort(connection, HostPolicy())
    port._operations["read"] = (connection.session_identity, "read", None)

    async def collect():
        first_result = await port.query_nodes("read", TARGET, ["Role"], 10, 2, 500)
        relation = first_result["relations"][0]
        assert relation["property"] == property_key and relation["offset"] == start
        cursor = relation["cursor"]
        with pytest.raises(ValueError, match="not observed for this source and scope"):
            await port.query_relation("read", replace(TARGET, surface="other"), property_key, cursor, 2, 500)
        with pytest.raises(ComputerUseError, match="no longer retained") as failure:
            await port.query_nodes(
                "read",
                TARGET,
                ["Role"],
                10,
                2,
                500,
                relation_page={"property": str(property_key), "cursor": cursor, "limit": 2},
            )
        assert failure.value.code == "snapshot_expired" and request.await_count == 1
        last_page = await port.query_relation("read", TARGET, property_key, cursor, 2, 500)
        result = await port.query_nodes(
            "read",
            TARGET,
            ["Role"],
            10,
            2,
            500,
            relation_page={"property": property_key, "cursor": cursor, "limit": 2},
        )
        assert last_page["relation"] == result["relations"][0]
        assert last_page["relation"]["offset"] == start + 3 and last_page["relation"]["total"] == start + 5
        assert [call.args[1]["query"]["kind"] for call in request.await_args_list] == ["nodes", "relation", "nodes"]

    asyncio.run(collect())


def test_renewed_cursor_does_not_refresh_its_original_eviction_order():
    def next_page(method, parameters, options):
        assert method == "inspect"
        return page(["first", "third"], cursor=next(tokens))

    tokens = iter([*map(str, range(512)), "0", "512", None])
    request = AsyncMock(side_effect=next_page)
    connection = SimpleNamespace(protocol_version=6, session_identity=(1, "session"), platform="macos", request=request)
    port = ConnectionPort(connection, HostPolicy())
    port._operations["read"] = (connection.session_identity, "read", None)

    async def collect():
        for token in [*map(str, range(512)), "0", "512"]:
            result = await port.query_relation("read", TARGET, "AXChildren", None, 3, 500)
            assert result["relation"]["cursor"] == token
        with pytest.raises(ValueError, match="not observed for this source and scope"):
            await port.query_relation("read", TARGET, "AXChildren", "0", 3, 500)
        assert request.await_count == 514
        # The next cursor has not been evicted. Its offset is still enforced.
        with pytest.raises(ValueError, match="changed its observed range"):
            await port.query_relation("read", TARGET, "AXChildren", "1", 3, 500)
        assert request.await_count == 515

    asyncio.run(collect())
