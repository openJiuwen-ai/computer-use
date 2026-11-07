# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A section's heading remains one target when its content forms visual columns."""

import pytest
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.preparation import prepare_snapshot
from computer_use.protocol.observation import (
    Action,
    CoordinateSpace,
    Geometry,
    NativeNode,
    NativeObservation,
    Relation,
    encode_observation,
)


def settings_capture(*, rows, heading_position):
    def node(role, name="", *, box=None, children=()):
        return NativeNode(
            role=role,
            name=name,
            geometry={"bounds": Geometry("screen", *box, True)} if box else {},
            actions=(),
            relations={"children": Relation("children", children, total=len(children))},
        )

    children = ["heading"]
    nodes = {
        "window": node("window", "Settings", box=(0, 0, 800, 500), children=("group",)),
        "heading": node("heading", "Skills", box=(0, 0, 32, 24) if heading_position else None),
    }
    for index in range(rows):
        y = 59 + index * 81
        label, description, toggle = (f"{kind}{index}" for kind in ("label", "description", "toggle"))
        children.extend((label, description, toggle))
        nodes[label] = node("static_text", f"Setting {index}", box=(17, y, 150, 20))
        nodes[description] = node("static_text", f"Description {index}", box=(17, y + 24, 193, 20))
        nodes[toggle] = node("checkbox", f"Toggle {index}", box=(713, y + 10, 42, 24))
    nodes["group"] = node("group", box=(0, 0, 772, 450), children=tuple(children))
    return NativeObservation(
        "macos", "settings", "capture", (1, 2), ("window",), nodes,
        coordinate_spaces={"screen": CoordinateSpace("point", "primary_screen_top_left", "down")},
    )


@pytest.mark.parametrize("rows", [1, 2, 3])
@pytest.mark.parametrize("heading_position", [True, False])
def test_heading_and_visual_columns_keep_each_original_target_once(rows, heading_position):
    native = settings_capture(rows=rows, heading_position=heading_position)
    original = encode_observation(native)
    snapshot = normalize_facts(native, DisplayReferences(native.session))
    prepared = prepare_snapshot(snapshot)
    text = DisplayDocument.tree(prepared).text

    assert "Presentation fallback" not in text
    assert text.count("heading Skills") == 1
    assert len(text.encode()) < 4096
    refs = [node.ref for node in prepared.nodes]
    assert len(refs) == len(set(refs))
    assert set(refs) == set(snapshot.refs)
    for index in range(rows):
        for label in (f"Setting {index}", f"Description {index}", f"Toggle {index}"):
            assert label in text
    assert encode_observation(native) == original


def test_heading_keeps_an_already_composed_control():
    native = NativeObservation(
        "macos", "settings", "capture", (1, 2), ("window",),
        {
            "window": NativeNode(role="window", actions=(),
                geometry={"bounds": Geometry("screen", 0, 0, 300, 200, True)},
                relations={"children": Relation("children", ("heading", "wrapper"), total=2)}),
            "heading": NativeNode(role="heading", name="Downloads", actions=()),
            "wrapper": NativeNode(
                role="group", name="Location /Downloads", actions=(Action("custom", "context-help"),),
                geometry={"bounds": Geometry("screen", 0, 40, 100, 30, True)},
                relations={"children": Relation("children", ("change",), total=1)},
            ),
            "change": NativeNode(
                role="button", name="Change", actions=(Action("invoke", "change-location"),),
                relations={"children": Relation("children", (), total=0)},
                geometry={"bounds": Geometry("screen", 0, 40, 100, 30, True)},
            ),
        },
        coordinate_spaces={"screen": CoordinateSpace("point", "primary_screen_top_left", "down")},
    )
    prepared = prepare_snapshot(normalize_facts(native, DisplayReferences(native.session)))
    assert prepared.roots[0].failure is None
    interface = prepared.roots[0].interface
    assert interface is not None
    control, = (block for block in interface.blocks.values() if block.layout == "compound-control")
    section, = (block for block in interface.blocks.values() if block.label_ref is not None and block.kind == "region")
    assert control.key in section.children
