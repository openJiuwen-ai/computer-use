# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise named AT-SPI action contracts through the real native pipelines."""

import importlib
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


class ProviderError(Exception):
    pass


class States(set):
    def contains(self, state):
        return state in self


@pytest.fixture(scope="module")
def native_modules():
    source = Path(__file__).resolve().parents[2] / "os_adapter/linux/src"
    with patch.object(sys, "path", [str(source), *sys.path]):
        return SimpleNamespace(
            action=importlib.import_module("computer_use_linux.control.native_action"),
            accessibility=importlib.import_module("computer_use_linux.objects.native_accessibility"),
            facts=importlib.import_module("computer_use_linux.read.native_facts"),
            store=importlib.import_module("computer_use_linux.objects.native_store"),
            operation=importlib.import_module("computer_use_linux.runtime.native_operation"),
            protocol=importlib.import_module("computer_use_linux.contracts.protocol"),
        )


@pytest.fixture
def scene(native_modules):
    ctx = native_modules.protocol.Request(time.monotonic() + 10, threading.Event())
    root = SimpleNamespace(key="root", parent=None, destroyed=False)
    element = SimpleNamespace(key="element", parent=root, destroyed=False)
    s = SimpleNamespace(
        ctx=ctx,
        root=root,
        element=element,
        role="push button",
        role_error=False,
        native_role="button",
        live_error=False,
        enabled=True,
        action_interface=True,
        actions=[{"name": "activate", "description": "Activate item"}, {"name": "menu", "description": "Open menu"}],
        name_error=None,
        description_errors=set(),
        description_reads=[],
        expire_on_description=False,
        name_reads=0,
        parent_reads=0,
        after_name=None,
        on_parent=None,
        modal=False,
        sent=[],
        action_result=True,
        action_error=False,
        failure=native_modules.protocol.Failure,
    )

    def refresh(obj):
        if obj is element and s.live_error:
            raise ProviderError("provider no longer reachable")

    def states(obj):
        if obj.destroyed:
            return States(("defunct",))
        return States(("enabled", "sensitive")) if s.enabled else States()

    def role_name(_):
        if s.role_error:
            raise ProviderError("role text query failed")
        return s.role

    def parent(obj):
        if obj is element:
            s.parent_reads += 1
            if s.on_parent:
                s.on_parent()
        return obj.parent

    def name(_, index):
        s.name_reads += 1
        if index == s.name_error:
            raise ProviderError("action name unavailable")
        value = s.actions[index]["name"]
        if s.after_name:
            s.after_name(index)
        return value

    def description(_, index):
        s.description_reads.append(index)
        if s.expire_on_description:
            s.unit.facts.until = time.monotonic() - 1
        if index in s.description_errors:
            raise ProviderError("action description unavailable")
        return s.actions[index]["description"]

    def do_action(_, index):
        s.sent.append((index, s.actions[index]["name"]))
        if s.action_error:
            raise ProviderError("action reply unavailable after entry")
        return s.action_result

    flags = SimpleNamespace(ENABLED="enabled", SENSITIVE="sensitive", READ_ONLY="readOnly", DEFUNCT="defunct")
    accessible = SimpleNamespace(
        get_state_set=states,
        get_role_name=role_name,
        get_role=lambda obj: s.native_role if obj is element else "frame",
        get_interfaces=lambda _: ["Action"] if s.action_interface else [],
        get_parent=parent,
    )
    ax = SimpleNamespace(
        ctx=ctx,
        glib=SimpleNamespace(Error=ProviderError),
        dead={},
        refresh=refresh,
        native_key=lambda obj: obj.key,
        call=lambda method, *args: method(*args),
        atspi=SimpleNamespace(
            StateType=flags,
            Role=SimpleNamespace(INVALID="invalid"),
            Accessible=accessible,
            Action=SimpleNamespace(
                get_n_actions=lambda _: len(s.actions),
                get_action_name=name,
                get_action_description=description,
                do_action=do_action,
            ),
        ),
    )
    ax.live = lambda obj: native_modules.accessibility.NativeAccessibility.live(ax, obj)
    ax.begin = lambda request: setattr(ax, "ctx", request)
    window = SimpleNamespace(key="window", accessible=root, transient=None, modal=False, mapped=True)
    modal = SimpleNamespace(key="modal", transient="window", modal=True, mapped=True)
    selected = object()
    directory = SimpleNamespace(
        surfaces={"surface": selected},
        validate=lambda *_: None,
        windows=lambda *_: [SimpleNamespace(ref="modal", window=modal)] if s.modal else [],
    )
    backend = SimpleNamespace(ax=ax, ensure_unlocked=lambda: None, desktop=SimpleNamespace(validate=lambda _: window))
    store = native_modules.store.NativeStore(ax)
    node = store.keep("surface", element)
    binding = {
        "session": "session",
        "instance": "instance",
        "rootWindow": "root",
        "surface": "surface",
        "node": node,
        "capture": None,
    }
    unit = native_modules.action.NativeAction(
        backend, store, directory, native_modules.facts.NativeFacts(ax, store, "session")
    )

    def prepare(request="activate", kind="action"):
        parameters = {"action": request} if kind == "action" else {}
        intent = native_modules.action.action_intent({"kind": kind, "parameters": parameters})
        operation = native_modules.operation.NativeOperation(binding, ctx.deadline, intent, writable=True)
        facts = unit.inspect(operation, binding, (), ctx)
        exact = [p for p in facts["primitives"] if p["action"]["name"] == request]
        matched = exact or [p for p in facts["primitives"] if p["action"].get("description") == request]
        return operation, matched

    def dispatch(operation, primitive):
        s.name_reads = s.parent_reads = 0
        command = {"kind": "native_action", "primitiveId": primitive["id"], "target": binding, "foreground": False}
        return operation.step(1, command, lambda: unit.execute(operation, command, ctx)), command

    s.prepare, s.dispatch, s.unit = prepare, dispatch, unit
    return s


