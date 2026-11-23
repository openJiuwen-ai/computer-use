# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify visual OCR references from captured evidence, without a native desktop."""

from __future__ import annotations

import asyncio
from io import BytesIO

import pytest
from computer_use.client.visual import VisualElement
from computer_use.errors import ComputerUseError
from computer_use.protocol.png import PNG, png_size
from computer_use.session.image_coordinates import ImageCoordinates
from computer_use.session.observation import (
    CapturedTextDocument,
    bind_observation_presentation,
    lazy_observation,
    observation_document,
    observation_presentation,
)
from computer_use.visual.index import build_visual_index
from computer_use.visual.model import VisualElement as VisualElementRecord
from computer_use.visual.model import VisualIndex, VisualRef
from computer_use.visual.render import render_legend, render_marks
from PIL import Image


def _png(width=300, height=200):
    image = Image.new("RGB", (width, height), "white")
    output = BytesIO()
    image.save(output, format="PNG")
    return PNG(output.getvalue())


def _visual(lines, *, source="windowsOCR", status="complete", truncated=False):
    return {
        "source": source,
        "status": status,
        "language": "en-US",
        "truncated": truncated,
        "lines": lines,
    }


def _line(text, x, y, width, height):
    return {"text": text, "rect": {"x": x, "y": y, "width": width, "height": height}}


def test_macos_vision_capture_metadata_reaches_the_visual_index():
    import base64

    from computer_use.protocol.captures import capture_metadata, decode_capture

    png = _png(300, 200)
    image = decode_capture(
        {
            "id": "capture-mac",
            "kind": "window",
            "scope": "surface-mac",
            "startedAt": 1,
            "completedAt": 2,
            "width": 300,
            "height": 200,
            "png": base64.b64encode(bytes(png)).decode("ascii"),
            "visualText": _visual([_line("Export", 20, 20, 60, 20)], source="macOSVision"),
        },
        "window",
        "surface-mac",
    )

    metadata = capture_metadata(image)
    index = build_visual_index(image, frame_id="capture-mac", metadata=metadata)

    assert metadata["visualText"]["source"] == "macOSVision"
    assert index is not None
    assert index.source == "macOSVision"
    assert index.elements[0].source == "macOSVision"


def test_document_text_does_not_hide_an_ocr_only_control():
    png = _png()
    metadata = {
        "id": "capture-1",
        "visualText": _visual([_line("Export", 20, 20, 60, 20)]),
    }
    tree = '17 文档 Document text: "File Edit Export Tools"'

    index = build_visual_index(png, frame_id="capture-1", tree_text=tree, metadata=metadata)

    assert index is not None
    assert [(item.ref, item.label) for item in index.elements] == [(1, "Export")]


def test_addressable_accessibility_row_still_deduplicates_ocr():
    png = _png()
    metadata = {
        "id": "capture-1",
        "visualText": _visual([_line("Export", 20, 20, 60, 20)]),
    }
    tree = '12 按钮 "Export"'

    index = build_visual_index(png, frame_id="capture-1", tree_text=tree, metadata=metadata)

    assert index is None


def test_chromium_document_body_allows_auto_visuals():
    from computer_use.client.computer import App

    index = VisualIndex(
        frame_id="capture-1",
        elements=(VisualElementRecord(ref=1, label="Export", rect=(20, 20, 60, 20), frame_id="capture-1"),),
    )

    class Result:
        text = '17 文档 Document text: "File Edit"\n  16 窗格 Chrome Legacy Window'

    class PlainDocument:
        text = '17 文档 Document text: "File Edit"'

    assert App._auto_visual(Result(), index) is True
    assert App._auto_visual(PlainDocument(), index) is False


