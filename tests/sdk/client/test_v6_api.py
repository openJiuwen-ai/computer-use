"""The approved public stub is executable contract, including keyword-only args."""

import ast
import inspect
import unittest
from pathlib import Path

import computer_use


class V6SurfaceTests(unittest.TestCase):
    def test_runtime_methods_match_documented_argument_kinds_and_defaults(self):
        stub = ast.parse(Path(computer_use.__file__).with_name("__init__.pyi").read_text())
        for cls in stub.body:
            if not isinstance(cls, ast.ClassDef) or cls.name not in ("Computer", "UI", "App", "Window", "Element"):
                continue
            runtime = getattr(computer_use, cls.name)
            for method in cls.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) or method.name.startswith("_"):
                    continue
                with self.subTest(cls=cls.name, method=method.name):
                    actual = getattr(runtime, method.name)
                    self.assertEqual(inspect.iscoroutinefunction(actual), isinstance(method, ast.AsyncFunctionDef))
                    params = list(inspect.signature(actual).parameters.values())
                    expected = [a.arg for a in method.args.args] + [a.arg for a in method.args.kwonlyargs]
                    self.assertEqual([p.name for p in params], expected)
                    kwonly = {a.arg for a in method.args.kwonlyargs}
                    self.assertEqual({p.name for p in params if p.kind == inspect.Parameter.KEYWORD_ONLY}, kwonly)
                    defaults = (
                        {
                            a.arg: ast.literal_eval(v)
                            for a, v in zip(method.args.args[-len(method.args.defaults) :], method.args.defaults)
                        }
                        if method.args.defaults
                        else {}
                    )
                    defaults.update(
                        {
                            a.arg: ast.literal_eval(v)
                            for a, v in zip(method.args.kwonlyargs, method.args.kw_defaults)
                            if v is not None
                        }
                    )
                    self.assertEqual(
                        {p.name: p.default for p in params if p.default is not inspect.Parameter.empty}, defaults
                    )

    def test_no_removed_locator_or_element_coordinate_api(self):
        self.assertFalse(hasattr(computer_use.App, "find"))
        self.assertFalse(hasattr(computer_use.Window, "window"))
        self.assertFalse(hasattr(computer_use.Element, "drag"))
        self.assertNotIn("at", inspect.signature(computer_use.Element.click).parameters)
