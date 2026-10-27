import unittest

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.directory.service import (
    select_application,
    select_surface,
    selected_window,
)
from computer_use.interaction.read.app import window_summary
from computer_use.protocol.directory import decodeDirectory


def window(id, parent=None, modal=False, accessible=True):
    return dict(
        window=id,
        instance="app:" + id,
        surface="scope:" + id,
        node="node:" + id if accessible else None,
        title="Same title",
        kind="window",
        parent=parent,
        parentState="known" if parent else "none",
        modal=modal,
        nativeWindow=True,
        accessibility=accessible,
    )


def directory(*surfaces, reasons=()):
    return dict(
        kind="surfaces",
        session="session",
        platform="macos",
        application="running:1",
        observed=dict(id="obs", startedAt=1, completedAt=2),
        stopReasons=list(reasons),
        surfaces=list(surfaces),
        provider=dict(status="alreadyEnabled", attribute=None, writeStatus=None),
    )


class NativeDirectoryTests(unittest.TestCase):
    def test_accessibility_only_entry_remains_a_distinct_selectable_scope(self):
        original = directory(
            window("native"), {**window("ax"), "nativeWindow": False}, window("visual", accessible=False)
        )
        parsed = decodeDirectory(original)
        self.assertEqual(
            window_summary(parsed, {"0": "native", "1": "ax", "2": "visual"}),
            '0 "Same title"; 1 "Same title" (native window unmatched); 2 "Same title"',
        )
        self.assertEqual(parsed["surfaces"], original["surfaces"])
        for ref in ("native", "ax", "visual"):
            chosen = select_surface(parsed, ref)
            self.assertEqual(chosen.root_window, ref)
            self.assertEqual(chosen.surface, "scope:" + ref)
        with self.assertRaises(ComputerUseError) as error:
            select_surface(parsed)
        self.assertEqual(error.exception.code, "ambiguous_target")

    def test_reported_window_focus_is_metadata_not_selection_authority(self):
        original = directory(window("a"), window("b"))
        focused = decodeDirectory({**original, "focusedWindow": "b"})
        self.assertEqual(focused["focusedWindow"], "b")
        self.assertEqual(focused["surfaces"], original["surfaces"])
        self.assertEqual(
            window_summary(focused, {"0": "a", "1": "b"}), '0 "Same title"; 1 "Same title" (reported focus)'
        )
        with self.assertRaises(ComputerUseError) as caught:
            select_surface(focused)
        self.assertEqual(caught.exception.code, "ambiguous_target")
        self.assertEqual(select_surface(focused, "a"), select_surface(original, "a"))
        for reported in (None, "outside-this-directory"):
            parsed = decodeDirectory({**original, "focusedWindow": reported})
            self.assertEqual(parsed["focusedWindow"], reported)
            self.assertNotIn("reported focus", window_summary(parsed, {"0": "a", "1": "b"}))
        self.assertNotIn("focusedWindow", decodeDirectory(original))
        for invalid in (False, 0, "", []):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                decodeDirectory({**original, "focusedWindow": invalid})

    def test_running_window_catalogue_and_desktop_selectors(self):
        app = dict(
            id="application:1",
            displayName="Editor",
            isRunning=True,
            path=None,
            identifier=None,
            windows=[dict(selector="instance:a", window="a"), dict(selector="instance:b", window="b")],
        )
        value = dict(
            kind="applications",
            session="session",
            platform="windows",
            scope="runningWindows",
            observed=dict(id="obs", startedAt=1, completedAt=2),
            stopReasons=[],
            applications=[app],
        )
        parsed = decodeDirectory(value)
        self.assertEqual(select_application(parsed, "editor"), app)
        self.assertEqual(selected_window(select_application(parsed, "instance:b"), "instance:b"), "b")
        with self.assertRaises(ComputerUseError) as caught:
            select_application(parsed, "not installed")
        self.assertEqual(caught.exception.code, "element_not_found")
        self.assertIn("does not discover or launch", caught.exception.detail)
        parsed["stopReasons"] = ["UIA supplement timed out"]
        self.assertEqual(select_application(parsed, "instance:a"), app)
        with self.assertRaises(ComputerUseError) as caught:
            select_application(parsed, "editor")
        self.assertEqual(caught.exception.code, "observation_incomplete")
        for change in (
            dict(windows=app["windows"] * 2),
            dict(isRunning=False),
            dict(windows=[dict(selector=app["id"], window="a")]),
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                decodeDirectory({**value, "applications": [{**app, **change}]})
        with self.assertRaises(ValueError):
            decodeDirectory({**value, "applications": [app, {**app, "id": "application:2"}]})

    def test_application_roots_have_independent_scope_and_no_fabricated_window(self):
        root = dict(kind="menuBar", instance="app-scope", surface="menu-scope", node="menu-root")
        value = dict(
            kind="roots",
            session="s",
            platform="macos",
            application="running:1",
            observed=dict(id="roots", startedAt=1, completedAt=2),
            roots=[root],
            stopReasons=[],
        )
        parsed = decodeDirectory(value)
        self.assertEqual(parsed["roots"], [root])
        parsed["roots"][0]["node"] = "changed"
        self.assertEqual(value["roots"][0]["node"], "menu-root")
        for duplicate in ({**root, "node": "other"}, {**root, "surface": "other"}):
            with self.assertRaises(ValueError):
                decodeDirectory({**value, "roots": [root, duplicate]})
        with self.assertRaises(ValueError):
            decodeDirectory({**value, "roots": [{**root, "window": "invented"}]})
        self.assertEqual(decodeDirectory({**value, "roots": [], "stopReasons": ["provider timeout"]})["roots"], [])

    def test_application_identity_resolution_does_not_choose_between_processes(self):
        app = dict(
            id="instance:1",
            displayName="Editor",
            isRunning=True,
            path="/Applications/Editor.app",
            identifier="local.editor",
        )
        value = dict(
            kind="applications",
            session="session",
            platform="macos",
            observed=dict(id="obs", startedAt=1, completedAt=2),
            stopReasons=[],
            applications=[app],
        )
        for selector in ("instance:1", "Editor", "editor", "/Applications/Editor.app", "local.editor"):
            self.assertEqual(select_application(decodeDirectory(value), selector), app)
        value["applications"].append({**app, "id": "instance:2"})
        with self.assertRaises(ComputerUseError) as error:
            select_application(value, "local.editor")
        self.assertEqual(error.exception.code, "ambiguous_target")
        self.assertNotIn("candidates=", error.exception.detail)
        self.assertEqual(select_application(value, "instance:1"), app)
        value["applications"] = [{**app, "id": "local.editor", "isRunning": False}]
        self.assertFalse(select_application(value, "Editor")["isRunning"])

    def test_same_title_documents_require_selection_and_keep_root(self):
        value = decodeDirectory(directory(window("a"), window("b"), window("dialog", "b", True)))
        with self.assertRaises(ComputerUseError) as error:
            select_surface(value)
        self.assertEqual(error.exception.code, "ambiguous_target")
        chosen = select_surface(value, "b")
        self.assertEqual(
            (chosen.instance, chosen.root_window, chosen.surface, chosen.node),
            ("app:b", "b", "scope:dialog", "node:dialog"),
        )
        self.assertEqual(select_surface(value, "a").surface, "scope:a")

    def test_declared_application_names_preserve_identity_and_ambiguity(self):
        app = dict(
            id="instance:1",
            displayName="文本编辑",
            isRunning=True,
            path="/Applications/TextEdit.app",
            identifier="local.editor",
            aliases=["TextEdit", "Document Editor"],
        )
        value = dict(
            kind="applications",
            session="session",
            platform="macos",
            observed=dict(id="obs", startedAt=1, completedAt=2),
            stopReasons=[],
            applications=[app],
        )
        parsed = decodeDirectory(value)
        for selector in ("文本编辑", "TextEdit", " textedit ", "document editor"):
            self.assertEqual(select_application(parsed, selector), app)
        # Declared names must not turn into executable guesses, substrings or
        # localized-name replacements in listApps().
        self.assertEqual(parsed["applications"][0]["displayName"], "文本编辑")
        for selector in ("Text", "Editor", "textedit.app"):
            with self.subTest(selector=selector), self.assertRaises(ComputerUseError):
                select_application(parsed, selector)
        for second in (
            {**app, "id": "instance:2"},
            {**app, "id": "instance:2", "path": "/Other.app", "identifier": "other", "displayName": "TextEdit"},
        ):
            with self.subTest(second=second), self.assertRaises(ComputerUseError) as caught:
                select_application({**parsed, "applications": [app, second]}, "TextEdit")
            self.assertEqual(caught.exception.code, "ambiguous_target")
        partial = {**parsed, "stopReasons": ["installation discovery timed out"]}
        with self.assertRaises(ComputerUseError) as caught:
            select_application(partial, "TextEdit")
        self.assertEqual(caught.exception.code, "observation_incomplete")
        self.assertEqual(select_application(partial, "instance:1"), app)
        installed = {**app, "id": "installation:1", "isRunning": False}
        self.assertEqual(select_application({**parsed, "applications": [installed]}, "TextEdit"), installed)
        self.assertEqual(select_application({**parsed, "applications": [installed, app]}, "TextEdit"), app)

    def test_application_names_cannot_shadow_exact_identifiers_or_paths(self):
        selected = dict(
            id="installation:1",
            displayName="Installed",
            isRunning=False,
            path="/Applications/Original.app",
            identifier="local.original",
        )
        other = dict(
            id="instance:2",
            displayName="local.original",
            isRunning=True,
            path="/Applications/Other.app",
            identifier="local.other",
            aliases=["/Applications/Original.app", "installation:1"],
        )
        value = dict(applications=[other, selected], stopReasons=[])
        for selector in ("local.original", "/Applications/Original.app", "installation:1"):
            self.assertEqual(select_application(value, selector), selected)
        self.assertEqual(select_application(value, "local.other"), other)

    def test_application_name_schema_is_optional_bounded_and_copied(self):
        app = dict(id="instance:1", displayName="Editor", isRunning=True, path=None, identifier=None)
        value = dict(
            kind="applications",
            session="session",
            platform="linux",
            observed=dict(id="obs", startedAt=1, completedAt=2),
            stopReasons=[],
            applications=[app],
        )
        self.assertNotIn("aliases", decodeDirectory(value)["applications"][0])
        app["aliases"] = ["Declared name"]
        parsed = decodeDirectory(value)
        parsed["applications"][0]["aliases"].append("Changed copy")
        self.assertEqual(app["aliases"], ["Declared name"])
        for invalid in (None, "Editor", [""], ["  "], [1], ["a"] * 17):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                decodeDirectory({**value, "applications": [{**app, "aliases": invalid}]})

    def test_visual_window_is_preserved_without_inventing_a_node(self):
        chosen = select_surface(decodeDirectory(directory(window("visual", accessible=False))))
        self.assertEqual(chosen.surface, "scope:visual")
        self.assertIsNone(chosen.node)

    def test_unknown_ownership_or_coverage_never_proves_unique_window(self):
        unknown = window("a")
        unknown["parentState"] = "unknown"
        for value in (
            directory(window("a", "unread")),
            directory(window("a"), reasons=["AX failed"]),
            directory(window("a"), window("cycle", "cycle")),
            directory(unknown),
        ):
            with self.subTest(value=value):
                parsed = decodeDirectory(value)
                with self.assertRaises(ComputerUseError) as error:
                    select_surface(parsed)
                self.assertEqual(error.exception.code, "observation_incomplete")
                self.assertEqual(select_surface(parsed, "a").root_window, "a")

    def test_ambiguous_modal_and_closed_root_do_not_retarget(self):
        value = directory(window("a"), window("one", "a", True), window("two", "a", True))
        with self.assertRaises(ComputerUseError) as error:
            select_surface(value, "a")
        self.assertEqual(error.exception.code, "ambiguous_target")
        with self.assertRaises(ComputerUseError) as error:
            select_surface(directory(window("b")), "a")
        self.assertEqual(error.exception.code, "stale_target")

    def test_metadata_and_bindings_are_validated_without_dropping_visual_entries(self):
        for modify in (
            lambda v: v["surfaces"].append(v["surfaces"][0]),
            lambda v: v["surfaces"][0].update(accessibility=False),
            lambda v: v["observed"].update(completedAt=0),
            lambda v: v.update(extra=True),
        ):
            value = directory(window("a"))
            modify(value)
            with self.assertRaises(ValueError):
                decodeDirectory(value)
        original = directory(window("a"))
        decoded = decodeDirectory(original)
        decoded["surfaces"][0]["title"] = "changed"
        self.assertEqual(original["surfaces"][0]["title"], "Same title")


if __name__ == "__main__":
    unittest.main()