def test_build_visual_index_deduplicates_accessibility_names():
    png = _png()
    metadata = {
        "id": "capture-1",
        "visualText": _visual(
            [
                _line("Export", 20, 20, 60, 20),
                _line("Cancel", 100, 20, 50, 20),
                _line("Q3 Report", 40, 100, 90, 18),
            ]
        ),
    }

    index = build_visual_index(png, frame_id="capture-1", tree_text='button "Cancel"', metadata=metadata)

    assert index is not None
    assert [(item.ref, item.label) for item in index.elements] == [(1, "Export"), (2, "Q3 Report")]
    assert index.frame_id == "capture-1"
    assert not index.truncated


def test_build_visual_index_ignores_invalid_and_out_of_bounds_lines():
    png = _png(300, 200)
    metadata = {
        "id": "capture-2",
        "visualText": _visual(
            [
                _line("Outside", 290, 20, 40, 20),
                _line("Zero", 10, 10, 0, 20),
                _line("Inside", 10, 10, 40, 20),
            ]
        ),
    }

    index = build_visual_index(png, metadata=metadata)

    assert index is not None
    assert [item.label for item in index.elements] == ["Inside"]


def test_build_visual_index_marks_truncated_coverage():
    png = _png()
    metadata = {
        "id": "capture-3",
        "visualText": _visual(
            [_line("First", 10, 10, 40, 20), _line("Second", 60, 10, 40, 20)],
        ),
    }

    index = build_visual_index(png, metadata=metadata, max_lines=1)

    assert index is not None
    assert [item.label for item in index.elements] == ["First"]
    assert index.truncated is True


def test_build_visual_index_deduplicates_unquoted_tree_names():
    png = _png()
    metadata = {
        "id": "capture-dedup",
        "visualText": _visual(
            [
                _line("文件", 10, 10, 40, 20),
                _line("开始", 60, 10, 40, 20),
                _line("EMPTY", 100, 10, 50, 20),
            ]
        ),
    }
    tree_text = '55 菜单项目 文件\n6 按钮 开始\nTree: full'

    index = build_visual_index(png, metadata=metadata, tree_text=tree_text)

    assert index is not None
    assert [item.label for item in index.elements] == ["EMPTY"]


def test_auto_visual_uses_document_body_signal():
    from types import SimpleNamespace

    from computer_use.client.computer import App

    index = VisualIndex(
        frame_id="frame",
        elements=(VisualElementRecord(ref=1, label="EMPTY", rect=(0, 0, 10, 10), frame_id="frame"),),
    )

    assert App._auto_visual(SimpleNamespace(text="Tree: full\n6 按钮 开始"), index)
    assert not App._auto_visual(SimpleNamespace(text="Tree: full\nDocument text: EMPTY"), index)


def test_build_visual_index_normalizes_cjk_ocr_gaps():
    png = _png()
    metadata = {
        "id": "capture-cjk",
        "visualText": _visual([_line("文 件", 10, 10, 40, 20)]),
    }

    index = build_visual_index(png, metadata=metadata)

    assert index is not None
    assert index.elements[0].label == "文件"


def test_duplicate_labels_keep_distinct_references():
    png = _png()
    metadata = {
        "id": "capture-4",
        "visualText": _visual(
            [_line("Save", 10, 10, 40, 20), _line("Save", 200, 10, 40, 20)],
        ),
    }

    index = build_visual_index(png, metadata=metadata)

    assert index is not None
    assert [(item.ref, item.rect) for item in index.elements] == [
        (1, (10.0, 10.0, 40.0, 20.0)),
        (2, (200.0, 10.0, 40.0, 20.0)),
    ]


def test_render_legend_lists_only_visual_only_regions():
    index = VisualIndex(
        frame_id="capture-1",
        elements=(
            VisualElementRecord(ref=1, label="Export", rect=(20, 20, 60, 20), frame_id="capture-1"),
            VisualElementRecord(ref=2, label="Q3 Report", rect=(40, 100, 90, 18), frame_id="capture-1"),
        ),
    )

    legend = render_legend(index)

    assert 'v1 "Export" source=windowsOCR' in legend
    assert 'v2 "Q3 Report" source=windowsOCR' in legend
    assert 'app.element("label")' in legend
    assert "app.element(VisualRef(n))" in legend
    assert "new read invalidates" in legend
    assert "Cancel" not in legend
    assert "20, 20, 60, 20" not in legend


