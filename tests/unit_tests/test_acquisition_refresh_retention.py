# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A budget-limited refresh cannot erase better same-window captured evidence."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.interaction.read.acquisition.components.model import ComponentParts, ComponentRequest
from computer_use.interaction.read.acquisition.content import has_content_progress, prefer_earlier_budget_capture
from computer_use.interaction.read.acquisition.model import (
    CollectedAttempt,
    Components,
    ReadPlan,
    ReadRunState,
    RecheckedAttempt,
    Refresh,
)
from computer_use.interaction.read.acquisition.passes.classify_read_attempt import classify_read_attempt
from computer_use.interaction.read.acquisition.pipeline import _CollectComponents
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.native.contracts import Binding
from computer_use.native.query.directory import SurfaceReadProfile
from computer_use.native.scopes import WorkingWindow
from computer_use.pipeline import Contract, Control, Effect
from computer_use.protocol.observation import (
    ControlValue,
    NativeNode,
    NativeObservation,
    ReadIssue,
    Relation,
    States,
    TextInfo,
)
from computer_use.protocol.observation.codec import decode_observation


@pytest.fixture
def anyio_backend():
    return "asyncio"


def snapshot(native):
    return normalize_facts(native, DisplayReferences(native.session))


@pytest.fixture
def recorded():
    path = Path(__file__).parents[1] / "fixtures" / "recorded" / "live7-window-observations.json"
    data = json.loads(path.read_text())
    return {key: snapshot(decode_observation(data[key])) for key in ("before_full", "before_budget", "after")}


def native(*, children=True, budget=False, value="initial", name="Fixture", start=10):
    root = NativeNode(
        role="window",
        name=name,
        control_value=ControlValue(value=value),
        relations={"children": Relation("children", ("child",) if children else (), total=1 if children else 0)},
    )
    nodes = {"root": root}
    if children:
        nodes["child"] = NativeNode(
            role="button", name="Open schedule", relations={"children": Relation("children", (), total=0)}
        )
    return NativeObservation(
        "macos",
        "session",
        f"capture-{start}",
        (start, start + 5),
        ("root",),
        nodes,
        issues=(ReadIssue(("nodes",), "partial", "collection time budget"),) if budget else (),
    )


def state_for(tree, *, provider_status="requestReturned"):
    target = Binding(tree.native.session, "instance", "window", "surface", tree.native.roots[0])
    directory = {
        "session": target.session,
        "application": "application",
        "provider": {"status": provider_status},
        "surfaces": [
            {
                "window": target.root_window,
                "instance": target.instance,
                "surface": target.surface,
                "node": target.node,
                "parent": None,
                "modal": False,
                "parentState": "none",
                "kind": "window",
            }
        ],
    }
    plan = ReadPlan(
        WorkingWindow("application", "Fixture", target),
        directory,
        None,
        None,
        {"view": "tree", "fields": None, "max_depth": None, "max_nodes": 2000},
        SurfaceReadProfile.ACCESSIBILITY,
    )
    return ReadRunState(plan, target, directory, components=Components(directory, tree))


async def refresh_components(monkeypatch, state, tree):
    pipeline = SimpleNamespace(
        arun=AsyncMock(return_value=ComponentParts(tree=tree)),
        contract=Contract(ComponentRequest, ComponentParts, {Effect.QUERY}),
    )
    monkeypatch.setattr("computer_use.interaction.read.acquisition.pipeline.ComponentPipeline", lambda _: pipeline)
    collector = _CollectComponents(None, refresh=True)
    return (await collector._arun(state, Control(remaining=lambda: 1000))).components


@pytest.mark.anyio
async def test_recorded_complete_capture_survives_actual_later_one_node_budget_refresh(monkeypatch, recorded):
    before, limited = recorded["before_full"], recorded["before_budget"]
    assert len(before.native.nodes) == 8 and len(limited.native.nodes) == 1
    assert prefer_earlier_budget_capture(before, limited)
    components = await refresh_components(monkeypatch, state_for(before), limited)
    assert components.tree is before
    assert components.tree.native.id == before.native.id
    assert components.tree.native.interval_ns == before.native.interval_ns
    assert len(components.tree.native.nodes) == 8
    assert any(
        "insufficient collection budget" in gap.detail and "original capture interval" in gap.detail
        for gap in components.gaps
    )
    assert not components.image_precedes_tree


