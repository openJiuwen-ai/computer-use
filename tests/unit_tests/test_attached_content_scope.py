# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Logical menu content stays in its form without forged physical ancestry."""

from computer_use.interaction.query.instances.scope import allowed, display_selection
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.views.pipeline import member_target
from computer_use.interaction.read.views.publication import capture_header
from computer_use.interaction.read.views.related import RelatedRead
from computer_use.protocol.directory import decode_directory
from computer_use.session.interfaces import WindowIdentity
from test_d34_read_contracts import directory, setup, window


def scene():
    form = {**window("Form", title="Form"), "content": {"kind": "window", "host": None, "evidence": "AXRole"}}
    menu = {**window("Options", title="Options"), "kind": "dialog", "nativeWindow": False,
            "content": {"kind": "menu", "host": "Form", "evidence": "AXChildren"}}
    return form, menu, directory(form, menu, focused="Options")


def test_directory_preserves_attachment_separately_from_physical_parent():
    _, menu, current = scene()
    decoded = decode_directory(current)
    assert decoded["surfaces"][1]["content"] == menu["content"]
    assert decoded["surfaces"][1]["parent"] is None


def test_fixed_form_observes_attached_menu_and_excludes_an_unrelated_window():
    form, menu, current = scene()
    current["surfaces"].append(window("Other"))
    _, app, _ = setup(current)
    app._anchor = form["window"]
    assert set(allowed(app)) == {"Form", "Options"}
    assert menu["parent"] is None


def test_attached_menu_is_not_an_independent_choice_or_primary_even_when_focused():
    form, menu, current = scene()
    controller, _, state = setup(current)
    choices = controller.interfaces.windows.choices(current)
    assert [item.title for item in choices] == ["Form"]
    assert display_selection(controller, state, current).root.root_window == "Form"
    related = RelatedRead(member_target(state, form), (WindowIdentity.of(form),))
    assert related.preferred(current, "Form") == "Form"
    assert related.new_surfaces(current) == (menu["window"],)


def test_unclassified_ax_root_remains_addressable_without_stealing_the_working_context():
    _form, menu, current = scene()
    menu["content"] = {"kind": "unknown", "host": None, "evidence": "unclassified"}
    controller, app, state = setup(current)
    choices = controller.interfaces.windows.choices(current)
    assert {item.title for item in choices} == {"Form", "Options"}
    assert display_selection(controller, state, current).root.root_window == "Form"
    app._anchor = None
    assert allowed(app)["Options"]["node"] == menu["node"]


def test_model_header_describes_menu_under_host_without_another_window_switch():
    _, _, current = scene()
    controller, app, _ = setup(current)
    header = capture_header(controller, app, current, parts=())
    from computer_use.interaction.read.presentation.content import DisplayText

    shown = DisplayDocument.join([DisplayText("interface_header", (header,))]).text
    assert "Menu for window=0" in shown
    assert "Window 1" not in shown and "; window=1" not in shown


def test_application_source_does_not_replace_native_window_or_lose_its_ax_entry():
    main = {**window("Main"), "node": None, "accessibility": False,
            "content": {"kind": "unknown", "host": None, "evidence": "unclassified"}}
    source = {**window("Application"), "content": {"kind": "application", "host": None, "evidence": "AXRole"}}
    current = directory(main, source, focused="Application")
    controller, app, state = setup(current)
    assert display_selection(controller, state, current).root.root_window == "Main"
    choices = controller.interfaces.windows.choices(current)
    assert choices[0].id == 0
    assert len(choices) == 1
    assert allowed(app)["Application"]["node"] is not None


def test_menu_only_application_keeps_a_usable_observation_entry():
    menu = {**window("Menu"), "content": {"kind": "menu", "host": None, "evidence": "visibleNativeMenu"}}
    nested = {**window("Nested"), "content": {"kind": "menu", "host": "Menu", "evidence": "visibleNativeMenu"}}
    current = directory(menu, nested)
    controller, _, state = setup(current)
    assert display_selection(controller, state, current).root.root_window == "Menu"
