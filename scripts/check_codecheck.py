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


def check_four_space_indentation(tokens):
    """Check G.FMT.01: each code block adds exactly four spaces of indentation.

    Yield line and column positions for non-four-space or tab-indented blocks.
    Only INDENT/DEDENT tokens define blocks; continuation alignment, multiline
    strings, blank lines and comment-only lines are outside this check's scope.
    """
    widths = [0]
    for token in tokens:
        if token.type == tokenize.INDENT:
            width = len(token.string)
            if token.string.strip(" ") or width - widths[-1] != 4:
                yield token.start[0], 1
            widths.append(width)
        elif token.type == tokenize.DEDENT:
            widths.pop()


def check_mixed_line_endings(data):
    """Check G.FMT.10: report the first mixed LF/CRLF ending within one file."""
    first_ending = None
    for line, content in enumerate(data.splitlines(keepends=True), 1):
        ending = "CRLF" if content.endswith(b"\r\n") else "LF" if content.endswith(b"\n") else None
        if ending and first_ending and ending != first_ending:
            yield line, 1
            return
        first_ending = first_ending or ending


def check_assignment_expression(node):
    """Check G.OPR.04: find assignment expressions (:=), advisory by default."""
    if isinstance(node, ast.NamedExpr):
        yield "G.OPR.04", node


def check_negative_slice_step(node):
    """Check G.TYP.05: find literal negative slice steps without inferring variables."""
    if isinstance(node, ast.Slice):
        step = literal_number(node.step)
        if isinstance(step, (int, float)) and step < 0:
            yield "G.TYP.05", node.step


def check_zero_divisor(node):
    """Check G.OPR.01: find /, // or % with numeric literals and a zero divisor.

    Dynamic operand types, augmented assignments and cross-function protection
    remain unchecked. String formatting and overloaded operators are preserved.
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
        if literal_number(node.left) is not None and literal_number(node.right) == 0:
            yield "G.OPR.01", node.right


def local_body_nodes(function):
    """Walk a function body without entering nested function, lambda or class bodies."""
    pending = list(reversed(function.body))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            pending.extend(reversed(list(ast.iter_child_nodes(node))))


def literal_type(node):
    """Identify literal and container-expression types without executing source code."""
    if node is None:
        return type(None)
    if isinstance(node, ast.Constant):
        return type(node.value)
    for kinds, value_type in (
        ((ast.List, ast.ListComp), list),
        ((ast.Tuple,), tuple),
        ((ast.Set, ast.SetComp), set),
        ((ast.Dict, ast.DictComp), dict),
        ((ast.JoinedStr,), str),
    ):
        if isinstance(node, kinds):
            return value_type
    number = literal_number(node)
    return type(number) if number is not None else None


def magic_method_returns(node, name):
    """Find returns in undecorated synchronous methods, excluding generator methods."""
    if not isinstance(node, ast.ClassDef):
        return
    for method in node.body:
        if not isinstance(method, ast.FunctionDef) or method.name != name or method.decorator_list:
            continue
        body = list(local_body_nodes(method))
        if any(isinstance(child, (ast.Yield, ast.YieldFrom)) for child in body):
            continue
        for child in body:
            if isinstance(child, ast.Return):
                yield child


def invalid_literal_returns(node, name, allowed_types):
    """Find magic-method returns with known literal types outside the allowed set."""
    for statement in magic_method_returns(node, name):
        value_type = literal_type(statement.value)
        if value_type is not None and value_type not in allowed_types:
            yield statement.value if statement.value is not None else statement


def check_iter_return(node):
    """Check G.CLS.09/iter: literal containers and scalars are not iterator objects.

    Generator methods, generator expressions and unknown return types are allowed.
    Decorated and asynchronous methods are outside this check's inference scope.
    """
    for location in invalid_literal_returns(node, "__iter__", ()):
        yield "G.CLS.09", location


def check_bool_return(node):
    """Check G.CLS.09/bool: ordinary __bool__ methods must return bool, not another literal type."""
    for location in invalid_literal_returns(node, "__bool__", (bool,)):
        yield "G.CLS.09", location


def check_repr_return(node):
    """Check G.CLS.09/repr: ordinary __repr__ methods must return str; unknown types are unchecked."""
    for location in invalid_literal_returns(node, "__repr__", (str,)):
        yield "G.CLS.09", location


def check_length_hint_return(node):
    """Check G.CLS.09/length_hint: literal results must be non-negative integers.

    Boolean integers and NotImplemented are allowed by the length-hint protocol.
    Unknown results, decorated methods and generator methods remain unchecked.
    """
    for statement in magic_method_returns(node, "__length_hint__"):
        value_type = literal_type(statement.value)
        number = literal_number(statement.value)
        if (value_type is not None and value_type not in (int, bool)) or (isinstance(number, int) and number < 0):
            yield "G.CLS.09", statement.value if statement.value is not None else statement


def check_format_return(node):
    """Check G.CLS.09/format: ordinary __format__ methods must return str; unknown types are unchecked."""
    for location in invalid_literal_returns(node, "__format__", (str,)):
        yield "G.CLS.09", location


def check_getnewargs_return(node):
    """Check G.CLS.09/getnewargs: ordinary __getnewargs__ methods must return a tuple."""
    for location in invalid_literal_returns(node, "__getnewargs__", (tuple,)):
        yield "G.CLS.09", location


def check_getnewargs_ex_return(node):
    """Check G.CLS.09/getnewargs_ex: literal results must have shape (tuple, dict).

    Tuple unpacking and unknown component types are not inferred. Decorated,
    asynchronous and generator methods remain outside this check's scope.
    """
    for statement in magic_method_returns(node, "__getnewargs_ex__"):
        value = statement.value
        value_type = literal_type(value)
        invalid = value_type is not None and value_type is not tuple
        if isinstance(value, ast.Tuple) and not any(isinstance(item, ast.Starred) for item in value.elts):
            invalid = len(value.elts) != 2 or any(
                literal_type(item) is not None and literal_type(item) is not expected
                for item, expected in zip(value.elts, (tuple, dict))
            )
        if invalid:
            yield "G.CLS.09", value if value is not None else statement


def check_missing_method_receiver(node):
    """Check G.NAM.03/no-arguments: ordinary methods need a positional receiver.

    Decorated methods and methods accepting *args are allowed; this check does
    not infer custom descriptors or enforce receiver names already checked by Ruff.
    """
    if isinstance(node, ast.ClassDef):
        for method in node.body:
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and not method.decorator_list:
                if not (method.args.posonlyargs or method.args.args or method.args.vararg):
                    yield "G.NAM.03", method


def check_redundant_reraise(node):
    """Check G.ERR.13: a final ordinary except block containing only bare raise.

    Earlier handlers may protect exceptions from later handlers and are allowed.
    Handlers with logging or cleanup, and except* handlers, remain unchecked.
    """
    if isinstance(node, ast.Try) and node.handlers:
        handler = node.handlers[-1]
        if len(handler.body) == 1 and isinstance(handler.body[0], ast.Raise) and handler.body[0].exc is None:
            yield "G.ERR.13", handler.body[0]


def check_unreachable_statements(node):
    """Check G.CTL.02: statements following an unconditional jump in the same block.

    Only return/raise/break/continue are terminators. Branch conditions and
    cross-function control flow are not inferred.
    """
    for _, statements in ast.iter_fields(node):
        if not isinstance(statements, list) or not all(isinstance(item, ast.stmt) for item in statements):
            continue
        terminated = False
        for statement in statements:
            if terminated:
                yield "G.CTL.02", statement
            if isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                terminated = True


def check_class_docstring_layout(node):
    """Check G.CMT.02: existing class docstrings belong on an indented body line.

    Check same-line placement and indentation relative to the class. Missing
    docstrings and blank lines before an existing docstring are not checked.
    """
    if isinstance(node, ast.ClassDef) and ast.get_docstring(node) is not None:
        docstring = node.body[0]
        if docstring.lineno == node.lineno or docstring.col_offset != node.col_offset + 4:
            yield "G.CMT.02", docstring


def check_public_docstring_layout(node):
    """Check G.CMT.03: existing public-function docstrings belong on an indented body line.

    Names starting with underscore, missing docstrings and intervening blank
    lines are outside this check's scope; multiline function headers are allowed.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
        if ast.get_docstring(node) is not None:
            docstring = node.body[0]
            if docstring.lineno == node.lineno or docstring.col_offset != node.col_offset + 4:
                yield "G.CMT.03", docstring


