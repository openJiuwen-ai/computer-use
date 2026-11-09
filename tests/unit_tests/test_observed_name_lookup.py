# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Names consume delivered native evidence without changing the live fallback."""

import asyncio
import gc
import weakref
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.diagnostics import local_failure
from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.scope import window_scope
from computer_use.interaction.query.instances.scope import adopt
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.preparation import prepare_snapshot
from computer_use.interaction.read.views.pipeline import WindowPart, member_target, remember
from computer_use.interaction.read.views.publication import publish
from computer_use.native.contracts import Binding
from computer_use.native.query.observed import ObservedTargetQuery, capture_lookup_facts
from computer_use.native.query.path import TargetQuery
from computer_use.native.scopes import QueryRootUnavailable
from computer_use.native.semantics import PROPERTIES, RELATIONS
from computer_use.protocol.observation.adapters import decode_driver_reply, decode_query_facts
from computer_use.session.events import observation_delivered, set_observation_handler
from computer_use.session.lookup_continuation import current_lookup_continuation, lookup_continuation_scope
from computer_use.session.observed_lookup import merge_lookup_scopes, scope_key
from unit_tests.test_application_window_lifetimes import directory, ref, setup, surface


def value(item):
    if isinstance(item, list):
        return {"nativeType": "CFArray", "data": {"items": [value(child) for child in item]}}
    if isinstance(item, dict):
        return {"nativeType": "record", "data": {"fields": [[key, value(child)] for key, child in item.items()]}}
    return {"nativeType": "provider", "data": item}


def relation(name, children, *, partial=False):
    return {
        "key": ["macos.ax", name],
        "value": {
            "nativeType": "CFArray",
            "data": {
                "items": [{"nativeType": "AXUIElement", "data": {"ref": child}} for child in children],
            },
        },
        "read": {
            "complete": not partial,
            "details": [
                ["relation", value(True)],
                ["state", value("partial" if partial else "complete")],
                ["certainty", value("confirmed")],
                ["total", value(len(children) + int(partial))],
            ],
        },
    }


def captured_tree(
    target,
    references,
    *,
    label="Save",
    node="button",
    path=False,
    partial=False,
    duplicate=False,
    optional_error=False,
    actions_unread=False,
    invalid_root=False,
    capture="capture",
):
    """Use the actual wire adapter, including its original query metadata."""
    button = target.surface + ":" + node
    group, wrapper = target.surface + ":group", target.surface + ":wrapper"
    duplicate_node = target.surface + ":duplicate"
    rows = [
        (target.node, "AXWindow", "Form", [group] if path else [button]),
        (button, "AXButton", label, []),
    ]
    if path:
        rows.extend(((group, "AXGroup", "Settings", [wrapper]), (wrapper, "AXGroup", "", [button])))
    if duplicate:
        rows[0][3].append(duplicate_node)
        rows.append((duplicate_node, "AXButton", label, []))
    nodes = []
    for identity, role, name, children in rows:
        fields = {
            "AXRole": role,
            "AXTitle": name,
            "AXDescription": "",
            "AXIdentifier": "",
            "AXSubrole": "",
            "AXFocused": False,
            "AXModal": False,
            "AXValue": None,
        }
        props = [{"key": ["macos.ax", key], "value": value(item)} for key, item in fields.items()]
        if invalid_root and identity == target.node:
            props[0] = {
                "key": ["macos.ax", "AXRole"],
                "state": "error",
                "value": None,
                "read": {
                    "complete": False,
                    "issues": [
                        {
                            "reason": "error",
                            "nativeStatus": value({"nativeDomain": "AXError", "nativeCode": -25202}),
                        }
                    ],
                },
            }
        if optional_error:
            props.extend(
                (
                    {"key": ["macos.ax", "AXPosition"], "state": "unsupported", "value": None},
                    {"key": ["macos.ax", "AXSelectedText"], "state": "error", "value": None},
                )
            )
        props.extend(
            (
                relation("AXChildren", children, partial=partial and identity == target.node),
                relation("AXTitleUIElement", []),
                {
                    "key": ["toolkit", "actions"],
                    "value": value([]),
                    "read": {
                        "complete": not actions_unread,
                        "details": [["state", value("not_read" if actions_unread else "complete")]],
                    },
                },
            )
        )
        nodes.append(
            {"id": identity, "source": "macos.ax", "kind": "element", "nativeType": "AXUIElement", "properties": props}
        )
    wire = {
        "kind": "observation",
        "session": target.session,
        "platform": "macos",
        "roots": [target.node],
        "nodes": nodes,
        "read": {
            "complete": True,
            "intervalNs": [1000000, 2000000],
            "details": [
                ["surface", value(target.surface)],
                ["observation_id", value(capture)],
            ],
        },
    }
    reply = decode_driver_reply(wire)
    snapshot = normalize_facts(reply.observation, references)
    snapshot.collection = replace(reply.metadata, lookup_facts=capture_lookup_facts(decode_query_facts(wire)))
    return snapshot, button


