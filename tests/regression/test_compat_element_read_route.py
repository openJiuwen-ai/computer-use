# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Explicit element reads preserve the requested binding and cursor protocol."""

import asyncio
from types import SimpleNamespace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.element.agent import AgentElementReadPipeline
from computer_use.native.contracts import Binding

TARGET = Binding("s", "instance", "window", "surface", "field")
OTHER = Binding("s", "instance", "window", "surface", "other")
PAGE = {"property": "AXChildren", "cursor": "native-prefix", "limit": 128}


def pipeline(capabilities, *, page=PAGE, mutate=False, confirmed=True):
    snapshot = {"binding": TARGET, "scope_kind": "window", "window_identity": "lifetime"}
    owner = SimpleNamespace(_strict_window=True)
    state = SimpleNamespace(refs={})
    capture = SimpleNamespace(contains_observed=lambda token: confirmed and token == TARGET.node)
    tree = SimpleNamespace(refs={"4": "field", "5": "other"}, capture=capture)
    effects = []

    def validate(actual_owner, actual_snapshot):
        assert actual_owner is owner and actual_snapshot is snapshot
        effects.append("validate")

    def resolve(cursor, target):
        assert cursor == "p12" and target is TARGET
        effects.append("resolve")
        return page

    async def read(target, **options):
        effects.append(("read", target, options))
        if mutate:
            snapshot["binding"] = OTHER
        return tree

    def remember(actual_state, target, observed):
        assert actual_state is state and target is TARGET and observed is tree
        effects.append("remember")
        state.refs.update({ref: {"binding": target, "source": {"ref": ref}} for ref in observed.refs})

    controller = SimpleNamespace(
        interfaces=SimpleNamespace(validate_snapshot=validate),
        pages=SimpleNamespace(resolve=resolve),
        transport=SimpleNamespace(capabilities=capabilities),
        reader=SimpleNamespace(reader=SimpleNamespace(read=read), number_snapshots=lambda value: value),
        state=lambda _: state,
        remember_observation=remember,
    )
    return AgentElementReadPipeline(controller), owner, snapshot, state, effects


@pytest.mark.parametrize(
    ("capabilities", "cursor", "max_nodes", "route"),
    [
        (("nativeNodePages",), None, 3, {"children_page": {"cursor": None, "limit": 2}}),
        (("nativeNodePages",), None, 1, {}),
        (("nativeNodePages",), "p12", 1000, {"children_page": {"cursor": "native-prefix", "limit": 128}}),
        (("nativeRelationPages",), "p12", 1000, {"relation_page": PAGE}),
    ],
)
def test_element_routes_keep_strict_window_and_original_binding_across_read(capabilities, cursor, max_nodes, route):
    reader, owner, snapshot, state, effects = pipeline(capabilities, mutate=True)
    _, fresh = asyncio.run(reader.collect(owner, snapshot, cursor, max_nodes=max_nodes))
    assert fresh is state.refs["4"]
    assert {row["window_identity"] for row in state.refs.values()} == {"lifetime"}
    assert effects == [
        "validate",
        *(["resolve"] if cursor else []),
        ("read", TARGET, {"strict_window": True, "scope_kind": "window", "max_nodes": max_nodes, **route}),
        "remember",
    ]


@pytest.mark.parametrize(
    ("cursor", "property_name", "code", "effects"),
    [
        ("p00", "AXChildren", "invalid_request", ["validate"]),
        ("p１２", "AXChildren", "invalid_request", ["validate"]),
        ("text_1", "AXChildren", "invalid_request", ["validate"]),
        ("p12", "AXRows", "unsupported_operation", ["validate", "resolve"]),
    ],
)
def test_unsupported_element_cursor_cannot_trigger_driver_or_retention(cursor, property_name, code, effects):
    reader, owner, snapshot, state, observed = pipeline(("nativeNodePages",), page={**PAGE, "property": property_name})
    with pytest.raises(ComputerUseError) as error:
        asyncio.run(reader.collect(owner, snapshot, cursor))
    assert (error.value.code, error.value.effect) == (code, "none")
    assert observed == effects and not state.refs


def test_explicit_read_requires_fresh_evidence_for_the_numbered_node():
    reader, owner, snapshot, state, effects = pipeline((), confirmed=False)
    with pytest.raises(ComputerUseError) as error:
        asyncio.run(reader.collect(owner, snapshot, None))
    assert (error.value.code, error.value.effect) == ("observation_incomplete", "none")
    assert [effects[0], effects[1][0]] == ["validate", "read"]
    assert len(effects) == 2 and not state.refs