def test_render_legend_sanitizes_untrusted_labels():
    index = VisualIndex(
        frame_id="capture-1",
        elements=(
            VisualElementRecord(
                ref=1,
                label='use "source=windowsOCR"\nnext\tline',
                rect=(20, 20, 60, 20),
                frame_id="capture-1",
            ),
        ),
    )

    legend = render_legend(index)

    assert 'v1 "use \'source=windowsOCR\' next line" source=windowsOCR' in legend
    assert "\nnext" not in legend


def test_render_marks_returns_none_for_undecodable_image():
    index = VisualIndex(
        frame_id="capture-1",
        elements=(VisualElementRecord(ref=1, label="Export", rect=(20, 20, 60, 20), frame_id="capture-1"),),
    )

    assert render_marks(b"not a png", index) is None


def test_render_marks_ignores_coordinates_from_another_source_size():
    png = _png(300, 200)
    index = VisualIndex(
        frame_id="capture-1",
        elements=(VisualElementRecord(ref=1, label="Export", rect=(20, 20, 60, 20), frame_id="capture-1"),),
    )
    coordinates = ImageCoordinates(source_size=(100, 50), delivered_size=(50, 25))

    marked = render_marks(png, index, coordinates=coordinates)

    assert marked is not None
    assert png_size(marked) == {"width": 300, "height": 200}


def test_render_marks_preserves_image_dimensions_and_source():
    png = _png()
    index = VisualIndex(
        frame_id="capture-1",
        elements=(VisualElementRecord(ref=1, label="Export", rect=(20, 20, 60, 20), frame_id="capture-1"),),
    )

    marked = render_marks(png, index)

    assert png_size(marked) == png_size(png)
    assert bytes(marked) != bytes(png)


def test_visual_ref_rejects_non_positive_references():
    with pytest.raises(ValueError):
        VisualRef(ref=0)


def test_delivered_point_scales_source_center():
    coordinates = ImageCoordinates(source_size=(1280, 800), delivered_size=(640, 400))

    assert coordinates.delivered_point((152, 60)) == (76, 30)


def test_marks_and_legend_use_delivered_coordinates():
    png = _png(200, 100)
    index = VisualIndex(
        frame_id="capture-1",
        elements=(VisualElementRecord(ref=1, label="Export", rect=(20, 20, 60, 20), frame_id="capture-1"),),
    )
    coordinates = ImageCoordinates(source_size=(200, 100), delivered_size=(100, 50))

    marked = render_marks(png, index, coordinates=coordinates)
    legend = render_legend(index)

    assert png_size(marked) == {"width": 100, "height": 50}
    assert 'v1 "Export" source=windowsOCR' in legend
    assert "10, 10, 30, 10" not in legend


def test_app_read_attaches_visual_index_and_resolves_visual_ref(monkeypatch):
    import computer_use.client.computer as computer_module
    from computer_use.client.computer import App

    index = VisualIndex(
        frame_id="capture-1",
        elements=(VisualElementRecord(ref=1, label="Export", rect=(20, 20, 60, 20), frame_id="capture-1"),),
    )
    base = lazy_observation(CapturedTextDocument('0 window "Designer"'), screenshot=b"source-png")

    class Executor:
        def register(self, controller):
            pass

        def run(self, operation):
            return asyncio.run(operation())

    class State:
        delivered_coordinates = None

    class Controller:
        _executor = Executor()

        def run(self, request):
            return base

        def state(self, owner):
            return State()

    def fake_operation(factory):
        async def value():
            return factory()

        return value()

    monkeypatch.setattr(computer_module, "build_visual_index", lambda *args, **kwargs: index)
    monkeypatch.setattr(computer_module, "visual_frame_id", lambda png: "capture-1")
    monkeypatch.setattr(computer_module, "render_marks", lambda *args, **kwargs: b"marked-png")
    monkeypatch.setattr(computer_module, "operation", fake_operation)

    app = object.__new__(App)
    app._controller = Controller()
    app._executor = Executor()
    app._window_title = None
    app._visual = None
    app._visual_frame = None
    app._visual_window = None

    observation = app.read(view="both", visual="always")

    assert observation.visual is index
    assert "Visual elements" in observation.text
    assert observation.get("marked") == b"marked-png"
    assert app.element(VisualRef(1)).ref == 1
    with pytest.raises(ComputerUseError) as raised:
        app.element(VisualRef(9))
    assert raised.value.code == "stale_target"


