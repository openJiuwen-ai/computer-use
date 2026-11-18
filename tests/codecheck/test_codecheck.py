# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run with the hook's Python, without importing or building the SDK."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts/check_codecheck.py"


class CodeCheckTests(unittest.TestCase):
    def check_source(self, source, *options, name="sample.py", directory=None):
        temporary = tempfile.TemporaryDirectory(prefix="codecheck-", dir=directory)
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = source.encode("utf-8") if isinstance(source, str) else source
        path.write_bytes(data)
        result = subprocess.run(
            [sys.executable, "-I", str(CHECK), *options, str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(path.read_bytes(), data, "Checking must preserve the source bytes.")
        return result

    def test_constant_zero_divisor_is_blocked(self):
        for operator in ("/", "//", "%"):
            for value in ("0", "-0.0", "False", "0j"):
                with self.subTest(operator=operator, value=value):
                    result = self.check_source(f"result = 10 {operator} {value}\n")
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("G.OPR.01", result.stdout)

    def test_runtime_divisors_and_zero_in_strings_remain_valid(self):
        result = self.check_source('divisor = 2\nresult = 10 / divisor\ntext = "result = 10 / 0"\n')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_string_modulo_and_overloaded_operators_remain_valid(self):
        source = """class Example:
    def __truediv__(self, _divisor):
        return self


item = Example()
text = "%d" % 0
template = "%d"
other = template % 0
template %= 0
value = item / 0
"""
        result = self.check_source(source, "--strict")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_advisory_rules_are_visible_and_strict_can_block_them(self):
        cases = {
            "G.TYP.05": "result = [1, 2, 3][::-1]\n",
            "G.OPR.04": "if (value := 1):\n    result = value\n",
        }
        for rule, source in cases.items():
            with self.subTest(rule=rule):
                advisory = self.check_source(source)
                strict = self.check_source(source, "--strict")
                self.assertEqual(advisory.returncode, 0, advisory.stdout + advisory.stderr)
                self.assertIn(rule, advisory.stdout)
                self.assertIn("提示", advisory.stdout)
                self.assertEqual(strict.returncode, 1, strict.stdout + strict.stderr)
                self.assertIn(rule, strict.stdout)

    def test_positive_and_unknown_slice_steps_remain_valid(self):
        result = self.check_source("step = 1\nresult = [1, 2, 3][::step]\nother = [1, 2, 3][::2]\n", "--strict")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mixed_newlines_are_blocked_but_uniform_newlines_pass(self):
        mixed = self.check_source(b"first = 1\r\nsecond = 2\n")
        self.assertEqual(mixed.returncode, 1, mixed.stdout + mixed.stderr)
        self.assertIn("G.FMT.10", mixed.stdout)
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                clean = self.check_source(newline.join((b"first = 1", b"second = 2", b"")))
                self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)

    def test_custom_suppression_is_rule_and_line_specific(self):
        ignored = self.check_source("result = [1, 2][::-1]  # codecheck: ignore[G.TYP.05] -- reverse the sequence\n")
        self.assertEqual(ignored.returncode, 0, ignored.stdout + ignored.stderr)
        self.assertNotIn("G.TYP.05", ignored.stdout)
        unrelated = self.check_source("result = 1 / 0  # codecheck: ignore[G.TYP.05]\n")
        self.assertEqual(unrelated.returncode, 1, unrelated.stdout + unrelated.stderr)
        string = self.check_source('text = "# codecheck: ignore[G.OPR.01]"; result = 1 / 0\n')
        self.assertEqual(string.returncode, 1, string.stdout + string.stderr)
        self.assertIn("G.OPR.01", string.stdout)

    def test_ruff_rules_are_reported_with_the_ci_rule(self):
        cases = [
            ("G.FMT.06", "import os, sys\n"),
            ("G.FNM.01", "def collect(items=[]):\n    return items\n"),
            ("G.FNM.02", "callbacks = [lambda: index for index in range(3)]\n"),
            ("G.LOG.01", 'import logging\nlogging.debug(f"value {1}")\n'),
            ("G.TES.01", "assert True\n"),
            ("G.FIO.03", "import tempfile\nname = tempfile.mktemp()\n"),
            ("G.PRJ.03", "breakpoint()\n"),
            ("G.SER.03", 'import yaml\nvalue = yaml.load("a: 1")\n'),
            ("G.SER.04", 'import jsonpickle as jp\nvalue = jp.decode("{}")\n'),
            ("G.SER.04", "from jsonpickle import encode as encode_data\nvalue = encode_data({})\n"),
            ("G.EDV.04", 'import subprocess\nsubprocess.run("echo ok", shell=True, check=True)\n'),
            ("G.OPR.02", "value = None\nresult = value == None\n"),
            ("G.NAM.03", "class Example:\n    def method(wrong):\n        return wrong\n"),
            ("G.CLS.09", 'class Example:\n    def __len__(self):\n        return "bad"\n'),
            ("G.CLS.09", "class Example:\n    def __index__(self):\n        return 1.5\n"),
            ("G.CLS.09", "class Example:\n    def __str__(self):\n        return 42\n"),
            ("G.CLS.09", "class Example:\n    def __bytes__(self):\n        return 42\n"),
            ("G.CLS.09", 'class Example:\n    def __hash__(self):\n        return "bad"\n'),
            ("G.TYP.01", "from decimal import Decimal\nvalue = Decimal(1.2)\n"),
            ("G.TYP.06", 'value = {"same": 1, "same": 2}\n'),
            ("G.EXP.03", "identity = lambda value: value\n"),
            ("G.ERR.04", "try:\n    int('bad')\nexcept ValueError:\n    raise RuntimeError('bad')\n"),
            ("G.ERR.09", "try:\n    int('bad')\nexcept (ValueError, ValueError):\n    pass\n"),
            ("G.ERR.10", "try:\n    int('bad')\nexcept ValueError:\n    pass\nexcept ValueError:\n    pass\n"),
            ("G.ERR.14", "def read():\n    try:\n        return 1\n    finally:\n        return 2\n"),
        ]
        for rule, source in cases:
            with self.subTest(rule=rule, source=source):
                result = self.check_source(source)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(rule, result.stdout)

    def test_legitimate_counterparts_pass(self):
        source = """import json
import logging
import subprocess
import tempfile
from decimal import Decimal

logging.debug("value %s", 1)
subprocess.run(["echo", "ok"], check=True)
amount = Decimal("1.2")
value = json.loads("{}")
with tempfile.TemporaryFile() as stream:
    stream.write(b"test")


def collect(items=None):
    return [] if items is None else items


class Example:
    def __len__(self):
        return 2
"""
        result = self.check_source(source, "--strict", name="有空格的 source.pyi")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_assertions_in_test_files_are_allowed(self):
        result = self.check_source("assert True\n", directory=ROOT / "tests")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ruff_exclusions_apply_to_custom_checks(self):
        result = self.check_source("result = 1 / 0\n", name=".venv/sample.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("G.OPR.01", result.stdout)

    def test_unparseable_input_reports_a_check_failure(self):
        result = self.check_source("def broken(\n")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("invalid-syntax", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_external_protected_access_including_module_wrapper(self):
        for source in (
            "class Service:\n    _field = 1\n\n\nvalue = Service()._field\n",
            "def wrap(method):\n    async def run(self):\n        self._operations.add(method)\n    return run\n",
        ):
            with self.subTest(source=source):
                result = self.check_source(source)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("G.CLS.11", result.stdout)

    def test_protected_members_in_class_and_dunder_access_are_allowed(self):
        result = self.check_source("""class Service:
    def __init__(self):
        self._field = 1

    def read(self):
        return self._field


class Derived(Service):
    def read(self):
        return self._field


value = Service().__class__
""")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_protected_accesses_on_the_same_line_keep_their_columns(self):
        result = self.check_source(
            "class Example:\n    _field = 1\n\n\nitem = Example()\nvalues = (item._field, item._field)\n"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        diagnostics = [line for line in result.stdout.splitlines() if "G.CLS.11" in line]
        self.assertEqual(len(diagnostics), 2, result.stdout)
        self.assertNotEqual(diagnostics[0], diagnostics[1])

    def test_dataclass_post_init_is_an_initialization_method(self):
        result = self.check_source("""from dataclasses import dataclass, field


@dataclass
class Example:
    seed: int = 0
    value: int = field(init=False)

    def __post_init__(self):
        self.value = self.seed

    def reset(self):
        self.value = 0
""")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_attribute_definition_and_reset_are_distinguished(self):
        bad = self.check_source("class Builder:\n    def build(self):\n        self._calls = 0\n")
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.assertIn("G.CLS.08", bad.stdout)
        good = self.check_source("""from dataclasses import dataclass


@dataclass
class Builder:
    calls: int = 0

    def build(self):
        self.calls = 0


class Service:
    def __init__(self):
        self._calls = 0

    def build(self):
        self._calls = 0
""")
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)

    def test_line_width_boundary_uses_120_characters(self):
        for width in (120, 121):
            for character in ("x", "中"):
                with self.subTest(width=width, character=character):
                    source = "value = '" + character * (width - 10) + "'\n"
                    result = self.check_source(source)
                    self.assertEqual(result.returncode, int(width > 120), result.stdout + result.stderr)
                    self.assertEqual("G.FMT.02" in result.stdout, width > 120)

    def test_import_order_knows_project_packages(self):
        bad = self.check_source("import computer_use\nimport os\n\n__all__ = ['computer_use', 'os']\n")
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.assertIn("G.FMT.07", bad.stdout)
        good = self.check_source("import os\n\nimport computer_use\n\n__all__ = ['computer_use', 'os']\n")
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
        bad_project_order = self.check_source(
            "import computer_use\nimport astroid\n\n__all__ = ['computer_use', 'astroid']\n"
        )
        self.assertEqual(bad_project_order.returncode, 1, bad_project_order.stdout + bad_project_order.stderr)
        self.assertIn("G.FMT.07", bad_project_order.stdout)

    def test_inherited_sdk_fields_are_resolved_from_source(self):
        result = self.check_source("""from computer_use.native.query.graph import NativeGraph


class Derived(NativeGraph):
    def reset(self):
        self.dependencies = {}
""")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_tool_failures_return_incomplete_status(self):
        checker = runpy.run_path(str(CHECK))
        failures = [FileNotFoundError("missing checker"), subprocess.TimeoutExpired("ruff", 120)]
        for failure in failures:
            with self.subTest(failure=failure):
                error = io.StringIO()
                with patch.object(subprocess, "run", side_effect=failure), redirect_stderr(error):
                    result = checker["main"]([str(CHECK)])
                self.assertEqual(result, 2)
                self.assertIn("检查未完成", error.getvalue())

    def test_malformed_pylint_output_preserves_existing_findings(self):
        checker = runpy.run_path(str(CHECK))
        output, error = io.StringIO(), io.StringIO()
        responses = [
            subprocess.CompletedProcess(
                [],
                1,
                json.dumps(
                    [
                        {
                            "filename": str(CHECK),
                            "location": {"row": 1, "column": 1},
                            "code": "E401",
                            "message": "Multiple imports on one line",
                        }
                    ]
                ),
                "",
            ),
            subprocess.CompletedProcess([], 0, str(CHECK) + "\n", ""),
            subprocess.CompletedProcess([], 1, "broken JSON", "tool failed"),
        ]
        with patch.object(subprocess, "run", side_effect=responses), redirect_stdout(output), redirect_stderr(error):
            result = checker["main"]([str(CHECK)])
        self.assertEqual(result, 2)
        self.assertIn("G.FMT.06", output.getvalue())
        self.assertIn("tool failed", error.getvalue())

    def test_nested_definition_requires_a_blank_line(self):
        for keyword in ("def", "async def"):
            source = f"def outer():\n    value = 1\n    {keyword} inner():\n        return value\n    return inner\n"
            with self.subTest(keyword=keyword):
                bad = self.check_source(source)
                self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
                self.assertIn("G.FMT.03", bad.stdout)
                good = self.check_source(source.replace(f"    {keyword} inner", f"\n    {keyword} inner"))
                self.assertEqual(good.returncode, 0, good.stdout + good.stderr)

    def test_rule_inventory_preserves_all_ci_rules(self):
        catalog = json.loads((ROOT / "scripts/codecheck_rules.json").read_text(encoding="utf-8"))
        rules = catalog["rules"]
        self.assertEqual(len(rules), 132)
        self.assertEqual(len({rule["id"] for rule in rules}), 132)
        self.assertTrue(all(rule["name"] and rule["coverage"] for rule in rules))
        result = subprocess.run(
            [sys.executable, "-I", str(CHECK), "--list-rules"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("132", result.stdout)
        self.assertIn("待补充", result.stdout)
        self.assertIn("G.FNM.01", result.stdout)


if __name__ == "__main__":
    unittest.main()