class LookupFixture:
    def __init__(self, *items):
        self.controller, self.app = setup(*(items or (surface("a"),)))
        self.refs = DisplayReferences("session")
        self.calls = []
        self.live = {}
        self.controller.interfaces.lookup = SimpleNamespace(arun=AsyncMock(side_effect=self.lookup))

    async def lookup(self, request, _control):
        root = self.controller._query_root(request.owner)
        self.calls.append((root.root_window, request.path, request.timeout_ms))
        if root.root_window not in self.live:
            raise local_failure("no live match", "element_not_found")
        return self.live[root.root_window]

    def observe(self, key="a", **options):
        target = member_target(self.app._instance, self.app._instance.members[key])
        tree, button = captured_tree(target, self.refs, **options)
        remember(self.controller, self.app, WindowPart(target, tree=tree))
        self.app._context = key
        document = DisplayDocument.tree(prepare_snapshot(tree, compact=True, public_api=True)).scoped(
            (target.session, target.instance, target.root_window, target.surface)
        )
        result = publish(
            self.controller, self.app, document, directory=self.app._instance.directory, trees=((target, tree),)
        )
        number = int(next(number for number, token in tree.refs.items() if token == button))
        self.live[key] = self.app._instance.refs[str(number)]
        return result, number

    def find(self, target, *, app=None, **options):
        return asyncio.run(self.controller.interfaces.element(app or self.app, target, 0, None, **options))


@pytest.mark.parametrize("path", [False, True])
def test_delivered_name_and_path_reuse_exact_numbered_binding_without_live_lookup(path):
    fixture = LookupFixture()
    _observed, number = fixture.observe(path=path)
    target = ["Settings", "Save"] if path else "Save"

    found = fixture.find(target)

    assert found["binding"] == fixture.find(number)["binding"]
    assert found["source"] == fixture.find(number)["source"]
    assert fixture.calls == []


def test_fresh_name_keeps_live_lookup_and_does_not_rebind_a_saved_element():
    fixture = LookupFixture()
    _observed, number = fixture.observe()
    saved = fixture.find(number)
    current = dict(saved, binding=replace(saved["binding"], node="rebuilt-button"))
    fixture.live["a"] = current

    assert fixture.find("Save")["binding"] == saved["binding"]
    assert fixture.calls == []
    assert fixture.find("Save", fresh=True)["binding"] == current["binding"]
    assert len(fixture.calls) == 1
    assert fixture.find(number)["binding"] == saved["binding"]
    assert fixture.find("Save")["binding"] == current["binding"]
    assert len(fixture.calls) == 1


