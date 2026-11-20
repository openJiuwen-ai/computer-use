# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep reference ordering and isolation with the current native allocator."""

import pytest
from compat_read_fixtures import snapshot_from_wire
from computer_use.interaction.read.numbering.pipeline import number_snapshots
from computer_use.interaction.read.numbering.references import read_references
from computer_use.pipeline.core import Control
from regression.test_migrated_read_provider_facts import observation, value


@pytest.mark.usefixtures("native_read")
@pytest.mark.parametrize("backend", ["linux", "windows"])
def test_allocator_preserves_typed_aliases_across_duplicates_and_scopes(backend):
    references = read_references("driver", backend)
    key = ("surface雪", "window", "node😀")
    assert references.reference_batch((key, tuple(key), "next")) == ("0", "0", "1")
    assert references.reference(key) == "0"
    assert references.scoped_batch("other", "window", ("node😀", "node😀")) == ("2", "2")
    assert references.reference(("other", "applicationMenu", "node😀")) == "3"
    assert read_references("new driver", backend).reference(key) == "0"


@pytest.mark.usefixtures("native_read")
@pytest.mark.parametrize("backend", ["linux", "windows"])
def test_native_numbering_interleaves_preview_then_assigns_hidden_nodes(backend):
    def part(surface, hidden):
        raw = observation(backend)
        raw["session"] = "driver"
        raw["read"]["details"][1][1] = value(surface)
        root = raw["nodes"][0]
        raw["nodes"] = [{**root, "id": hidden, "properties": None}, root]
        return snapshot_from_wire(raw)

    references = read_references("driver", backend)
    inputs = [part("one", "hidden-one"), part("two", "hidden-two")]
    previews, markers = [], []

    def observe(event):
        if event.path[-1] == "PreviewSnapshot" and event.phase == "enter":
            previews.append(event.path)
            if len(previews) == 2:
                # Only the first visible root is allocated between previews.
                markers.append(references.reference("between previews"))

    first, second = number_snapshots(inputs, references, _control=Control(diagnostic=observe))
    assert len(previews) == 2 and markers == ["1"]
    assert first.refs == {"0": "root", "3": "hidden-one"}
    assert second.refs == {"2": "root", "4": "hidden-two"}
    assert first.capture is inputs[0].capture and second.capture is inputs[1].capture
    assert tuple(first.refs.values()) == first.source_tokens
    assert tuple(second.refs.values()) == second.source_tokens
