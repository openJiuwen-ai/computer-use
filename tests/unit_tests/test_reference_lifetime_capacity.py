"""Observation cache pressure must not revoke still-live public identities."""

from collections import OrderedDict

from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.native.contracts import Binding
from computer_use.session.bindings import SessionBindings
from computer_use.session.interfaces import (
    ApplicationEntry,
    ApplicationReferences,
    DirectoryMembership,
    InstanceState,
    WindowIdentity,
)
from computer_use.session.state import AppState
from computer_use.session.windows import WindowReferences


def test_number_is_stable_after_unrelated_observations():
    refs = DisplayReferences("session")
    original = refs.reference("original")
    for index in range(17000):
        refs.reference(("other", index))
    assert refs.reference("original") == original


def test_application_entry_is_stable_after_other_application_discoveries():
    refs = ApplicationReferences()
    original = ApplicationEntry("session", "original", "App", True)
    number = refs.publish(original)
    for index in range(8300):
        refs.publish(ApplicationEntry("session", f"other:{index}", "Other", False))
    assert refs.publish(original) == number
    assert refs.resolve(number, "session") == original


def test_old_ref_keeps_binding_when_historical_content_is_released():
    bindings = SessionBindings()
    state = AppState("app")
    original = {"binding": object(), "source": {"ref": "0"}, "text": "old text", "children": "old children"}
    bindings.remember(state, {"0": original})
    for index in range(1, 8200):
        bindings.remember(state, {str(index): {"binding": object(), "text": "other", "children": None}})
    assert state.refs["0"]["binding"] is original["binding"]
    assert state.refs["0"]["source"] == {"ref": "0"}
    assert state.refs["0"]["text"] is None
    assert original["text"] == "old text", "an already-held Element snapshot must not be mutated"
    fresh = dict(original, text="new text")
    bindings.remember(state, {"0": fresh})
    assert state.refs["0"]["text"] == "new text", "an explicit current text read still gets its captured value"


def test_same_window_number_survives_other_window_discovery():
    windows = WindowReferences()

    def directory(index):
        return {
            "session": "session",
            "application": "app",
            "surfaces": [
                {
                    "window": f"w{index}",
                    "surface": f"s{index}",
                    "instance": f"i{index}",
                    "nativeWindow": True,
                }
            ],
        }

    initial = directory(0)
    original = int(next(iter(windows.register_directory(initial))))
    for index in range(1, 16400):
        windows.register_directory(directory(index))
    assert windows.resolve(original, "session").identity.window == "w0"
    assert int(next(iter(windows.register_directory(initial)))) == original


def test_shared_reference_table_has_one_bounded_content_history():
    bindings = SessionBindings()
    refs = OrderedDict()
    first, second = AppState("one", refs=refs), AppState("two", refs=refs)
    for index in range(8200):
        bindings.remember(first if index % 2 else second, {str(index): {"binding": index, "text": "value"}})
    assert refs["0"]["binding"] == 0
    assert refs["0"]["text"] is None
    assert refs["8199"]["text"] == "value"


def test_retired_window_releases_payload_but_preserves_action_error_provenance():
    state = InstanceState("app")
    identity = WindowIdentity("window", "surface", "instance")
    held = {
        "binding": Binding("session", "instance", "window", "surface", "node"),
        "window_identity": identity,
        "source": {"ref": "7"},
        "text": "old",
        "children": "page",
    }
    state.refs["7"] = held
    state.window_images[identity] = object()
    state.retire(identity)
    assert state.refs["7"]["binding"] == held["binding"]
    assert state.refs["7"]["source"] == held["source"]
    assert state.refs["7"]["text"] is None
    assert held["text"] == "old"
    assert identity not in state.window_images


def test_partial_observation_does_not_release_an_absent_windows_content():
    state = InstanceState("app")
    state.refs["7"] = {"text": "retained", "children": "page"}
    state.reconcile({"surfaces": []}, DirectoryMembership(False, ()))
    assert state.refs["7"]["text"] == "retained"