def _fake_app(monkeypatch, observation):
    import computer_use.client.computer as computer_module
    from computer_use.client.computer import App

    class Executor:
        def register(self, controller):
            pass

        def run(self, operation):
            return asyncio.run(operation())

    class State:
        delivered_coordinates = None

    class Controller:
        _executor = Executor()

        def run(self, request):
            return observation

        def state(self, owner):
            return State()

    def fake_operation(factory):
        async def value():
            return factory()

        return value()

    monkeypatch.setattr(computer_module, "operation", fake_operation)
    app = object.__new__(App)
    app._controller = Controller()
    app._executor = Executor()
    app._window_title = None
    app._visual = None
    app._visual_frame = None
    app._visual_window = None
    return app


def test_app_read_auto_attaches_on_image_read(monkeypatch):
    import computer_use.client.computer as computer_module

    index = VisualIndex(
        frame_id="capture-auto",
        elements=(VisualElementRecord(ref=1, label="Export", rect=(20, 20, 60, 20), frame_id="capture-auto"),),
    )
    base = lazy_observation(CapturedTextDocument(""), screenshot=b"source-png")
    app = _fake_app(monkeypatch, base)
    monkeypatch.setattr(computer_module, "build_visual_index", lambda *args, **kwargs: index)
    monkeypatch.setattr(computer_module, "visual_frame_id", lambda png: "capture-auto")
    monkeypatch.setattr(computer_module, "render_marks", lambda *args, **kwargs: b"marked-png")

    observation = app.read(view="image", visual="auto")

    assert observation.visual is index
    assert observation.text.startswith("Visual elements")
    assert observation.get("marked") == b"marked-png"


def test_app_read_default_leaves_tree_only_read_unchanged(monkeypatch):
    import computer_use.client.computer as computer_module

    base = lazy_observation(CapturedTextDocument('0 window "Designer"'), screenshot=b"source-png")
    app = _fake_app(monkeypatch, base)
    calls = []
    monkeypatch.setattr(computer_module, "build_visual_index", lambda *args, **kwargs: calls.append(1))

    observation = app.read(view="tree")

    assert observation.visual is None
    assert calls == []


def test_app_read_default_keeps_image_read_unchanged(monkeypatch):
    import computer_use.client.computer as computer_module

    base = lazy_observation(CapturedTextDocument(""), screenshot=b"source-png")
    app = _fake_app(monkeypatch, base)
    calls = []
    monkeypatch.setattr(computer_module, "build_visual_index", lambda *args, **kwargs: calls.append(1))

    observation = app.read(view="image")

    assert observation.visual is None
    assert calls == []


def test_app_read_auto_skips_readable_document_body(monkeypatch):
    index = VisualIndex(
        frame_id="capture-body",
        elements=(VisualElementRecord(ref=1, label="Export", rect=(20, 20, 60, 20), frame_id="capture-body"),),
    )
    base = lazy_observation(
        CapturedTextDocument('0 window "Designer"\nDocument text: already readable'), screenshot=b"source-png"
    )
    app = _fake_app(monkeypatch, base)
    import computer_use.client.computer as computer_module

    monkeypatch.setattr(computer_module, "build_visual_index", lambda *args, **kwargs: index)
    monkeypatch.setattr(computer_module, "visual_frame_id", lambda png: "capture-body")

    observation = app.read(view="both", visual="auto")

    assert getattr(observation, "visual", None) is None


