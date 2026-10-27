"""Driver-only structural boundaries and isolated Pass/extension contracts."""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.control.input_context.passes.attach_selected_context import attach_selected_context
from computer_use_linux.control.model import NativeInputStep
from computer_use_linux.read.model import NativeQueryScope
from computer_use_linux.read.passes.check_query_boundary import check_query_boundary

ROOT = Path(__file__).resolve().parents[4] / "os_adapter/linux/src/computer_use_linux"


class NativeDirectoryStructureTests(unittest.TestCase):
    def test_native_tests_and_fixtures_mirror_existing_source_responsibilities(self):
        tests = Path(__file__).resolve().parents[1]
        self.assertFalse(list(tests.glob("test_*.py")))
        self.assertFalse(list(tests.glob("*fixture*")))
        for path in tests.rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".cpp") or path.name == "__init__.py":
                continue
            relative = path.relative_to(tests)
            self.assertTrue((ROOT / relative.parts[0]).is_dir(), str(path))
            if path.name.startswith("test_"):
                self.assertNotEqual(path.parent.name, "fixtures", str(path))
            else:
                self.assertEqual(path.parent.name, "fixtures", str(path))
        wrapper = tests / "control/fixtures/v5_launch_wrapper.py"
        self.assertTrue(wrapper.with_name("v5_launch_fixture.py").is_file())

    def test_one_pass_per_file_with_explicit_effect_and_borrow_contract(self):
        stages = list(ROOT.rglob("passes/*.py"))
        self.assertGreater(len(stages), 25)
        for path in stages:
            if path.name == "__init__.py":
                continue
            source = ast.parse(path.read_text())
            functions = [n for n in source.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            self.assertEqual([n.name for n in functions], [path.stem], str(path))
            self.assertTrue(
                any(
                    isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CONTRACT" for t in n.targets)
                    for n in source.body
                ),
                str(path),
            )
            for node in ast.walk(source):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn(
                        node.module,
                        ("computer_use_linux.runtime.native", "computer_use_linux.runtime.server"),
                        str(path),
                    )
                if isinstance(node, ast.Attribute):
                    self.assertNotEqual(node.attr, "e", str(path))

    def test_no_native_mechanism_borrows_complete_runtime(self):
        for path in ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
                    self.assertNotEqual(node.attr, "e", str(path))
            if path.parent.name != "runtime":
                for node in tree.body:
                    if isinstance(node, ast.ImportFrom):
                        self.assertNotEqual(node.module, "computer_use_linux.runtime.native", str(path))

    def test_boundary_pass_uses_only_supplied_operation_facts(self):
        target = dict(session="s", instance=None, rootWindow=None, surface=None, node=None, capture=None)
        operation = SimpleNamespace(token="one", target=target)
        scope = NativeQueryScope(operation, "one", target, dict(kind="applications"), "applications", False, True)
        check_query_boundary(scope, tuple(target))
        with self.assertRaises(Failure):
            check_query_boundary(
                NativeQueryScope(operation, "other", target, scope.request, scope.kind, False, True), tuple(target)
            )

    def test_unregistered_extension_reuses_same_step_owner_without_entering_sibling(self):
        history, sends = {}, []

        class Owner:
            def step(self, number, command, execute):
                if number not in history:
                    history[number] = execute()
                return history[number]

        owner = Owner()
        unit = SimpleNamespace(
            execute=lambda operation, command, ctx: sends.append(command) or dict(dispatch="unknown")
        )
        step = NativeInputStep(owner, 1, dict(kind="example"))

        def extension(value):
            # One local rule, no extra Runtime, registry or execution history.
            self.assertEqual(value.command["kind"], "example")
            return value.operation.step(
                value.step, value.command, lambda: unit.execute(value.operation, value.command, None)
            )

        self.assertEqual(extension(step), extension(step))
        self.assertEqual(len(sends), 1)
        self.assertEqual(len(history), 1)
        self.assertEqual(attach_selected_context({}, None, dict(original=True)), dict(original=True))


if __name__ == "__main__":
    unittest.main()