def admitted(scene, request="activate", kind="action"):
    operation, candidates = scene.prepare(request, kind)
    supported = [p for p in candidates if p["supported"] == "yes"]
    assert len(supported) == 1
    return operation, supported[0]


@pytest.mark.parametrize("metadata", ("selected_failure", "unrelated_failure", "changed"))
def test_exact_name_does_not_query_action_descriptions(scene, metadata):
    if metadata != "changed":
        scene.description_errors.add(0 if metadata == "selected_failure" else 1)
    operation, primitive = admitted(scene)
    if metadata == "changed":
        scene.actions[0]["description"] = "New explanatory text"
        scene.actions[1]["description"] = "Another explanation"

    receipt, _ = scene.dispatch(operation, primitive)

    assert receipt["dispatch"] == "dispatched" and scene.sent == [(0, "activate")]
    assert scene.description_reads == []


def test_slow_optional_description_cannot_disable_an_exact_named_action(scene):
    scene.expire_on_description = True
    raw = scene.unit.facts.action_catalogue(scene.element, scene.ctx)
    assert raw["state"] == "partial" and scene.description_reads == [0]
    scene.ctx.check()  # Only the native fact budget expired, not the operation.

    operation, primitive = admitted(scene)
    receipt, _ = scene.dispatch(operation, primitive)

    assert receipt["dispatch"] == "dispatched" and scene.sent == [(0, "activate")]


def test_exact_name_precedes_another_actions_matching_description(scene):
    scene.actions[1]["description"] = "activate"
    operation, primitive = admitted(scene)

    receipt, _ = scene.dispatch(operation, primitive)

    assert receipt["dispatch"] == "dispatched" and scene.sent == [(0, "activate")]
    assert scene.description_reads == []


def test_default_raw_catalogue_retains_description_failure_as_observation(scene):
    scene.description_errors.add(1)

    raw = scene.unit.facts.action_catalogue(scene.element, scene.ctx)

    assert raw["state"] == "complete" and raw["total"] == 2
    assert raw["items"][0]["description"] == "Activate item"
    assert raw["items"][1]["name"] == "menu"
    assert raw["items"][1]["descriptionStatus"]["state"] == "error"
    assert scene.description_reads == [0, 1] and scene.sent == []


@pytest.mark.parametrize("metadata", ("missing", "changed", "failed"))
def test_exact_action_does_not_depend_on_role_text(scene, metadata):
    scene.role = None if metadata == "missing" else scene.role
    scene.role_error = metadata == "failed"
    operation, primitive = admitted(scene)
    if metadata == "changed":
        scene.role = "toggle button"

    receipt, _ = scene.dispatch(operation, primitive)

    assert receipt["dispatch"] == "dispatched" and scene.sent == [(0, "activate")]


@pytest.mark.parametrize("ambiguity", ("duplicate_description", "unrelated_unknown", "selected_unknown"))
def test_description_request_needs_complete_global_uniqueness(scene, ambiguity):
    if ambiguity == "duplicate_description":
        scene.actions[1]["description"] = "Activate item"
    else:
        scene.description_errors.add(1 if ambiguity == "unrelated_unknown" else 0)

    _, candidates = scene.prepare("Activate item")

    assert not [p for p in candidates if p["supported"] == "yes"]
    assert scene.sent == []


@pytest.mark.parametrize("change", ("changed", "failed"))
def test_description_request_remains_bound_to_its_inspected_description(scene, change):
    operation, primitive = admitted(scene, "Activate item")
    if change == "changed":
        scene.actions[0]["description"] = "A different action description"
    else:
        scene.description_errors.add(0)

    receipt, _ = scene.dispatch(operation, primitive)

    assert receipt["dispatch"] == "notDispatched" and scene.sent == []