def test_failed_fresh_lookup_does_not_revive_old_positive_or_invalidate_other_names():
    fixture = LookupFixture()
    _observed, number = fixture.observe(path=True)
    saved = fixture.find(number)
    group = fixture.find("Settings")
    assert fixture.find("Save")["binding"] == saved["binding"]
    fixture.live.pop("a")

    with pytest.raises(ComputerUseError) as missing:
        fixture.find("Save", fresh=True)
    assert missing.value.code == "element_not_found"
    assert len(fixture.calls) == 1
    assert fixture.find("Settings")["binding"] == group["binding"]
    assert len(fixture.calls) == 1

    current = dict(saved, binding=replace(saved["binding"], node="newly-appeared"))
    fixture.live["a"] = current
    assert fixture.find("Save")["binding"] == current["binding"]
    assert len(fixture.calls) == 2
    assert fixture.find(number)["binding"] == saved["binding"]
    assert fixture.find("Save")["binding"] == current["binding"]
    assert len(fixture.calls) == 2


@pytest.mark.parametrize("options", [{"partial": True}, {"duplicate": True}])
def test_incomplete_or_ambiguous_observation_uses_original_live_lookup(options):
    fixture = LookupFixture()
    _observed, number = fixture.observe(**options)
    current = dict(fixture.find(number), binding=replace(fixture.find(number)["binding"], node="live-unique"))
    fixture.live["a"] = current

    assert fixture.find("Save")["binding"] == current["binding"]
    assert len(fixture.calls) == 1


@pytest.mark.parametrize("options", [{"optional_error": True}, {"actions_unread": True}])
def test_unrelated_optional_field_failure_does_not_prevent_name_reuse(options):
    fixture = LookupFixture()
    _observed, number = fixture.observe(**options)
    assert fixture.find("Save")["binding"] == fixture.find(number)["binding"]
    assert fixture.calls == []


def test_known_invalid_captured_root_uses_original_live_lookup_without_retiring_window():
    fixture = LookupFixture()
    _observed, number = fixture.observe(invalid_root=True)

    assert fixture.find("Save")["binding"] == fixture.find(number)["binding"]
    assert len(fixture.calls) == 1
    assert "a" in fixture.app._instance.members


def test_absent_cached_name_does_not_become_a_negative_cache():
    fixture = LookupFixture()
    fixture.observe(label="Before")
    assert fixture.find("New confirmation")["binding"] == fixture.live["a"]["binding"]
    assert len(fixture.calls) == 1
    assert fixture.find("New confirmation")["binding"] == fixture.live["a"]["binding"]
    assert len(fixture.calls) == 1


def test_new_observation_replaces_name_candidates_but_keeps_old_numbered_binding():
    fixture = LookupFixture()
    _observed, original_number = fixture.observe(label="Before", node="original", capture="old")
    old_binding = fixture.find(original_number)["binding"]
    _current, current_number = fixture.observe(label="After", node="replacement", capture="new")

    assert fixture.find("After")["binding"] == fixture.find(current_number)["binding"]
    assert fixture.find(original_number)["binding"] == old_binding
    assert fixture.calls == []
    fixture.find("Before")
    assert len(fixture.calls) == 1


def test_fixed_window_uses_its_own_observation_after_other_window_delivery():
    fixture = LookupFixture(surface("a"), surface("b"))
    _first, a_number = fixture.observe("a")
    _second, b_number = fixture.observe("b")
    a = window_scope(fixture.controller, fixture.app, ref(fixture.app, "a"))

    assert fixture.find("Save", app=a)["binding"] == fixture.find(a_number)["binding"]
    assert fixture.find("Save")["binding"] == fixture.find(b_number)["binding"]
    assert fixture.calls == []


def test_old_cross_window_ambiguity_falls_back_before_returning_current_unique_match():
    fixture = LookupFixture(surface("a"), surface("child", parent="a"))
    fixture.observe("child")
    _observed, number = fixture.observe("a")
    # The latest observations had two matches. The live fallback now sees only
    # one; stale cache ambiguity must not become an immediate public rejection.
    fixture.live.pop("child")

    found = fixture.find("Save")

    assert found["binding"] == fixture.find(number)["binding"]
    assert {call[0] for call in fixture.calls} == {"a", "child"}
    assert len(fixture.calls) == 2


