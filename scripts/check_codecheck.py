# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Check Python files against locally implemented MR rules and configured tools.

Checker docstrings identify CI rules and implementation boundaries. The full
rule inventory, tool mappings and custom diagnostic messages are maintained in
codecheck_rules.json.
"""

import argparse
import ast
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
import tomllib


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "pyproject.toml"
CATALOG = Path(__file__).with_name("codecheck_rules.json")
IGNORE = re.compile(r"^#\s*codecheck:\s*ignore\[([^\]]+)\]")


def literal_number(node):
    """Read a numeric literal, including unary signs, for G.OPR.01 and G.TYP.05."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, complex)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = literal_number(node.operand)
        if value is not None:
            return -value if isinstance(node.op, ast.USub) else value
    return None


def custom_findings(data: bytes, filename: str):
    """Find four CI rule patterns through AST and token inspection.

    CI rules:
        G.OPR.01: /, // or % with two numeric literals and a zero divisor.
        G.TYP.05: a literal negative slice step.
        G.OPR.04: an assignment expression (:=).
        G.FMT.10: mixed LF and CRLF endings within one file.

    Source code is never evaluated; dynamic operand types remain unchecked.
    Same-line ``# codecheck: ignore[<rule>]`` comments suppress only that rule.
    Advisory versus blocking behavior is applied by check_files.
    """
    ignored = {}
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        source = data.decode(encoding)
        tree = ast.parse(source, filename=filename)
        for token in tokenize.tokenize(io.BytesIO(data).readline):
            if token.type == tokenize.COMMENT:
                match = IGNORE.match(token.string)
                if match:
                    ignored.setdefault(token.start[0], set()).update(item.strip() for item in match[1].split(","))
    except (SyntaxError, UnicodeError, tokenize.TokenError):
        # Ruff reports parse errors with its normal file and location diagnostics.
        return []

    findings = []

    def emit(code, line, column):
        if code not in ignored.get(line, ()):
            findings.append({"filename": filename, "line": line, "column": column, "code": code})

    first_ending = None
    for line, content in enumerate(data.splitlines(keepends=True), 1):
        ending = "CRLF" if content.endswith(b"\r\n") else "LF" if content.endswith(b"\n") else None
        if ending and first_ending and ending != first_ending:
            emit("G.FMT.10", line, 1)
            break
        first_ending = first_ending or ending

    lines = source.splitlines()
    for node in ast.walk(tree):
        code = None
        location = node
        if isinstance(node, ast.NamedExpr):
            code = "G.OPR.04"
        elif isinstance(node, ast.Slice):
            step = literal_number(node.step)
            if isinstance(step, (int, float)) and step < 0:
                code, location = "G.TYP.05", node.step
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            # The % operator also formats strings; unknown operand types remain unchecked.
            if literal_number(node.left) is not None and literal_number(node.right) == 0:
                code, location = "G.OPR.01", node.right
        if code:
            # AST columns are UTF-8 byte offsets; diagnostics use character columns.
            prefix = lines[location.lineno - 1].encode("utf-8")[: location.col_offset].decode("utf-8")
            emit(code, location.lineno, len(prefix) + 1)
    return findings


def ruff_command(*options):
    """Build the Ruff command using project rules and exclusions from pyproject.toml."""
    return [sys.executable, "-I", "-m", "ruff", "check", "--config", str(CONFIG), "--force-exclude", *options]


def report_finding(filename, line, column, code, message, rules, engine):
    """Label a tool diagnostic with all matching CI rule codes from the catalog."""
    codes = sorted({rule["code"] for rule in rules if code in rule["local_rules"]})
    label = ", ".join([*codes, f"{engine}/{code}"])
    sys.stdout.write(f"{filename}:{line}:{column}: {label} {message}\n")


def python_sources_only(directory, names):
    """Exclude non-source files from Pylint's temporary package view."""
    return [
        name
        for name in names
        if name in ("__pycache__", "target")
        or name.startswith(".")
        or (not Path(directory, name).is_dir() and Path(name).suffix not in (".py", ".pyi"))
    ]


def pylint_checks(files, rules, settings):
    """Run the configured Pylint checks for four CI rules.

    CI rule mappings:
        G.FMT.02 -> C0301: configured line-length limit (120 characters).
        G.FMT.07 -> C0411: standard-library, third-party and project import order.
        G.CLS.08 -> W0201: instance attributes defined outside initializer methods.
        G.CLS.11 -> W0212: access to protected members outside allowed class contexts.

    Coverage follows Pylint's inference and exemptions, including protected
    member access from subclasses and attributes inferred from base classes.
    Project ignore-paths applies. Source-only package aliases provide local
    import classification and inheritance information without installing the SDK.
    """
    patterns = settings["pylint-ignore-paths"]
    files = [
        filename
        for filename in files
        if not Path(filename).is_relative_to(ROOT)
        or not any(re.match(pattern, Path(filename).relative_to(ROOT).as_posix()) for pattern in patterns)
    ]
    if not files:
        return 0
    # The SDK uses setuptools package aliases. Expose source-only packages for
    # static inference and isort classification, without installing the SDK.
    with tempfile.TemporaryDirectory(prefix="codecheck-imports-") as temporary:
        for package, directory in settings["source-packages"].items():
            shutil.copytree(ROOT / directory, Path(temporary) / package, ignore=python_sources_only)
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "pylint",
                "--rcfile",
                str(CONFIG),
                "--init-hook",
                f"import sys; sys.path.insert(0, {temporary!r})",
                "--disable=all",
                "--enable=" + ",".join(["F", "E0001", *settings["pylint-select"]]),
                "--reports=n",
                "--score=n",
                "--persistent=n",
                "--output-format=json",
                *files,
            ],
            cwd=temporary,
            text=True,
            capture_output=True,
            timeout=settings["timeout"],
            check=False,
        )
    if result.stderr:
        sys.stderr.write(result.stderr)
    for item in json.loads(result.stdout):
        report_finding(
            item["path"],
            item["line"],
            item["column"] + 1,
            item["message-id"],
            item["message"],
            rules,
            "pylint",
        )
    if result.returncode < 0 or result.returncode & (1 | 2 | 32):
        sys.stderr.write(f"Pylint 检查未完成，退出码 {result.returncode}。\n")
        return 2
    return int(bool(result.returncode))


