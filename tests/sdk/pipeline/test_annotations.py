"""Guard type information used by editors without importing native drivers."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class AnnotationContractsTests(unittest.TestCase):
    def test_execution_functions_have_parameter_and_return_types(self):
        """Closures and dependency factories must preserve the typed boundary."""
        paths = [ROOT / "computer_use_engine/pipeline/core.py"]
        paths.extend((ROOT / "computer_use_engine/interaction").rglob("*.py"))
        missing = []
        for path in paths:
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                arguments = [
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                    *([node.args.vararg] if node.args.vararg else []),
                    *([node.args.kwarg] if node.args.kwarg else []),
                ]
                absent = [arg.arg for arg in arguments if arg.arg not in ("self", "cls") and arg.annotation is None]
                if absent or node.returns is None:
                    missing.append((str(path.relative_to(ROOT)), node.lineno, absent))
        self.assertEqual(missing, [])

    def test_ir_fields_do_not_use_bare_collections(self):
        """A field's container must reveal its key/item type in IntelliSense."""
        missing = []
        for path in (ROOT / "computer_use_engine/interaction").rglob("*.py"):
            if path.name not in ("model.py", "ir.py", "requests.py", "snapshot.py"):
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.AnnAssign):
                    continue
                if isinstance(node.annotation, ast.Name) and node.annotation.id in (
                    "dict",
                    "list",
                    "tuple",
                    "set",
                    "frozenset",
                    "Mapping",
                    "Callable",
                ):
                    missing.append((str(path.relative_to(ROOT)), node.lineno))
        self.assertEqual(missing, [])

    def test_data_contract_docstrings_describe_fields(self):
        """Google Attributes sections document ownership, scope, and units."""
        missing = []
        for path in (ROOT / "computer_use_engine/interaction").rglob("*.py"):
            if path.name not in ("model.py", "ir.py", "requests.py", "snapshot.py"):
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.ClassDef):
                    continue
                fields = [
                    item.target.id
                    for item in node.body
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
                ]
                if fields and "Attributes:" not in (ast.get_docstring(node) or ""):
                    missing.append((str(path.relative_to(ROOT)), node.name))
        self.assertEqual(missing, [])
