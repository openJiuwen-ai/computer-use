"""Image layout must prove an inverse before authorizing a UI coordinate."""

import unittest
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace as R
from unittest.mock import patch

from PIL import Image
from sdk.interaction.read.acquisition.images.test_window_overview import preview

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.images.composite.pipeline import InterfaceImagePipeline
from computer_use.interaction.read.presentation.content import DisplayText, render_text
from computer_use.interaction.read.views.pipeline import WindowPart
from computer_use.native.contracts import Binding
from computer_use.protocol.captures import captureMetadata


class CompositeTests(unittest.TestCase):
    def setup(self, windows):
        root = Binding("session", "instance", "window-0", "surface-0")
        members = {
            f"window-{ref}": dict(
                window=f"window-{ref}",
                surface=f"surface-{ref}",
                parent=f"window-{parent}" if parent is not None else None,
                parentState="known" if parent is not None else "none",
                modal=modal,
                title=ref,
            )
            for ref, parent, modal in windows
        }
        instance = R(
            anchor=R(root=root, name="Fixture"),
            members=members,
            windows={int(ref): f"window-{ref}" for ref, parent, modal in windows},
            directory={"surfaces": list(members.values())},
            selector="Fixture",
        )
        self.ui = R(_instance=instance, _anchor=None, _image=None)

        # Real weak-referenceable owners are required by image authority.
        class Owner:
            pass

        owner = Owner()
        owner.__dict__.update(vars(self.ui))
        self.ui = owner
        self.registered = []

        class Controller:
            pass

        self.controller = Controller()
        self.controller.transport = R(session="session")
        self.controller.adapters = R(
            register_image=lambda *args: self.registered.append(args), image_delivery_epoch=lambda: 0
        )
        self.pipeline = InterfaceImagePipeline(self.controller)

    @staticmethod
    def part(ref, xy, size=(100, 60), color="red", *, matrix=None):
        value = preview(str(ref), color, size).image
        value = replace(value, coordinates={"nativeSpace": "screen", "toNative": matrix or [1, 0, 0, 1, *xy]})
        return WindowPart(replace(value.target, capture=None), image=value)

    def test_composite_retains_context_and_partial_notes_until_terminal_formatting(self):
        from computer_use.interaction.read.presentation import content

        self.setup([("0", None, False), ("1", "0", True)])
        part = self.part(0, (0, 0))
        with patch.object(content, "render_text", wraps=content.render_text) as renderer:
            canvas, notes = self.pipeline.run(self.ui, [part])
            self.assertEqual(renderer.call_count, 0)
            self.assertIs(canvas.png, part.image.image)
            self.assertTrue(all(isinstance(note, DisplayText) for note in notes))
            context = self.registered[0][1]["context"]
            self.assertIsInstance(context, DisplayText)
            self.ui._instance.anchor.name = "Later app name"
            self.assertEqual(render_text(context), "App instance Fixture; overall view")
            self.assertIn("only window 0", render_text(notes[0]))
            self.assertTrue(canvas.unavailable_mapping)
            with self.assertRaises(ComputerUseError):
                canvas.hit((1, 1))

    def test_owned_popup_keeps_actual_layout_and_round_trips_to_native_capture_pixels(self):
        self.setup([("0", None, False), ("1", "0", True)])
        root, child = self.part(0, (100, 200)), self.part(1, (120, 210), (30, 20), "blue")
        canvas, notes = self.pipeline.run(self.ui, [child, root])
        self.assertFalse(canvas.unavailable_mapping)
        self.assertEqual(captureMetadata(canvas.png)["width"], 100)
        with Image.open(BytesIO(canvas.png)) as pixels:
            self.assertEqual(pixels.getpixel((25, 15))[:3], (0, 0, 255))
            self.assertEqual(pixels.getpixel((5, 5))[:3], (255, 0, 0))
        piece, point = canvas.hit((25, 15))
        self.assertEqual((piece.window, point), ("window-1", (5, 5)))
        self.assertEqual(piece.image.target, child.image.target)
        self.assertIn("captured separately", " ".join(render_text(note) for note in notes))

    def test_unproved_sibling_stacking_degrades_explicitly_without_coordinate_authority(self):
        self.setup([("0", None, False), ("1", "0", False), ("2", "0", False)])
        parts = [self.part(0, (0, 0)), self.part(1, (10, 10), (30, 30)), self.part(2, (20, 20), (30, 30))]
        canvas, notes = self.pipeline.run(self.ui, parts)
        self.assertTrue(canvas.unavailable_mapping)
        self.assertIn("stacking order", " ".join(render_text(note) for note in notes))
        with self.assertRaises(ComputerUseError):
            canvas.hit((1, 1))

    def test_missing_member_capture_remains_visible_as_a_partial_view(self):
        self.setup([("0", None, False), ("1", "0", True)])
        canvas, notes = self.pipeline.run(self.ui, [self.part(0, (0, 0))])
        self.assertTrue(canvas.unavailable_mapping)
        self.assertIn("not the complete instance layout", " ".join(render_text(note) for note in notes))
        with self.assertRaises(ComputerUseError):
            canvas.hit((1, 1))

    def test_canvas_gaps_and_cross_ui_coordinates_do_not_gain_authority(self):
        self.setup([("0", None, False), ("1", "0", False)])
        canvas, _ = self.pipeline.run(self.ui, [self.part(0, (0, 0), (10, 10)), self.part(1, (20, 0), (10, 10))])
        self.assertEqual(captureMetadata(canvas.png)["width"], 30)
        self.assertEqual(canvas.hit((25, 5))[1], (5, 5))
        with self.assertRaises(ComputerUseError):
            canvas.hit((15, 5))
        with self.assertRaises(ComputerUseError):
            canvas.hit((30, 5))

    def test_single_window_needs_only_its_own_map_not_a_desktop_rectangular_transform(self):
        self.setup([("0", None, False)])
        part = self.part(0, (0, 0), matrix=[0, 1, -1, 0, 100, 100])
        canvas, _ = self.pipeline.run(self.ui, [part])
        self.assertFalse(canvas.unavailable_mapping)
        self.assertIs(canvas.png, part.image.image)
        self.assertEqual(canvas.hit((3, 4))[1], (3, 4))