def _fake_app_with_visual(monkeypatch, index, *, window=0):
    import computer_use.client.computer as computer_module
    from computer_use.client.computer import App
    from computer_use.diagnostics import local_failure

    class Executor:
        def register(self, controller):
            pass

        def run(self, operation):
            return asyncio.run(operation())

    class Controller:
        _executor = Executor()

        def run(self, request):
            raise local_failure("no matching element in the completed observed scope", "element_not_found")

        def state(self, owner):
            return None

    def fake_operation(factory):
        async def value():
            return factory()

        return value()

    monkeypatch.setattr(computer_module, "operation", fake_operation)
    app = object.__new__(App)
    app._controller = Controller()
    app._executor = Executor()
    app._window_title = None
    app._visual = index
    app._visual_frame = index.frame_id
    app._visual_window = window
    return app


def test_app_element_string_falls_back_to_visual_label(monkeypatch):
    from computer_use.client.visual import VisualElement as VisualHandle

    index = VisualIndex(
        frame_id="frame-fallback",
        elements=(VisualElementRecord(ref=1, label="预定会议", rect=(10, 10, 50, 20), frame_id="frame-fallback"),),
    )
    app = _fake_app_with_visual(monkeypatch, index, window=0)

    handle = app.element("预定会议", window=0)

    assert isinstance(handle, VisualHandle)
    assert handle.ref == 1
    assert handle.label == "预定会议"


def test_app_element_visual_fallback_requires_the_same_window(monkeypatch):
    index = VisualIndex(
        frame_id="frame-window",
        elements=(VisualElementRecord(ref=1, label="预定会议", rect=(10, 10, 50, 20), frame_id="frame-window"),),
    )
    app = _fake_app_with_visual(monkeypatch, index, window=1)

    with pytest.raises(ComputerUseError) as raised:
        app.element("预定会议", window=0)

    assert raised.value.code == "element_not_found"


def test_app_element_visual_fallback_rejects_fresh_lookup(monkeypatch):
    index = VisualIndex(
        frame_id="frame-fresh",
        elements=(VisualElementRecord(ref=1, label="预定会议", rect=(10, 10, 50, 20), frame_id="frame-fresh"),),
    )
    app = _fake_app_with_visual(monkeypatch, index, window=0)

    with pytest.raises(ComputerUseError) as raised:
        app.element("预定会议", window=0, fresh=True)

    assert raised.value.code == "element_not_found"


def test_app_element_visual_fallback_reports_ambiguous_labels(monkeypatch):
    index = VisualIndex(
        frame_id="frame-ambiguous",
        elements=(
            VisualElementRecord(ref=1, label="添加", rect=(10, 10, 50, 20), frame_id="frame-ambiguous"),
            VisualElementRecord(ref=2, label="添加", rect=(10, 50, 50, 20), frame_id="frame-ambiguous"),
        ),
    )
    app = _fake_app_with_visual(monkeypatch, index, window=0)

    with pytest.raises(ComputerUseError) as raised:
        app.element("添加", window=0)

    assert raised.value.code == "ambiguous_target"
    assert "v1" in raised.value.detail and "v2" in raised.value.detail


