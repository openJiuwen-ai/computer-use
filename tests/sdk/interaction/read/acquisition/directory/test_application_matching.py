"""Native catalogue declarations choose a running identity without merging it."""

import unittest
from copy import deepcopy

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.directory.service import select_application
from computer_use.protocol.directory import decodeDirectory


class ApplicationMatchingTests(unittest.TestCase):
    def setUp(self):
        self.running = dict(
            id="process:1",
            displayName="internal-process",
            isRunning=True,
            path=None,
            identifier=None,
            matchKeys=["native.window:Editor"],
        )
        self.installed = dict(
            id="recipe:1",
            displayName="Document Editor",
            isRunning=False,
            path="/catalogue/editor.desktop",
            identifier="editor.desktop",
            matchKeys=["native.window:Editor"],
        )
        self.directory = dict(
            kind="applications",
            session="session",
            platform="linux",
            observed=dict(id="observation", startedAt=1, completedAt=2),
            applications=[self.running, self.installed],
            stopReasons=[],
        )

    def test_all_installation_selectors_resolve_the_unique_observed_running_identity(self):
        directory = decodeDirectory(self.directory)
        before = deepcopy(directory)
        for selector in (
            "Document Editor",
            "document editor",
            "editor.desktop",
            "/catalogue/editor.desktop",
            "recipe:1",
        ):
            with self.subTest(selector=selector):
                result = select_application(directory, selector)
                self.assertEqual(result, self.running)
                self.assertIsNone(result["path"])
                self.assertIsNone(result["identifier"])
        self.assertEqual(directory, before)

    def test_two_running_instances_require_an_explicit_running_id(self):
        self.directory["applications"].append({**self.running, "id": "process:2"})
        for selector in ("Document Editor", "recipe:1"):
            with self.assertRaises(ComputerUseError) as error:
                select_application(self.directory, selector)
            self.assertEqual(error.exception.code, "ambiguous_target")
        self.assertEqual(select_application(self.directory, "process:1"), self.running)

    def test_two_launch_recipes_with_one_shared_hint_cannot_claim_the_same_process(self):
        self.directory["applications"].append({**self.installed, "id": "recipe:2", "displayName": "Other Profile"})
        with self.assertRaises(ComputerUseError) as error:
            select_application(self.directory, "Document Editor")
        self.assertEqual(error.exception.code, "ambiguous_target")

    def test_partial_catalogue_cannot_prove_a_unique_association_but_exact_running_id_works(self):
        self.directory["stopReasons"] = ["A native provider could not enumerate all entries"]
        for selector in ("Document Editor", "recipe:1"):
            with self.assertRaises(ComputerUseError) as error:
                select_application(self.directory, selector)
            self.assertEqual(error.exception.code, "observation_incomplete")
        self.assertEqual(select_application(self.directory, "process:1"), self.running)

    def test_missing_or_different_case_keys_do_not_guess_a_process_by_its_name(self):
        for keys in ([], ["native.window:editor"], ["other.native:Editor"]):
            with self.subTest(keys=keys):
                self.running["matchKeys"] = keys
                self.assertEqual(select_application(self.directory, "Document Editor"), self.installed)

    def test_retained_selection_is_not_rebound_when_a_later_catalogue_changes(self):
        selected = select_application(self.directory, "Document Editor")
        self.running["id"] = "process:new-generation"
        self.assertEqual(selected["id"], "process:1")
        self.assertEqual(select_application(self.directory, "Document Editor")["id"], "process:new-generation")

    def test_legacy_catalogues_without_match_keys_keep_existing_selection(self):
        del self.running["matchKeys"]
        del self.installed["matchKeys"]
        directory = decodeDirectory(self.directory)
        self.assertEqual(select_application(directory, "Document Editor"), self.installed)
        self.assertEqual(select_application(directory, "internal-process"), self.running)

    def test_keys_are_bounded_unique_nonblank_strings(self):
        for keys in (None, "native.window:Editor", [None], [""], ["x", "x"], [str(i) for i in range(65)]):
            with self.subTest(keys=keys):
                self.installed["matchKeys"] = keys
                with self.assertRaises(ValueError):
                    decodeDirectory(self.directory)


if __name__ == "__main__":
    unittest.main()
