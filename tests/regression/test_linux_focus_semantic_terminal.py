# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep native keyboard capture bounded by the receiver, not its text content."""

import importlib
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


class States(set):
    def contains(self, flag):
        return flag in self


class Node:
    def __init__(self, name, parent=None, *, role="panel", focused=False, editable=False, interfaces=()):
        self.name, self.parent, self.role = name, parent, role
        self.children, self.interfaces = [], list(interfaces)
        self.states = States(("SHOWING", "VISIBLE"))
        if focused:
            self.states.add("FOCUSED")
        if editable:
            self.states.add("EDITABLE")
        if parent is not None:
            parent.children.append(self)


@pytest.fixture(scope="module")
def native_modules():
    source = Path(__file__).resolve().parents[2] / "os_adapter/linux/src"
    with patch.object(sys, "path", [str(source), *sys.path]):
        return SimpleNamespace(
            sequence=importlib.import_module("computer_use_linux.control.native_sequence_receiver"),
            protocol=importlib.import_module("computer_use_linux.contracts.protocol"),
        )


@pytest.fixture
def scene(native_modules):
    root = Node("root", role="frame")
    queried_children = []
    context = native_modules.protocol.Request(time.monotonic() + 5, threading.Event())
    focus = SimpleNamespace(window="x-window", xid=77)
    matches = []

    def children(obj):
        queried_children.append(obj.name)
        return len(obj.children)

    accessible = SimpleNamespace(
        get_state_set=lambda obj: obj.states,
        get_parent=lambda obj: obj.parent,
        get_role_name=lambda obj: obj.role,
        get_interfaces=lambda obj: obj.interfaces,
        get_child_count=children,
        get_child_at_index=lambda obj, index: obj.children[index],
    )
    flags = ("FOCUSED", "EDITABLE", "READ_ONLY", "MANAGES_DESCENDANTS", "SHOWING", "VISIBLE")
    atspi = SimpleNamespace(
        Accessible=accessible,
        StateType=SimpleNamespace(**{flag: flag for flag in flags}),
        StateSet=States,
        CollectionMatchType=SimpleNamespace(ALL="all"),
        CollectionSortOrder=SimpleNamespace(CANONICAL="canonical"),
        MatchRule=SimpleNamespace(new=lambda *_: object()),
        Collection=SimpleNamespace(get_matches=lambda *_: matches[:1]),
    )
    ax = SimpleNamespace(
        native_key=lambda obj: obj.name,
        live=lambda obj: None,
        call=lambda method, *args: method(*args),
        pump=lambda: None,
        begin=lambda _: None,
        glib=SimpleNamespace(Error=RuntimeError),
        ctx=context,
        focus_events={},
        atspi=atspi,
    )
    backend = SimpleNamespace(
        ax=ax,
        desktop=SimpleNamespace(
            active=lambda: focus.window,
            focus_belongs=lambda _: True,
            keyboard_focus=lambda: focus.xid,
        ),
    )
    window = SimpleNamespace(key="x-window", accessible=root)

    def capture(target):
        return native_modules.sequence.NativeSequenceReceiver(
            backend,
            window,
            context,
            recipients=SimpleNamespace(hint=None),
            obj=target,
        )

    return SimpleNamespace(
        root=root,
        window=window,
        ax=ax,
        focus=focus,
        matches=matches,
        queried_children=queried_children,
        capture=capture,
        failure=native_modules.protocol.Failure,
    )


@pytest.mark.parametrize("role", ("entry", "text", "password text"))
@pytest.mark.parametrize("interfaces", (("Text",), ("EditableText",)))
def test_focused_text_receiver_does_not_enumerate_ordinary_content(scene, role, interfaces):
    field = Node("field", scene.root, role=role, focused=True, editable=True, interfaces=interfaces)
    for index in range(1100):
        annotation = Node(f"annotation-{index}", field, role="static")
        if index == 0:
            annotation.states.update(("ACTIVE", "SELECTED"))

    sequence = scene.capture(field)

    assert sequence.obj is field
    sequence.validate()
    assert "field" not in scene.queried_children


@pytest.mark.parametrize("root_available", (True, False))
def test_current_text_receiver_does_not_enumerate_content_after_ax_started(scene, root_available):
    field = Node("field", scene.root, role="entry", focused=True, editable=True, interfaces=("Text",))
    for index in range(1100):
        Node(f"annotation-{index}", field, role="static")
    scene.ax._state = "ready"
    if not root_available:
        scene.window.accessible = None
        scene.ax.windows = list
        scene.ax.matching_window = lambda *_: scene.root

    sequence = scene.capture(None)

    assert sequence.obj is field
    sequence.validate()
    assert "field" not in scene.queried_children