def test_partial_new_observation_cannot_resurrect_a_removed_old_candidate():
    fixture = LookupFixture()
    fixture.observe(label="Save", node="old", capture="old")
    _current, number = fixture.observe(label="Other", node="new", capture="partial", partial=True)

    result = fixture.find("Save")

    assert result["binding"] == fixture.find(number)["binding"]
    assert len(fixture.calls) == 1


def test_window_lifetime_replacement_updates_app_lookup_without_rebinding_old_id():
    fixture = LookupFixture()
    _previous, old_number = fixture.observe()
    original = fixture.find(old_number)["binding"]
    old_window = window_scope(fixture.controller, fixture.app, ref(fixture.app, "a"))
    replacement = surface("a", scope="replacement")
    adopt(fixture.controller, fixture.app, directory(replacement, focus="a"))
    _current, new_number = fixture.observe(capture="new-lifetime")

    assert fixture.find("Save")["binding"] == fixture.find(new_number)["binding"]
    assert fixture.find(old_number)["binding"] == original
    assert fixture.calls == []
    with pytest.raises(ComputerUseError) as error:
        fixture.find("Save", app=old_window)
    assert error.value.code == "stale_target"


def test_pending_observation_without_tool_continuation_cannot_replace_delivered_facts():
    fixture = LookupFixture()
    _first, original_number = fixture.observe(node="original")
    set_observation_handler(lambda _event: None)
    try:
        pending, next_number = fixture.observe(node="replacement", capture="pending")
        assert fixture.find("Save")["binding"] == fixture.find(original_number)["binding"]
        assert fixture.calls == []
        observation_delivered(pending)
        assert fixture.find("Save")["binding"] == fixture.find(next_number)["binding"]
        assert fixture.calls == []
    finally:
        set_observation_handler(None)


def test_delayed_observation_cannot_revive_facts_disproved_by_a_later_fresh_lookup():
    fixture = LookupFixture()
    fixture.observe(node="old", capture="delivered")
    old = fixture.find("Save")
    set_observation_handler(lambda _event: None)
    try:
        pending, _ = fixture.observe(node="pending-before-fresh", capture="pending")
        fixture.live.clear()
        with pytest.raises(ComputerUseError) as missing:
            fixture.find("Save", fresh=True)
        assert missing.value.code == "element_not_found"
        observation_delivered(pending)
        current = dict(old, binding=replace(old["binding"], node="live-current"))
        fixture.live["a"] = current
        assert fixture.find("Save")["binding"] == current["binding"]
        assert len(fixture.calls) == 2
    finally:
        set_observation_handler(None)


@pytest.mark.parametrize("component", ["tree", "image"])
@pytest.mark.parametrize("deferred", [False, True])
def test_failed_tree_capture_invalidates_its_scope_but_image_only_preserves_it(component, deferred):
    fixture = LookupFixture()
    fixture.observe(node="old")
    fixed = window_scope(fixture.controller, fixture.app, ref(fixture.app, "a"))
    old = fixture.find("Save", app=fixed)
    current = dict(old, binding=replace(old["binding"], node="live-current"))
    fixture.live["a"] = current
    item = fixture.app._instance.members["a"]
    coverage = {
        "window": item["window"], "surface": item["surface"], "instance": item["instance"],
        "requested": (component,), "attempted": (component,), "captured": (), "included": (), "delivered": (),
        "required": True, "missing": (component,), "lifecycle": "retained",
    }
    if deferred:
        set_observation_handler(lambda _event: None)
    try:
        with lookup_continuation_scope():
            publish(
                fixture.controller, fixture.app, f"{component} unavailable", partial=True,
                directory=fixture.app._instance.directory, content_available=False,
                gaps=(ObservationGap(component, "observation_incomplete", "unavailable"),),
                window_coverage=(coverage,),
            )
            found = fixture.find("Save", app=fixed)
            assert found["binding"] == (current if component == "tree" else old)["binding"]
            assert len(fixture.calls) == int(component == "tree")
    finally:
        set_observation_handler(None)