def pycodestyle_checks(files, rules):
    """Check G.FMT.03's nested-definition blank line requirement via E306.

    Other G.FMT.03 variants, such as spacing between top-level classes or class
    methods, are outside this check's coverage.
    """
    if not files:
        return 0
    import pycodestyle

    class Report(pycodestyle.BaseReport):
        def error(self, line_number, offset, text, check):
            code = super().error(line_number, offset, text, check)
            if code:
                report_finding(self.filename, line_number, offset + 1, code, text[5:], rules, "pycodestyle")
            return code

    style = pycodestyle.StyleGuide(select=["E306"], reporter=Report, quiet=True)
    return int(bool(style.check_files(files).total_errors))


def check_files(files, rules, settings, strict):
    """Combine read-only Ruff, custom, Pylint and pycodestyle diagnostics.

    Ruff rule selection lives in pyproject.toml; its CI mappings and partial
    coverage and custom diagnostic messages are recorded in codecheck_rules.json.
    Custom G.TYP.05 and G.OPR.04 are advisory by default through
    settings['advisory']; strict makes them block.
    Ruff and custom checks accept .py and .pyi; the other tools receive .py only.
    Return 0 for success, 1 for blocking findings, or 2 for an incomplete check.
    """
    messages = {rule["code"]: rule["message"] for rule in rules if "message" in rule}
    paths = [str(Path(name).resolve()) for name in files]
    result = subprocess.run(
        [*ruff_command("--no-fix", "--output-format=json"), "--", *paths],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=settings["timeout"],
        check=False,
    )
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode not in (0, 1):
        sys.stderr.write(result.stdout)
        return 2
    items = json.loads(result.stdout)
    for item in items:
        location = item["location"]
        report_finding(
            item["filename"],
            location["row"],
            location["column"],
            item["code"],
            item["message"],
            rules,
            "ruff",
        )
    if any(item["code"] in ("invalid-syntax", "E902") for item in items):
        return 2

    # Reuse Ruff's file selection so native checks honor its excludes and gitignore.
    selected = subprocess.run(
        [*ruff_command("--show-files"), "--", *paths],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=settings["timeout"],
        check=False,
    )
    if selected.returncode:
        sys.stderr.write(selected.stderr)
        return 2
    status = result.returncode
    sources = []
    for filename in selected.stdout.splitlines():
        path = Path(filename)
        if path.suffix not in (".py", ".pyi"):
            continue
        if path.suffix == ".py":
            sources.append(filename)
        for item in sorted(
            custom_findings(path.read_bytes(), filename), key=lambda value: (value["line"], value["column"])
        ):
            code = item["code"]
            warning = code in settings["advisory"] and not strict
            level = "提示" if warning else "错误"
            sys.stdout.write(f"{filename}:{item['line']}:{item['column']}: {code} [{level}] {messages[code]}\n")
            if not warning:
                status = 1
    status = max(status, pylint_checks(sources, rules, settings))
    return max(status, pycodestyle_checks(sources, rules))


def main(argv: list[str]) -> int:
    """Run selected files or list the CI rule inventory and local coverage."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-rules", action="store_true", help="列出全部 CI 规则及本地覆盖范围")
    parser.add_argument("--strict", action="store_true", help="将建议类的本地提示也作为检查失败")
    parser.add_argument("files", nargs="*", help="检查的文件，提交时由 pre-commit 提供")
    args = parser.parse_args(argv)
    rules = json.loads(CATALOG.read_text(encoding="utf-8"))["rules"]
    if args.list_rules:
        covered = sum(bool(rule["local_rules"]) for rule in rules)
        sys.stdout.write(
            f"共 {len(rules)} 条 CI 规则，{covered} 条有本地检查，其余 {len(rules) - covered} 条待补充。\n"
        )
        for rule in rules:
            local = ", ".join(rule["local_rules"]) or "待补充"
            sys.stdout.write(f"{rule['name']} [{local}] {rule['coverage']}\n")
        return 0
    if not args.files:
        return 0
    try:
        with CONFIG.open("rb") as stream:
            configuration = tomllib.load(stream)["tool"]
        settings = configuration["codecheck"]
        settings["pylint-ignore-paths"] = configuration["pylint"]["MAIN"]["ignore-paths"]
        return check_files(args.files, rules, settings, args.strict)
    except (OSError, ValueError, ImportError, subprocess.TimeoutExpired) as error:
        sys.stderr.write(f"CodeCheck 检查未完成：{error}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