@pytest.mark.parametrize("ambiguity", ("unknown_name", "duplicate_name", "over_budget"))
def test_name_request_never_assumes_unread_names_are_different(scene, ambiguity):
    if ambiguity == "unknown_name":
        scene.name_error = 1
    elif ambiguity == "duplicate_name":
        scene.actions[1]["name"] = "activate"
    else:
        scene.actions.extend({"name": f"other-{index}", "description": ""} for index in range(127))

    _, candidates = scene.prepare()

    assert not [p for p in candidates if p["supported"] == "yes"]
    assert scene.sent == []


def test_action_count_change_during_collection_blocks_dispatch(scene):
    operation, primitive = admitted(scene)

    def grow(index):
        if index == 1 and len(scene.actions) == 2:
            scene.actions.append({"name": "activate", "description": "New duplicate"})

    scene.after_name = grow
    receipt, _ = scene.dispatch(operation, primitive)

    assert receipt["dispatch"] == "notDispatched" and scene.sent == []


def test_reordered_catalogue_rematches_name_instead_of_retaining_index(scene):
    operation, primitive = admitted(scene)
    scene.actions.reverse()

    receipt, _ = scene.dispatch(operation, primitive)

    assert receipt["dispatch"] == "dispatched" and scene.sent == [(1, "activate")]


def test_unique_description_still_dispatches_the_named_action_after_reordering(scene):
    operation, primitive = admitted(scene, "Activate item")
    scene.actions.reverse()

    receipt, _ = scene.dispatch(operation, primitive)

    assert receipt["dispatch"] == "dispatched" and scene.sent == [(1, "activate")]
    assert set(scene.description_reads) == {0, 1}


def test_last_scope_query_cannot_retarget_the_selected_action_index(scene):
    operation, primitive = admitted(scene)

    def reorder_at_dispatch_boundary():
        if scene.parent_reads == 3:
            scene.actions.reverse()

    scene.on_parent = reorder_at_dispatch_boundary
    receipt, _ = scene.dispatch(operation, primitive)

    assert scene.actions[0]["name"] == "menu"  # The provider actually changed during scope validation.
    assert receipt["dispatch"] == "notDispatched" and scene.sent == []


def test_last_scope_query_cannot_change_a_description_selected_action(scene):
    operation, primitive = admitted(scene, "Activate item")

    def change_at_dispatch_boundary():
        if scene.parent_reads == 3:
            scene.actions[0]["description"] = "Changed during scope validation"

    scene.on_parent = change_at_dispatch_boundary
    receipt, _ = scene.dispatch(operation, primitive)

    assert scene.actions[0]["description"] == "Changed during scope validation"
    assert receipt["dispatch"] == "notDispatched" and scene.sent == []


@pytest.mark.parametrize("kind,name", (("click", "click"), ("openMenu", "showmenu")))
def test_semantic_action_keeps_its_role_dependency(scene, kind, name):
    scene.actions[0]["name"] = name
    operation, primitive = admitted(scene, name, kind)
    scene.role = "table row"

    receipt, _ = scene.dispatch(operation, primitive)

    assert receipt["dispatch"] == "notDispatched" and scene.sent == []


@pytest.mark.parametrize("boundary", ("destroyed", "outside_scope", "modal", "disabled", "no_action_interface"))
def test_explicit_action_retains_identity_scope_and_capability_boundaries(scene, boundary):
    operation, primitive = admitted(scene)
    if boundary == "destroyed":
        scene.element.destroyed = True
    elif boundary == "outside_scope":
        scene.element.parent = SimpleNamespace(key="other-root", parent=None, destroyed=False)
    elif boundary == "modal":
        scene.modal = True
    elif boundary == "disabled":
        scene.enabled = False
    else:
        scene.action_interface = False

    receipt, _ = scene.dispatch(operation, primitive)

    assert receipt["dispatch"] == "notDispatched" and scene.sent == []


@pytest.mark.parametrize("loss", ("native_enum_invalid", "provider_unreachable"))
def test_ignoring_role_text_keeps_native_liveness_validation(scene, loss):
    operation, primitive = admitted(scene)
    if loss == "native_enum_invalid":
        scene.native_role = "invalid"
    else:
        scene.live_error = True

    receipt, _ = scene.dispatch(operation, primitive)

    assert receipt["dispatch"] == "notDispatched" and receipt["errorCode"] == "stale_target"
    assert scene.sent == []


@pytest.mark.parametrize("outcome", ("false", "exception"))
def test_native_action_entry_keeps_unknown_effect_and_never_replays(scene, outcome):
    operation, primitive = admitted(scene)
    scene.action_result = outcome != "false"
    scene.action_error = outcome == "exception"
    receipt, command = scene.dispatch(operation, primitive)
    assert receipt["dispatch"] == "unknown" and receipt["retryReason"] is None
    assert receipt["errorCode"] == "execution_unknown" and scene.sent == [(0, "activate")]

    def execute():
        return scene.unit.execute(operation, command, scene.ctx)

    assert operation.step(1, command, execute) == receipt
    with pytest.raises(scene.failure, match="already ended or dispatched"):
        operation.step(2, command, execute)
    assert scene.sent == [(0, "activate")]