def test_replaced_observation_source_is_not_kept_alive_by_invalidation_registry():
    fixture = LookupFixture()
    fixture.observe(node="old", capture="old")
    previous = weakref.ref(next(iter(fixture.app._instance.lookup_observations.values())))
    previous_facts = weakref.ref(previous().facts)
    fixture.observe(node="current", capture="current")
    gc.collect()
    assert previous() is None
    assert previous_facts() is None


@pytest.mark.parametrize("retire", [False, True])
def test_ended_lifetime_releases_pending_sources_before_delayed_delivery(retire):
    fixture = LookupFixture(surface("a"), surface("b"))
    fixture.observe("a")
    set_observation_handler(lambda _event: None)
    try:
        pending, _ = fixture.observe("a", node="pending", capture="pending")
        sources = tuple(fixture.app._instance.lookup_captures)
        if retire:
            adopt(fixture.controller, fixture.app, directory(surface("b"), focus="b", ended=(surface("a"),)))
        else:
            fixture.controller.invalidate()
        assert all(source.facts is None and not source.snapshots for source in sources)
        observation_delivered(pending)
        assert not fixture.app._instance.lookup_observations
    finally:
        set_observation_handler(None)


def test_same_tool_continuation_reuses_returned_facts_without_advancing_delivery():
    fixture = LookupFixture()
    fixture.observe(label="Before")
    generation = fixture.app._instance.delivered_observation_generation
    lookup = fixture.app._instance.lookup_context
    image = fixture.app._instance.delivered_image
    set_observation_handler(lambda _event: None)
    try:
        with lookup_continuation_scope():
            _pending, number = fixture.observe(label="Confirm", node="confirmation", capture="confirmation")
            assert fixture.find("Confirm")["binding"] == fixture.find(number)["binding"]
            assert fixture.calls == []
            assert fixture.app._instance.delivered_observation_generation == generation
            assert fixture.app._instance.lookup_context == lookup
            assert fixture.app._instance.delivered_image is image
        fixture.find("Confirm")
        assert len(fixture.calls) == 1
    finally:
        set_observation_handler(None)


def test_fixed_window_consumes_same_tool_pending_facts_without_changing_default_scope():
    fixture = LookupFixture(surface("a"), surface("b"))
    fixture.observe("a", label="Old A")
    fixture.observe("b", label="B")
    a = window_scope(fixture.controller, fixture.app, ref(fixture.app, "a"))
    generation = fixture.app._instance.delivered_observation_generation
    context = fixture.app._instance.lookup_context
    set_observation_handler(lambda _event: None)
    try:
        with lookup_continuation_scope():
            _pending, number = fixture.observe("a", label="Confirm", node="confirmation", capture="pending")
            assert fixture.find("Confirm", app=a)["binding"] == fixture.find(number)["binding"]
            assert fixture.calls == []
            assert fixture.app._instance.lookup_context == context
            assert fixture.app._instance.delivered_observation_generation == generation
    finally:
        set_observation_handler(None)


@pytest.mark.parametrize("live_match", [False, True])
def test_fresh_lookup_in_continuation_does_not_revive_shadowed_delivered_source(live_match):
    fixture = LookupFixture()
    fixture.observe(node="old", capture="delivered")
    old = fixture.find("Save")
    set_observation_handler(lambda _event: None)
    try:
        with lookup_continuation_scope():
            fixture.observe(node="pending", capture="pending")
            fixture.find("Save")
            current = dict(old, binding=replace(old["binding"], node="live-current"))
            fixture.live = {"a": current} if live_match else {}
            if live_match:
                assert fixture.find("Save", fresh=True)["binding"] == current["binding"]
            else:
                with pytest.raises(ComputerUseError) as missing:
                    fixture.find("Save", fresh=True)
                assert missing.value.code == "element_not_found"
        fixture.live["a"] = current
        assert fixture.find("Save")["binding"] == current["binding"]
        assert len(fixture.calls) == 2
    finally:
        set_observation_handler(None)


