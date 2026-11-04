"""Startup window choices retain their evidence without obsolete error fields."""

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.app import NativeAppReader
from computer_use.interaction.read.model import WindowSelectionRequired


def test_startup_window_choice_preserves_directory_and_launch_receipt():
    error = ComputerUseError(
        {"code": "ambiguous_target", "effect": "none", "target": "window", "detail": "Several windows are available."}
    )
    directory = {"application": "instance:fixture", "surfaces": [{"window": "one"}, {"window": "two"}]}
    launch = {
        "application": {"id": "instance:fixture", "displayName": "Fixture"},
        "dispatch": "dispatched",
        "detail": "The selected application started.",
    }

    choice = WindowSelectionRequired(error, "instance:fixture", "Fixture", directory, launch)
    annotated = NativeAppReader._after_launch(choice, launch)

    assert isinstance(annotated, WindowSelectionRequired)
    assert annotated.application == "instance:fixture"
    assert annotated.directory is directory
    assert annotated.launch is launch
    assert annotated.code == "ambiguous_target"
    assert annotated.effect == "possible"
    assert "The selected application started." in annotated.detail
    assert "Several windows are available." in annotated.detail