def test_current_receiver_rejects_known_independent_focus(scene):
    first = Node("first", scene.root, role="entry", focused=True, editable=True, interfaces=("Text",))
    other = Node("other", scene.root, role="entry", focused=True, editable=True, interfaces=("Text",))
    scene.ax.focus_events = {first.name: first, other.name: other}

    with pytest.raises(scene.failure, match="multiple independent"):
        scene.capture(None)


def test_visual_capture_without_ax_start_keeps_native_xid_route(scene):
    scene.window.accessible = None
    scene.ax._state = "new"

    def unexpected_accessibility_query(*_):
        pytest.fail("Visual keyboard capture must not initialize accessibility.")

    scene.ax.call = unexpected_accessibility_query
    sequence = scene.capture(None)

    assert sequence.obj is None and sequence.source == "x11"
    sequence.validate()


def test_known_independent_focus_event_is_not_hidden_by_text_terminal(scene):
    field = Node("field", scene.root, role="entry", focused=True, editable=True, interfaces=("Text",))
    other = Node("other", scene.root, role="entry", focused=True, editable=True, interfaces=("Text",))
    scene.ax.focus_events = {other.name: other}

    with pytest.raises(scene.failure, match="independent"):
        scene.capture(field)


def test_collection_candidate_still_conflicts_with_known_independent_focus(scene):
    field = Node("field", scene.root, role="entry", focused=True, editable=True, interfaces=("Text",))
    other = Node("other", scene.root, role="entry", focused=True, editable=True, interfaces=("Text",))
    scene.root.interfaces.append("Collection")
    scene.matches.append(other)
    scene.ax.focus_events = {field.name: field}

    with pytest.raises(scene.failure, match="multiple independent"):
        scene.capture(field)


@pytest.mark.parametrize(
    "role", ("panel", "document frame", "document text", "document web", "combo box", "spin button")
)
@pytest.mark.parametrize("addressed", (True, False))
def test_focused_text_capable_container_still_finds_its_internal_editor(scene, role, addressed):
    owner = Node("owner", scene.root, role=role, focused=True, editable=True, interfaces=("Text", "EditableText"))
    editor = Node("editor", owner, role="entry", focused=True, editable=True, interfaces=("Text",))

    sequence = scene.capture(owner if addressed else None)

    assert sequence.obj is editor
    assert "owner" in scene.queried_children


@pytest.mark.parametrize("flag", ("READ_ONLY", "MANAGES_DESCENDANTS"))
def test_text_focus_scope_flags_do_not_hide_a_real_editor(scene, flag):
    owner = Node("owner", scene.root, role="text", focused=True, editable=True, interfaces=("Text",))
    owner.states.add(flag)
    editor = Node("editor", owner, role="entry", focused=True, editable=True, interfaces=("Text",))

    assert scene.capture(owner).obj is editor


@pytest.mark.parametrize("editable,interfaces", ((False, ("Text",)), (True, ())))
def test_text_role_alone_does_not_hide_an_internal_editor(scene, editable, interfaces):
    owner = Node("owner", scene.root, role="text", focused=True, editable=editable, interfaces=interfaces)
    editor = Node("editor", owner, role="entry", focused=True, editable=True, interfaces=("Text",))

    assert scene.capture(owner).obj is editor


def test_known_focused_descendant_keeps_its_own_receiver_boundary(scene):
    owner = Node("owner", scene.root, role="text", focused=True, editable=True, interfaces=("Text",))
    editor = Node("editor", owner, role="entry", focused=True, editable=True, interfaces=("Text",))
    scene.ax.focus_events = {editor.name: editor}

    assert scene.capture(owner).obj is editor


@pytest.mark.parametrize("change", ("native_window", "xid", "parent", "focused"))
@pytest.mark.parametrize("addressed", (True, False))
def test_text_terminal_retains_continuous_receiver_guards(scene, change, addressed):
    field = Node("field", scene.root, role="entry", focused=True, editable=True, interfaces=("Text",))
    sequence = scene.capture(field if addressed else None)
    if change == "native_window":
        scene.focus.window = "another-window"
    elif change == "xid":
        scene.focus.xid = 88
    elif change == "parent":
        field.parent = Node("another-root", role="frame")
    else:
        field.states.remove("FOCUSED")

    with pytest.raises(scene.failure):
        sequence.validate()