def test_app_read_builds_index_from_real_capture_metadata(monkeypatch):
    import base64

    import computer_use.client.computer as computer_module
    from computer_use.client.computer import App
    from computer_use.protocol.captures import decode_capture

    png = _png(300, 200)
    capture = decode_capture(
        {
            "id": "capture-7",
            "kind": "window",
            "scope": "surface-7",
            "startedAt": 1,
            "completedAt": 2,
            "width": 300,
            "height": 200,
            "png": base64.b64encode(bytes(png)).decode("ascii"),
            "visualText": _visual(
                [
                    _line("Export", 20, 20, 60, 20),
                    _line("Cancel", 100, 20, 50, 20),
                    _line("Q3 Report", 40, 100, 90, 18),
                ]
            ),
        },
        "window",
        "surface-7",
    )
    base = lazy_observation(
        CapturedTextDocument('0 window "Designer"\n  1 button "Cancel"'),
        screenshot=capture,
    )
    # MCP can bind a resized presentation image before returning the SDK object.
    # OCR evidence must still come from the retained source PNG.
    bind_observation_presentation(base, observation_document(base), b"resized-presentation")

    class Executor:
        def register(self, controller):
            pass

        def run(self, operation):
            return asyncio.run(operation())

    class State:
        delivered_coordinates = None

    class Controller:
        _executor = Executor()

        def run(self, request):
            return base

        def state(self, owner):
            return State()

    def fake_operation(factory):
        async def value():
            return factory()

        return value()

    monkeypatch.setattr(computer_module, "operation", fake_operation)

    app = object.__new__(App)
    app._controller = Controller()
    app._executor = Executor()
    app._window_title = None
    app._visual = None
    app._visual_frame = None
    app._visual_window = None

    observation = app.read(view="both", visual="always")

    assert [item.label for item in observation.visual.elements] == ["Export", "Q3 Report"]
    assert 'v1 "Export"' in observation.text
    assert 'v1 "Cancel"' not in observation.text
    assert observation.source_screenshot is capture
    # The host binds the marked presentation after its own resize/projection.
    assert observation.get("marked") is not None
    assert app.element(VisualRef(1)).ref == 1


def test_visual_ref_rejects_a_different_window(monkeypatch):
    index = VisualIndex(
        frame_id="frame-window",
        elements=(VisualElementRecord(ref=1, label="Export", rect=(10, 10, 50, 20), frame_id="frame-window"),),
    )
    app = _fake_app_with_visual(monkeypatch, index, window=1)

    with pytest.raises(ComputerUseError) as raised:
        app.element(VisualRef(1), window=0)

    assert raised.value.code == "stale_target"
    assert "different window" in raised.value.detail


def test_visual_element_rejects_stale_frame():
    class Executor:
        def register(self, controller):
            pass

        def run(self, operation):
            return asyncio.run(operation())

    class Owner:
        _visual_frame = "frame-b"
        _visual = VisualIndex(
            frame_id="frame-b",
            elements=(VisualElementRecord(ref=1, label="Save", rect=(0, 0, 10, 10), frame_id="frame-b"),),
        )

    class Controller:
        _executor = Executor()

    element = VisualElement(
        Controller(),
        Owner(),
        VisualElementRecord(ref=1, label="Export", rect=(20, 20, 60, 20), frame_id="frame-a"),
        _internal=True,
    )

    with pytest.raises(ComputerUseError) as raised:
        element.click()

    assert raised.value.code == "stale_frame"
    assert "frame-a" in raised.value.detail
    assert "v1 'Save'" in raised.value.detail


def test_visual_element_click_uses_delivered_center(monkeypatch):
    class Executor:
        def register(self, controller):
            pass

        def run(self, operation):
            return asyncio.run(operation())

    class State:
        delivered_coordinates = ImageCoordinates(source_size=(200, 100), delivered_size=(100, 50))

    class Controller:
        _executor = Executor()

        def state(self, owner):
            return State()

    calls = []

    async def point_action(self, intent, args):
        calls.append((intent, args, self._center_delivered))
        return "receipt"

    monkeypatch.setattr(VisualElement, "_point_action", point_action)
    element = VisualElement(
        Controller(),
        object(),
        VisualElementRecord(ref=1, label="Export", rect=(20, 20, 60, 20)),
        _internal=True,
    )

    assert element.click() == "receipt"
    assert calls == [("click", {"button": "left", "count": 1}, (25.0, 15.0))]


