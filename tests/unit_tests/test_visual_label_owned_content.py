# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep display label references valid when compressing owned editor content."""

import pytest
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.preparation import prepare_snapshot
from computer_use.interaction.read.presentation.spatial.passes import associate_labels
from computer_use.protocol.observation import (
    Action,
    ControlValue,
    CoordinateSpace,
    Geometry,
    NativeNode,
    NativeObservation,
    Relation,
    States,
    TextInfo,
    TextSpan,
    encode_observation,
)


def _editor_capture():
    def node(role, bounds, children=(), **fields):
        return NativeNode(
            role=role,
            geometry={"bounds": Geometry("screen", *bounds, True)},
            relations={"children": Relation("children", children, total=len(children))},
            **fields,
        )

    def text(source):
        return TextInfo(
            source,
            unit="utf16_code_unit",
            content_field=("control_value", "value"),
            selected_text="",
            selections=(TextSpan(0, 0),),
        )

    value = ControlValue('{"editor.tabSize":2}\n')
    actions = (Action("show_menu", key="AXShowMenu"), Action("scroll_into_view", key="AXScrollToVisible"))
    return NativeObservation(
        "macos",
        "test",
        "test",
        (1, 2),
        ("window",),
        {
            "window": node("window", (0, 0, 1000, 600), ("editor",), actions=()),
            "editor": node(
                "text_area",
                (503, 169, 173, 22),
                ("caption",),
                description="settings.json, preview",
                control_value=value,
                text=text("editor"),
                states=States(enabled=True, focused=True, selected=False),
                actions=(Action("invoke", key="AXPress"), *actions),
            ),
            "caption": node(
                "static_text",
                (503, 171, 173, 17),
                control_value=value,
                text=text("caption"),
                actions=actions,
                states=States(enabled=True, focused=False, selected=False),
            ),
        },
        coordinate_spaces={"screen": CoordinateSpace("point", "primary_screen_top_left", "down")},
    )


@pytest.mark.parametrize("publish_reference", [False, True])
def test_owned_multiline_value_keeps_published_visual_label(monkeypatch, publish_reference):
    native = _editor_capture()
    original = encode_observation(native)
    snapshot = normalize_facts(native, DisplayReferences(native.session))
    refs = {native_id: ref for ref, native_id in snapshot.refs.items()}

    if publish_reference:
        associate = associate_labels.associate_spatial_labels

        def publish(view, editor, geometry):
            associate(view, editor, geometry)
            owner = view.nodes[refs["editor"]]
            editor.annotate(owner, "visualLabelRef", refs["caption"])
            editor.annotate(owner, "_contextRefs", (refs["caption"],))

        monkeypatch.setattr(associate_labels, "associate_spatial_labels", publish)

    prepared = prepare_snapshot(snapshot)

    assert prepared.roots[0].failure is None
    assert "Presentation fallback" not in DisplayDocument.tree(prepared).text
    displayed = {node.ref: node.fields for node in prepared.nodes}
    editor = displayed[refs["editor"]]
    if publish_reference:
        assert editor["visualLabelRef"] == refs["caption"]
        assert refs["caption"] in displayed
    else:
        assert "visualLabelRef" not in editor
        assert refs["caption"] not in displayed
    assert prepared.snapshot.refs == snapshot.refs
    assert encode_observation(native) == original
