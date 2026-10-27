import ast
import re
import unittest

from repository import ROOT

"Keep current SDK consumers from silently regressing to retired call forms."


def retired_calls(source, *, embedded=True):
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    errors = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if (
                method == "read"
                and node.args
                and (
                    isinstance(node.args[0], ast.Dict)
                    or isinstance(node.args[0], ast.Call)
                    and isinstance(node.args[0].func, ast.Name)
                    and node.args[0].func.id == "dict"
                )
            ):
                errors.append((node.lineno, "read expects keyword arguments"))
            if method in ("typeText", "readText") and len(node.args) > (1 if method == "typeText" else 0):
                errors.append((node.lineno, method + " options must be keywords"))
            if method == "click" and len(node.args) > 1:
                errors.append((node.lineno, "coordinate click requires at="))
            if method == "element" and isinstance(parents.get(node), ast.Attribute):
                errors.append((node.lineno, "await element() before using its handle"))
            if (
                method == "element"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.isdecimal()
            ):
                errors.append((node.lineno, "numeric element references are integers, not strings"))
            if (
                method == "find"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in ("app", "ui", "window")
            ):
                errors.append((node.lineno, "use await element(path), not find()"))
        if embedded and isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(
                "." + name + "(" in node.value for name in ("read", "click", "element", "find", "typeText", "readText")
            ):
                try:
                    nested = retired_calls(node.value, embedded=False)
                except SyntaxError:
                    nested = []
                errors.extend((node.lineno, "embedded code: " + error) for _, error in nested)
            if re.search('\\b(?:app|probe|ui|window)\\.read\\(\\{\\{?["\\x27](?:full|menuPath)', node.value):
                errors.append((node.lineno, "embedded host code contains old read options"))
    return errors


class ConsumerMigrationTests(unittest.TestCase):
    def test_active_consumers_do_not_use_retired_public_forms(self):
        folders = (ROOT / "examples", ROOT / "experiments", ROOT / "tools/acceptance")
        failures = []
        for folder in folders:
            for file in folder.rglob("*.py"):
                source = file.read_text()
                for node in ast.walk(ast.parse(source)):
                    if (
                        isinstance(node, ast.ImportFrom)
                        and node.module
                        and (node.module == "computer_use" or node.module.startswith("computer_use."))
                    ):
                        module = ROOT / "computer_use_engine"
                        if node.module != "computer_use":
                            module = module / node.module.removeprefix("computer_use.").replace(".", "/")
                        if not module.is_dir() and not module.with_suffix(".py").is_file():
                            failures.append(f"{file.relative_to(ROOT)}:{node.lineno}: retired module {node.module}")
                for line, error in retired_calls(source):
                    failures.append(f"{file.relative_to(ROOT)}:{line}: {error}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_current_syntax_is_distinct_from_retired_syntax(self):
        current = (
            'field = await app.element(42)\nawait field.typeText("hello", '
            "replace=True)\nawait app.click(at=(12,24))\nawait "
            'app.read(view="both",full=True)'
        )
        self.assertEqual(retired_calls(current), [])
        for obsolete in (
            'await app.read({"full":True})',
            'await app.element("42").click()',
            'await app.find(["Save"]).click()',
            "await app.click(12,24)",
            'code = "await app.click(12,24)"',
            'await field.readText({"offset":0})',
        ):
            with self.subTest(source=obsolete):
                self.assertTrue(retired_calls(obsolete))