def test_fresh_lookup_invalidates_semantically_identical_paths_with_different_key_order():
    fixture = LookupFixture()
    fixture.observe()
    original = [{"name": "Save", "role": "button"}]
    reordered = [{"role": "button", "name": "Save"}]
    old = fixture.find(original)
    fixture.live.clear()
    with pytest.raises(ComputerUseError) as missing:
        fixture.find(reordered, fresh=True)
    assert missing.value.code == "element_not_found"
    current = dict(old, binding=replace(old["binding"], node="live-current"))
    fixture.live["a"] = current
    assert fixture.find(original)["binding"] == current["binding"]
    assert len(fixture.calls) == 2


def test_recovered_live_scope_also_invalidates_its_previously_captured_match():
    form, popup = surface("form"), surface("popup", parent_state="unknown")
    fixture = LookupFixture(form, popup)
    fixture.observe("form")
    old = fixture.find("Save")
    fixed = window_scope(fixture.controller, fixture.app, ref(fixture.app, "form"))
    fixture.observe("popup")
    fixture.live.clear()
    refreshed = directory(form, {**popup, "node": None, "accessibility": False}, focus="form")
    fixture.controller.reader.directory._query = AsyncMock(return_value=refreshed)
    attempts = []

    async def lookup(request, control):
        root = fixture.controller._query_root(request.owner)
        attempts.append(root.root_window)
        if len(attempts) == 1:
            raise QueryRootUnavailable(root, native_domain="AXError", native_code=-25202)
        return await fixture.lookup(request, control)

    fixture.controller.interfaces.lookup.arun.side_effect = lookup
    with pytest.raises(ComputerUseError) as missing:
        fixture.find("Save", fresh=True)
    assert missing.value.code == "element_not_found"
    assert attempts == ["popup", "form"]
    current = dict(old, binding=replace(old["binding"], node="live-current"))
    fixture.live["form"] = current
    assert fixture.find("Save", app=fixed)["binding"] == current["binding"]
    assert attempts == ["popup", "form", "form"]


def test_cancelled_lookup_does_not_consume_cache_or_dispatch_live_query():
    fixture = LookupFixture()
    fixture.observe()
    control = SimpleNamespace(cancelled=lambda: True)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(fixture.controller.interfaces.element(fixture.app, "Save", 0, control))
    assert fixture.calls == []


def test_retiring_one_window_releases_its_cache_without_breaking_other_window_or_old_ids():
    fixture = LookupFixture(surface("a"), surface("b"))
    _old, old_number = fixture.observe("a")
    original = fixture.find(old_number)["binding"]
    _current, current_number = fixture.observe("b")
    adopt(fixture.controller, fixture.app, directory(surface("b"), focus="b", ended=(surface("a"),)))

    assert all(source.target.root_window != "a" for source in fixture.app._instance.lookup_observations.values())
    assert fixture.find("Save")["binding"] == fixture.find(current_number)["binding"]
    assert fixture.find(old_number)["binding"] == original
    assert fixture.calls == []


def test_scope_cache_capacity_eviction_does_not_remove_live_numbered_binding():
    fixture = LookupFixture(*(surface(f"window-{index}") for index in range(129)))
    _first, first_number = fixture.observe("window-0")
    original = fixture.find(first_number)["binding"]
    for index in range(1, 129):
        fixture.observe(f"window-{index}")

    assert len(fixture.app._instance.lookup_observations) <= 128
    first = window_scope(fixture.controller, fixture.app, ref(fixture.app, "window-0"))
    assert fixture.find("Save", app=first)["binding"] == original
    assert fixture.find(first_number)["binding"] == original
    assert len(fixture.calls) == 1