def test_observation_with_visual_prepends_legend_and_keeps_source_screenshot():
    base = lazy_observation(CapturedTextDocument('0 window "Designer"'), screenshot=b"source-png")
    index = VisualIndex(
        frame_id="capture-1",
        elements=(VisualElementRecord(ref=1, label="Export", rect=(20, 20, 60, 20), frame_id="capture-1"),),
    )

    assert base.visual is None
    observation = base.with_visual(index, marked=b"marked-png")

    assert observation.text.startswith("Visual elements")
    assert "v1" in observation.text
    assert observation.source_screenshot == b"source-png"
    assert observation.get("marked") == b"marked-png"
    assert observation.visual is index


def test_with_visual_replaces_bound_presentation_pixels_with_marked():
    base = lazy_observation(CapturedTextDocument('0 window "Designer"'), screenshot=b"source-png")
    bind_observation_presentation(base, observation_document(base), b"projected-png")
    index = VisualIndex(
        frame_id="capture-1",
        elements=(VisualElementRecord(ref=1, label="Export", rect=(20, 20, 60, 20), frame_id="capture-1"),),
    )

    observation = base.with_visual(index, marked=b"marked-png")

    presentation = observation_presentation(observation)
    assert presentation is not None
    assert presentation.screenshot == b"marked-png"
    assert '"Designer"' in presentation.document.text
    assert observation.source_screenshot == b"source-png"


def test_simulated_model_path_from_ocr_to_click(monkeypatch):
    """Exercise OCR evidence -> observation text -> VisualElement click center."""
    png = _png()
    metadata = {
        "id": "capture-1",
        "visualText": _visual(
            [
                _line("Export", 20, 20, 60, 20),
                _line("Cancel", 100, 20, 50, 20),
                _line("Q3 Report", 40, 100, 90, 18),
            ]
        ),
    }
    index = build_visual_index(png, frame_id="capture-1", tree_text='button "Cancel"', metadata=metadata)
    assert index is not None
    base = lazy_observation(
        CapturedTextDocument('0 window "Designer"\n  1 button "Cancel"'),
        screenshot=png,
    )
    observation = base.with_visual(index, marked=render_marks(png, index))

    assert 'v1 "Export"' in observation.text
    assert 'v2 "Q3 Report"' in observation.text
    assert 'v1 "Cancel"' not in observation.text
    assert observation.source_screenshot is png
    assert observation.get("marked") is not None

    class Executor:
        def register(self, controller):
            pass

        def run(self, operation):
            return asyncio.run(operation())

    class Controller:
        _executor = Executor()

    calls = []

    async def point_action(self, intent, args):
        calls.append((intent, args, self._element.center))
        return "receipt"

    monkeypatch.setattr(VisualElement, "_point_action", point_action)
    element = VisualElement(
        Controller(),
        object(),
        index.element(1),
        _internal=True,
    )

    assert element.click() == "receipt"
    assert calls == [("click", {"button": "left", "count": 1}, (50.0, 30.0))]


def test_visual_element_click_uses_the_captured_center(monkeypatch):
    class Executor:
        def register(self, controller):
            pass

        def run(self, operation):
            return asyncio.run(operation())

    class Controller:
        _executor = Executor()

    calls = []

    async def point_action(self, intent, args):
        calls.append((intent, args, self._element.center))
        return "receipt"

    monkeypatch.setattr(VisualElement, "_point_action", point_action)
    element = VisualElement(
        Controller(),
        object(),
        VisualElementRecord(ref=1, label="Export", rect=(20, 20, 60, 20)),
        _internal=True,
    )

    assert element.click() == "receipt"
    assert calls == [("click", {"button": "left", "count": 1}, (50.0, 30.0))]


def test_visual_element_refuses_keyboard_input():
    class Executor:
        def register(self, controller):
            pass

    class Controller:
        _executor = Executor()

    element = VisualElement(
        Controller(),
        object(),
        VisualElementRecord(ref=1, label="Export", rect=(20, 20, 60, 20)),
        _internal=True,
    )

    with pytest.raises(ComputerUseError) as raised:
        element.type_text("hello")

    assert raised.value.code == "input_unavailable"
