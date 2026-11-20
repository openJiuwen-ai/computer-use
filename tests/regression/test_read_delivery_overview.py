# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""An overview keeps the source preview and exposes a changed directory in the document."""

from types import SimpleNamespace

import pytest
from computer_use.interaction.read.acquisition.images.overview import WindowOverview
from computer_use.interaction.read.delivery.model import ObservationDraft
from computer_use.interaction.read.delivery.passes.append_window_overview import overview
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.pipeline.core import Control


@pytest.mark.parametrize(
    ("current_window", "note"),
    [("A", ""), ("B", "\nWindow catalogue changed after the thumbnail captures.")],
)
def test_catalogue_change_after_capture_is_a_renderable_overview_note(current_window, note):
    source = SimpleNamespace(
        acquired=SimpleNamespace(
            components=SimpleNamespace(source_directory={"surfaces": [{"window": "A"}]}),
            directory={"surfaces": [{"window": current_window}]},
        ),
    )
    preview = WindowOverview(None, DisplayDocument.join(("Preview",)), ({"window": "A"},))
    draft = ObservationDraft(source, {}, {}, DisplayDocument.join(("Header",)), preview)

    delivered = overview(draft, Control())

    assert delivered.document.text == "Header\nPreview" + note
    assert delivered.overview.sources is preview.sources
    assert (delivered.overview is preview) is (not note)
