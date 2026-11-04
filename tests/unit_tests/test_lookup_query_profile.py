# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Path queries collect only their consumed evidence without weakening matching."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.errors import ComputerUseError
from computer_use.native.contracts import Binding, HostPolicy
from computer_use.native.query.path import TargetQuery
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.execution import decode_inspect_query
from unit_tests.test_metadata_value_pool import payload


@pytest.fixture
def anyio_backend():
    return "asyncio"


class PathPort:
    platform = "macos"
    capabilities = ("nativeQueryActionProfile",)

    def __init__(self, *, duplicate=False, label=False, wrapper=False, wrapper_action=False, unread_name=False):
        self.queries = []
        self.duplicate, self.label = duplicate, label
        self.wrapper, self.wrapper_action = wrapper, wrapper_action
        self.unread_name = unread_name
        self.target = Binding("session", "instance", "window", "surface", "root")

    async def query_nodes(self, _operation, target, fields, _nodes, _depth, _timeout, **options):
        self.queries.append((tuple(fields), dict(options)))
        values = {
            "root": {"AXRole": "AXWindow", "AXTitle": "Window"},
            "group": {"AXRole": "AXGroup", "AXTitle": "Group"},
            "wrapper": {
                "AXRole": "AXGroup",
                "AXTitle": "",
                "AXDescription": "",
                "AXIdentifier": "",
                "AXSubrole": "",
                "AXFocused": False,
                "AXModal": False,
            },
            "button": {
                "AXRole": "AXButton",
                "AXTitle": "" if self.label else "Open schedule",
                "AXDescription": "",
                "AXIdentifier": "schedule",
            },
            "duplicate": {"AXRole": "AXButton", "AXTitle": "Open schedule"},
            "label": {"AXRole": "AXStaticText", "AXTitle": "", "AXDescription": "", "AXValue": "Open schedule"},
        }
        children = {"root": ["group"] if self.wrapper else ["button"], "group": ["wrapper"], "wrapper": ["button"]}
        if self.duplicate:
            children["root"].append("duplicate")
        included = ["root", "group", "wrapper", "button"] if self.wrapper else ["root", "button"]
        if self.duplicate:
            included.append("duplicate")
        if self.label:
            included.append("label")
        nodes, relations = [], []
        for key in included:
            actions = [{"name": "AXPress"}] if self.wrapper_action and key == "wrapper" else []
            nodes.append(
                {
                    "node": key,
                    "values": {field: values[key].get(field) for field in fields},
                    "fieldStatus": {"AXTitle": {"state": "error"}} if self.unread_name and key == "button" else {},
                    "requestedFields": list(fields),
                    "actions": {
                        "state": "not_read" if options.get("action_detail") == "none" else "complete",
                        "items": actions,
                    },
                }
            )
            for property_name, targets in (
                ("AXChildren", children.get(key, [])),
                ("AXTitleUIElement", ["label"] if self.label and key == "button" else []),
            ):
                relations.append(
                    {
                        "source": key,
                        "property": property_name,
                        "targets": targets,
                        "state": "complete",
                        "certainty": "confirmed",
                        "total": len(targets),
                    }
                )
        return {
            "kind": "nodes",
            "session": "session",
            "platform": "macos",
            "surface": "surface",
            "roots": [target.node],
            "observed": {"id": "capture", "startedAt": 1, "completedAt": 2},
            "nodes": nodes,
            "relations": relations,
            "unread": [],
            "stopReasons": [],
        }

    async def find(self, path):
        return await TargetQuery(self, "lease", self.target, lambda: 1000).resolve(path)


@pytest.mark.anyio
@pytest.mark.parametrize("label", [False, True])
async def test_single_name_keeps_label_fallback_and_omits_only_unused_fields_and_actions(label):
    port = PathPort(label=label)
    assert (await port.find(["Open schedule"])).node == "button"
    assert port.queries == [(("AXRole", "AXTitle", "AXDescription", "AXValue"), {"action_detail": "none"})]


@pytest.mark.anyio
async def test_identifier_only_query_still_reads_role_for_window_boundaries():
    port = PathPort()
    assert (await port.find([{"identifier": "schedule"}])).node == "button"
    assert port.queries == [(("AXRole", "AXIdentifier"), {"action_detail": "none"})]


@pytest.mark.anyio
async def test_single_name_does_not_lose_ambiguity_when_actions_are_unread():
    with pytest.raises(ComputerUseError) as failure:
        await PathPort(duplicate=True).find(["Open schedule"])
    assert failure.value.code == "ambiguous_target"


@pytest.mark.anyio
async def test_unread_candidate_name_cannot_become_a_unique_other_match():
    with pytest.raises(ComputerUseError) as failure:
        await PathPort(duplicate=True, unread_name=True).find(["Open schedule"])
    assert failure.value.code == "observation_incomplete"


@pytest.mark.anyio
async def test_multistep_path_keeps_action_names_for_transparent_container_decisions():
    port = PathPort(wrapper=True)
    assert (await port.find(["Group", "Open schedule"])).node == "button"
    fields, options = port.queries[0]
    assert options == {"action_detail": "names"}
    assert {"AXFocused", "AXModal", "AXSubrole", "AXIdentifier"}.issubset(fields)
    with pytest.raises(ComputerUseError) as failure:
        await PathPort(wrapper=True, wrapper_action=True).find(["Group", "Open schedule"])
    assert failure.value.code == "element_not_found"


@pytest.mark.anyio
async def test_other_ports_keep_existing_collection_profile():
    port = PathPort()
    port.capabilities = ()
    await port.find(["Open schedule"])
    fields, options = port.queries[0]
    assert options == {}
    assert "AXFocused" in fields and "AXSubrole" in fields


@pytest.mark.anyio
@pytest.mark.parametrize("detail", ["none", "names", "full", None])
async def test_transport_sends_typed_action_profile_without_an_extra_request(detail):
    async def inspect(method, parameters, options):
        assert method == "inspect"
        query = decode_inspect_query(parameters["query"])
        if detail is None:
            assert "actionDetail" not in query
        else:
            assert query["actionDetail"] == detail
        return payload()

    connection = SimpleNamespace(
        protocol_version=6,
        capabilities=("nativeQueryActionProfile",),
        platform="macos",
        session_identity=(1, "session"),
        request=AsyncMock(side_effect=inspect),
    )
    port = ConnectionPort(connection, HostPolicy())
    port._operations["read"] = (connection.session_identity, "read", None)
    options = {} if detail is None else {"action_detail": detail}
    await port.query_nodes(
        "read", Binding("session", "instance", "window", "surface", "root"), ["AXRole"], 10, 2, 500, **options
    )
    assert connection.request.await_count == 1


@pytest.mark.parametrize("detail", [False, "partial", 1, None])
def test_wire_rejects_non_profile_action_details(detail):
    with pytest.raises(ValueError):
        decode_inspect_query(
            {"kind": "nodes", "fields": ["AXRole"], "maxNodes": 1, "maxDepth": 0, "actionDetail": detail}
        )