def test_session_invalidation_releases_owned_lookup_sources_and_continuation_only():
    fixture, other = LookupFixture(), LookupFixture()
    _old, number = fixture.observe()
    fixture.find("Save", fresh=True)
    original = fixture.find(number)
    other.observe()
    set_observation_handler(lambda _event: None)
    try:
        with lookup_continuation_scope():
            fixture.observe(node="pending", capture="pending")
            assert current_lookup_continuation(fixture.app._instance) is not None
            fixture.controller.invalidate()
            assert not fixture.app._instance.lookup_observations
            assert not fixture.app._instance.lookup_results
            assert current_lookup_continuation(fixture.app._instance) is None
            assert fixture.app._instance.refs[str(number)] is original
            assert other.app._instance.lookup_observations
    finally:
        set_observation_handler(None)


def test_same_generation_sources_keep_distinct_ax_roots_within_one_physical_window():
    fixture = LookupFixture()
    fixture.observe()
    source = next(iter(fixture.app._instance.lookup_observations.values()))
    second = replace(source, target=replace(source.target, node="another-ax-root"))
    merged = merge_lookup_scopes((), (source, second))
    assert set(merged) == {scope_key(source.target), scope_key(second.target)}
    newer = replace(source, generation=source.generation + 1)
    assert list(merge_lookup_scopes(merged, (newer,)).values()) == [newer]


def test_eviction_cannot_resurrect_live_result_from_before_a_new_observation():
    fixture = LookupFixture()
    fixture.observe(node="old", capture="old")
    # Optional cache eviction leaves ordinary live lookup and IDs available.
    fixture.app._instance.lookup_observations.clear()
    old = fixture.find("Save")
    assert len(fixture.calls) == 1
    fixture.observe(node="new", capture="new")
    fixture.app._instance.lookup_observations.clear()

    assert fixture.find("Save")["binding"] == fixture.live["a"]["binding"]
    assert fixture.live["a"]["binding"] != old["binding"]
    assert len(fixture.calls) == 2


def test_fresh_failure_over_expanded_scopes_invalidates_earlier_smaller_scope_proof():
    fixture = LookupFixture(surface("a"), surface("child", parent="a"))
    fixture.observe("a")
    fixed = window_scope(fixture.controller, fixture.app, ref(fixture.app, "a"))
    old = fixture.find("Save", app=fixed, fresh=True)
    fixture.observe("child")
    fixture.live.clear()
    with pytest.raises(ComputerUseError) as missing:
        fixture.find("Save", app=fixed, fresh=True)
    assert missing.value.code == "element_not_found"
    assert len(fixture.calls) == 3

    adopt(fixture.controller, fixture.app, directory(surface("a"), ended=(surface("child", parent="a"),)))
    current = dict(old, binding=replace(old["binding"], node="new-after-fresh-failure"))
    fixture.live["a"] = current
    assert fixture.find("Save", app=fixed)["binding"] == current["binding"]
    assert len(fixture.calls) == 4


