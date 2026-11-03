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


def test_late_old_window_failure_does_not_remove_replacement_or_its_content():
    from computer_use.interaction.query.instances.scope import retain_fixed_failure
    from computer_use.native.contracts import Knowledge
    from computer_use.native.scopes import FixedWindowInvalidated, WorkingWindow
    from computer_use.session.interfaces import WindowLifetime, WindowMembership

    state = InstanceState("app")
    old = WindowIdentity("slot", "old-surface", "old-instance")
    new = WindowIdentity("slot", "new-surface", "new-instance")
    old_item = {"window": old.window, "surface": old.surface, "instance": old.instance}
    new_item = {"window": new.window, "surface": new.surface, "instance": new.instance}
    state.reconcile({"surfaces": [old_item]}, DirectoryMembership(True, (WindowMembership(old, Knowledge.YES),)))
    state.refs["old"] = {"window_identity": old, "text": "old content"}
    state.refs["new"] = {"window_identity": new, "text": "new content"}
    state.window_images[new] = image = object()
    state.reconcile({"surfaces": [new_item]}, DirectoryMembership(True, (WindowMembership(new, Knowledge.YES),)))
    old_selection = WorkingWindow("app", "App", Binding("session", old.instance, old.window, old.surface, None))
    new_selection = WorkingWindow("app", "App", Binding("session", new.instance, new.window, new.surface, None))
    state.anchor = new_selection

    # The old in-flight read can report its explicit failure after a new
    # catalogue has already proved the replacement window's lifetime.
    retain_fixed_failure(state, old_selection, FixedWindowInvalidated(old_selection.root, "old window ended"))

    assert state.evidence[old].lifetime is WindowLifetime.RETIRED
    assert state.evidence[new].lifetime is WindowLifetime.RETAINED
    assert state.members["slot"] == new_item
    assert state.refs["old"]["text"] is None
    assert state.refs["new"]["text"] == "new content"
    assert state.window_images[new] is image
    assert state.anchor is new_selection

    retain_fixed_failure(state, new_selection, FixedWindowInvalidated(new_selection.root, "new window ended"))
    assert "slot" not in state.members
    assert state.refs["new"]["text"] is None
    assert new not in state.window_images
    assert state.anchor is None


def test_partial_absence_keeps_identity_payload_and_can_recover_same_reference():
    from computer_use.native.contracts import Knowledge
    from computer_use.session.interfaces import WindowLifetime, WindowMembership

    windows = WindowReferences()
    state = windows.application("session", "app", "App")
    identity = WindowIdentity("window", "surface", "instance")
    item = {"window": identity.window, "surface": identity.surface, "instance": identity.instance, "nativeWindow": True}
    observed = {"session": "session", "application": "app", "surfaces": [item]}
    known = DirectoryMembership(True, (WindowMembership(identity, Knowledge.YES),))
    state.reconcile(observed, known)
    number = next(iter(windows.register_directory(observed)))
    state.refs["7"] = {"window_identity": identity, "text": "retained"}
    state.window_images[identity] = image = object()

    state.reconcile({"surfaces": []}, DirectoryMembership(False, ()))
    assert state.evidence[identity].lifetime is WindowLifetime.RETAINED
    assert state.evidence[identity].membership is Knowledge.UNKNOWN
    assert state.refs["7"]["text"] == "retained"
    assert state.window_images[identity] is image

    state.reconcile(observed, known)
    assert next(iter(windows.register_directory(observed))) == number
    assert state.evidence[identity].membership is Knowledge.YES
    assert state.refs["7"]["text"] == "retained"

    # A complete absence is a different fact. Reappearance cannot revive the
    # exact old lifetime; it needs a new native identity and a new public ref.
    state.reconcile({"surfaces": []}, DirectoryMembership(True, ()))
    state.reconcile(observed, known)
    assert state.evidence[identity].lifetime is WindowLifetime.RETIRED
    assert "window" not in state.members
    assert state.refs["7"]["text"] is None
    assert identity not in state.window_images


def test_retirement_releases_menu_payload_for_exact_owner_only():
    state = InstanceState("app")
    old = WindowIdentity("old-window", "old-surface", "old-instance")
    live = WindowIdentity("live-window", "live-surface", "live-instance")
    menu_binding = Binding("session", "menu-instance", None, "menu-surface", "menu-node")
    old_snapshot = {"binding": menu_binding, "menu_window_identity": old, "source": {"ref": "old-menu"}, "text": "menu"}
    state.refs["old-menu"] = old_snapshot
    state.refs["live-menu"] = {"binding": menu_binding, "menu_window_identity": live, "text": "live menu"}
    state.refs["live-element"] = {
        "binding": Binding("session", live.instance, live.window, live.surface, "node"),
        "text": "live element",
    }

    state.retire(old)

    assert state.refs["old-menu"]["text"] is None
    assert state.refs["old-menu"]["binding"] == menu_binding
    assert state.refs["old-menu"]["source"] == {"ref": "old-menu"}
    assert old_snapshot["text"] == "menu"
    assert state.refs["live-menu"]["text"] == "live menu"
    assert state.refs["live-element"]["text"] == "live element"


def test_refreshing_historical_content_does_not_reuse_or_mutate_held_snapshot():
    bindings = SessionBindings()
    state = AppState("app")
    held = {"binding": object(), "text": "original"}
    bindings.remember(state, {"0": held})
    for index in range(1, 8192):
        bindings.remember(state, {str(index): {"binding": object(), "text": "other"}})
    fresh = dict(held, text="fresh")
    bindings.remember(state, {"0": fresh})
    bindings.remember(state, {"8192": {"binding": object(), "text": "new"}})

    assert state.refs["0"] is fresh
    assert state.refs["0"]["binding"] is held["binding"]
    assert state.refs["0"]["text"] == "fresh"
    assert state.refs["1"]["text"] is None
    assert held["text"] == "original"