def test_recorded_coverage_and_anonymous_container_changes_do_not_prove_content_progress(recorded):
    assert not has_content_progress(recorded["before_full"], recorded["before_budget"])
    assert not has_content_progress(recorded["before_full"], recorded["after"])
    assert not has_content_progress(recorded["before_budget"], recorded["after"])


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["value", "name"])
async def test_budget_refresh_with_actual_common_content_change_replaces_the_old_capture(monkeypatch, change):
    before = snapshot(native())
    later = snapshot(
        native(
            children=False,
            budget=True,
            start=30,
            **({"value": "changed"} if change == "value" else {"name": "New document"}),
        )
    )
    assert has_content_progress(before, later)
    components = await refresh_components(monkeypatch, state_for(before), later)
    assert components.tree is later
    assert not any("earlier more complete" in gap.detail for gap in components.gaps)


@pytest.mark.anyio
@pytest.mark.parametrize("identity", ["session", "instance", "root_window", "surface", "node"])
async def test_old_capture_is_not_reused_for_a_changed_exact_window_binding(monkeypatch, identity):
    before, later = snapshot(native()), snapshot(native(children=False, budget=True, start=30))
    state = state_for(before)
    state = replace(state, target=replace(state.target, **{identity: "changed-identity"}))
    components = await refresh_components(monkeypatch, state, later)
    assert components.tree is later


@pytest.mark.anyio
async def test_new_root_and_non_budget_collection_loss_do_not_reuse_previous_tree(monkeypatch):
    before = snapshot(native())
    different = native(children=False, budget=True, start=30)
    different = replace(
        different, roots=("another-root",), nodes={"another-root": next(iter(different.nodes.values()))}
    )
    assert not prefer_earlier_budget_capture(before, snapshot(different))
    later = snapshot(
        replace(native(children=False, start=30), issues=(ReadIssue(("nodes",), "partial", "provider gap"),))
    )
    components = await refresh_components(monkeypatch, state_for(before), later)
    assert components.tree is later


def classified(tree, *, provider_status="requestReturned", remaining=1000):
    state = state_for(tree, provider_status=provider_status)
    attempt = RecheckedAttempt(
        CollectedAttempt(state, state.components), state.directory, state.target, state.target, True, ()
    )
    return classify_read_attempt(attempt, Control(remaining=lambda: remaining)).refresh


def test_provider_initialization_ack_does_not_refresh_a_healthy_full_graph(recorded):
    assert classified(recorded["before_full"], provider_status="requestReturned") is Refresh.KEEP
    assert classified(recorded["before_full"], provider_status="notRequested") is Refresh.KEEP


def test_busy_and_positively_unexposed_content_keep_the_bounded_refresh_route():
    full = native()
    root = replace(full.nodes["root"], states=States(busy=True))
    busy = snapshot(replace(full, nodes={**full.nodes, "root": root}))
    assert classified(busy) is Refresh.TREE
    assert classified(busy, remaining=399) is Refresh.KEEP
    shell = snapshot(
        replace(
            native(children=False, value=None),
            nodes={
                "root": NativeNode(
                    role="window", relations={"children": Relation("children", (), total=0)}, actions=()
                ),
            },
        )
    )
    assert classified(shell, provider_status="notRequested") is Refresh.TREE


def test_content_progress_requires_known_fields_and_previous_complete_parent_for_new_controls():
    before = native()
    after = replace(
        before,
        id="new",
        nodes={
            **before.nodes,
            "root": replace(
                before.nodes["root"], relations={"children": Relation("children", ("child", "new"), total=2)}
            ),
            "new": NativeNode(role="button", name="New control"),
        },
    )
    assert has_content_progress(snapshot(before), snapshot(after))
    unknown = replace(before, issues=(ReadIssue(("nodes",), "partial", "collection time budget"),))
    assert not has_content_progress(snapshot(unknown), snapshot(after))
    changed = replace(
        before,
        nodes={**before.nodes, "root": replace(before.nodes["root"], control_value=ControlValue("new"))},
        issues=(ReadIssue(("nodes", "root", "control_value", "value"), "timeout"),),
    )
    assert not has_content_progress(snapshot(before), snapshot(changed))


def test_focus_and_capture_local_text_sources_are_not_content_progress():
    before = native()
    root = replace(
        before.nodes["root"],
        text=TextInfo("capture:before", content="same text"),
        states=States(focused=False, active=False),
    )
    before = replace(before, nodes={**before.nodes, "root": root})
    after = replace(
        before,
        id="new",
        nodes={
            **before.nodes,
            "root": replace(
                root, text=replace(root.text, source="capture:after"), states=States(focused=True, active=True)
            ),
        },
    )
    assert not has_content_progress(snapshot(before), snapshot(after))