def platform_facts(platform, *, labelled=False, actionable_wrapper=False):
    """A complete acquired graph using each backend's actual query vocabulary."""
    roles = {
        "macos": ("AXWindow", "AXGroup", "AXButton", "AXStaticText"),
        "windows": ("ControlType.Window", "ControlType.Group", "ControlType.Button", "ControlType.Text"),
        "linux": ("frame", "panel", "push button", "label"),
    }[platform]
    props = PROPERTIES[platform]
    relation_names = {semantic: native for native, semantic in RELATIONS[platform].items()}
    sources = (
        ("root", roles[0], "Form"),
        ("group", roles[1], "Settings"),
        ("wrapper", roles[1], ""),
        ("button", roles[2], "" if labelled else "Save"),
        ("label", roles[3], ""),
    )
    rows, edges = [], []
    children = {"root": ["group"], "group": ["wrapper"], "wrapper": ["button"]}
    for token, role, name in sources:
        semantic = {
            "role": role,
            "name": name,
            "description": "",
            "identifier": "",
            "subrole": "",
            "focused": False,
            "modal": False,
            "nativeWindowHandle": 0,
            "windowContentScope": "content",
            "value": "Save" if token == "label" else None,
        }
        fields = {props[key]: item for key, item in semantic.items() if key in props}
        action = "Invoke.Invoke" if actionable_wrapper else "ScrollItem.ScrollIntoView"
        actions = [{"name": action}] if platform == "windows" and token == "wrapper" else []
        rows.append(
            {
                "node": token,
                "values": fields,
                "requestedFields": list(fields),
                "fieldStatus": {},
                "actions": {"state": "complete", "items": actions},
            }
        )
        for semantic_relation, targets in (
            ("children", children.get(token, [])),
            ("labelledBy", ["label"] if labelled and token == "button" else []),
        ):
            edges.append(
                {
                    "source": token,
                    "property": relation_names[semantic_relation],
                    "targets": targets,
                    "state": "complete",
                    "certainty": "confirmed",
                    "total": len(targets),
                }
            )
    return {
        "kind": "nodes",
        "session": "session",
        "platform": platform,
        "surface": "surface",
        "roots": ["root"],
        "nodes": rows,
        "relations": edges,
        "observed": {"id": "capture", "startedAt": 1, "completedAt": 2},
        "unread": [],
        "stopReasons": [],
    }


@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
@pytest.mark.parametrize("labelled", [False, True])
@pytest.mark.parametrize("path", [["Save"], ["Settings", "Save"]])
def test_captured_matcher_preserves_platform_name_and_anonymous_wrapper_rules(platform, labelled, path):
    async def run():
        facts = platform_facts(platform, labelled=labelled)
        target = Binding("session", "instance", "window", "surface", "root")
        port = SimpleNamespace(platform=platform, capabilities=(), query_nodes=AsyncMock(return_value=facts))
        live = TargetQuery(port, "lease", target, lambda: 1000)
        captured = ObservedTargetQuery(capture_lookup_facts(facts), target, lambda: 1000, ())

        async def outcome(query):
            try:
                return "match", await query.resolve(path), query.within
            except ComputerUseError as error:
                return "error", error.code

        expected = await outcome(live)
        assert await outcome(captured) == expected
        assert expected[0] == ("error" if platform == "windows" and labelled else "match")
        port.query_nodes.assert_awaited_once()

    asyncio.run(run())


def test_captured_windows_wrapper_keeps_an_action_boundary():
    async def run():
        facts = platform_facts("windows", actionable_wrapper=True)
        target = Binding("session", "instance", "window", "surface", "root")
        query = ObservedTargetQuery(capture_lookup_facts(facts), target, lambda: 1000, ())
        with pytest.raises(ComputerUseError) as error:
            await query.resolve(["Settings", "Save"])
        assert error.value.code == "element_not_found"

    asyncio.run(run())


def test_captured_lookup_facts_detach_mutable_input_and_keep_original_evidence():
    async def run():
        facts = platform_facts("macos")
        target = Binding("session", "instance", "window", "surface", "root")
        captured = capture_lookup_facts(facts)
        facts["nodes"][3]["values"]["AXTitle"] = "Changed externally"
        facts["relations"][0]["targets"].clear()
        assert (await ObservedTargetQuery(captured, target, lambda: 1000, ()).resolve(["Save"])).node == "button"
        with pytest.raises(TypeError):
            captured.nodes["button"]["values"]["AXTitle"] = "mutated"

    asyncio.run(run())