def check_todo_fixme_comments(tokens):
    """Check G.CMT.05: find TODO/FIXME words in comments, ignoring strings.

    Markers are case-insensitive whole words. custom_findings excludes this
    project's tests directory from this production-code check.
    """
    for token in tokens:
        if token.type == tokenize.COMMENT:
            match = re.search(r"\b(?:TODO|FIXME)\b", token.string, re.IGNORECASE)
            if match:
                yield token.start[0], token.start[1] + match.start() + 1


def custom_findings(data: bytes, filename: str):
    """Combine named local checks with same-line, per-rule suppression.

    Source code is parsed without evaluation. Ruff reports parse errors;
    check_files applies advisory versus blocking behavior to the findings.
    """
    ignored = {}
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        source = data.decode(encoding)
        tree = ast.parse(source, filename=filename)
        tokens = list(tokenize.tokenize(io.BytesIO(data).readline))
        for token in tokens:
            if token.type == tokenize.COMMENT:
                match = IGNORE.match(token.string)
                if match:
                    ignored.setdefault(token.start[0], set()).update(item.strip() for item in match[1].split(","))
    except (SyntaxError, UnicodeError, tokenize.TokenError):
        # Ruff reports parse errors with its normal file and location diagnostics.
        return []

    findings = []

    def emit(code, line, column, check):
        if code not in ignored.get(line, ()):
            findings.append(
                {"filename": filename, "line": line, "column": column, "code": code, "check": check.__name__}
            )

    for line, column in check_four_space_indentation(tokens):
        emit("G.FMT.01", line, column, check_four_space_indentation)
    for line, column in check_mixed_line_endings(data):
        emit("G.FMT.10", line, column, check_mixed_line_endings)
    if not Path(filename).resolve().is_relative_to(ROOT / "tests"):
        for line, column in check_todo_fixme_comments(tokens):
            emit("G.CMT.05", line, column, check_todo_fixme_comments)

    lines = source.splitlines()
    for node in ast.walk(tree):
        for check in (
            check_assignment_expression,
            check_negative_slice_step,
            check_zero_divisor,
            check_iter_return,
            check_bool_return,
            check_repr_return,
            check_length_hint_return,
            check_format_return,
            check_getnewargs_return,
            check_getnewargs_ex_return,
            check_missing_method_receiver,
            check_redundant_reraise,
            check_unreachable_statements,
            check_class_docstring_layout,
            check_public_docstring_layout,
        ):
            for code, location in check(node):
                # AST columns are UTF-8 byte offsets; diagnostics use character columns.
                prefix = lines[location.lineno - 1].encode("utf-8")[: location.col_offset].decode("utf-8")
                emit(code, location.lineno, len(prefix) + 1, check)
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
    messages = {name: rule["message"] for rule in rules if "message" in rule for name in rule["local_rules"]}
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
            message = messages[item["check"]]
            sys.stdout.write(f"{filename}:{item['line']}:{item['column']}: {code} [{level}] {message}\n")
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
