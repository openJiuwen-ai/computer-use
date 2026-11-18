# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""使用本地规则和配置的工具检查 Python 文件。

检查函数的文档字符串对应 CI 规则名称，并说明检测边界。
完整规则清单、工具映射和自有检查的提示文案统一维护在 codecheck_rules.json 中。
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
    """读取带一元正负号的数值字面量，为 G.OPR.01 和 G.TYP.05 提供静态值。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, complex)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = literal_number(node.operand)
        if value is not None:
            return -value if isinstance(node.op, ast.USub) else value
    return None


def check_four_space_indentation(tokens):
    """G.FMT.01 程序块应该采用4个空格缩进风格编写。

    报告每层缩进未增加4个空格或使用制表符的代码块位置。
    仅通过 INDENT/DEDENT 识别代码块，不检查续行对齐、多行字符串、空行和纯注释。
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
    """G.FMT.10 代码行的行尾结束符LF和CRLF不应该混用。

    只报告同一文件中首次混用 LF 和 CRLF 的位置；统一使用任一风格均可通过。
    """
    first_ending = None
    for line, content in enumerate(data.splitlines(keepends=True), 1):
        ending = "CRLF" if content.endswith(b"\r\n") else "LF" if content.endswith(b"\n") else None
        if ending and first_ending and ending != first_ending:
            yield line, 1
            return
        first_ending = first_ending or ending


def check_assignment_expression(node):
    """G.OPR.04 不建议使用海象运算符。

    识别赋值表达式（:=），默认提示；是否拦截由调用方的规则配置和严格模式决定。
    """
    if isinstance(node, ast.NamedExpr):
        yield "G.OPR.04", node


def check_negative_slice_step(node):
    """G.TYP.05 对序列使用切片操作时，不建议使用负步进值进行切片。

    仅检查字面量负步长，不推断变量的运行时值；默认提示。
    """
    if isinstance(node, ast.Slice):
        step = literal_number(node.step)
        if isinstance(step, (int, float)) and step < 0:
            yield "G.TYP.05", node.step


def check_zero_divisor(node):
    """G.OPR.01 对除法运算和模运算中的除数为0的情况做相应保护。

    仅检查 /、//、% 的两个操作数均为数值字面量且除数为零的情形。
    不推断动态类型、增量赋值或跨函数保护，保留字符串格式化和重载运算符的合法用法。
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
        if literal_number(node.left) is not None and literal_number(node.right) == 0:
            yield "G.OPR.01", node.right


def local_body_nodes(function):
    """遍历当前函数体，跳过嵌套函数、lambda 和类的内部节点。"""
    pending = list(reversed(function.body))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            pending.extend(reversed(list(ast.iter_child_nodes(node))))


def literal_type(node):
    """根据语法识别字面量和容器表达式的类型，不执行源码。"""
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
    """收集未装饰、非生成器的同步魔法方法中的 return 语句。"""
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
    """筛出魔法方法中字面量返回类型不在允许集合中的位置。"""
    for statement in magic_method_returns(node, name):
        value_type = literal_type(statement.value)
        if value_type is not None and value_type not in allowed_types:
            yield statement.value if statement.value is not None else statement


def check_iter_return(node):
    """G.CLS.09 重写类的魔法函数时必须返回其原型指定的类型--iter。

    检查未装饰的普通同步 __iter__ 返回标量或容器字面量的情形。
    允许生成器方法和生成器表达式，不推断未知类型、装饰器或异步方法的结果。
    """
    for location in invalid_literal_returns(node, "__iter__", ()):
        yield "G.CLS.09", location


def check_bool_return(node):
    """G.CLS.09 重写类的魔法函数时必须返回其原型指定的类型--bool。

    检查字面量返回类型，仅允许 bool。
    仅检查未装饰、非生成器的同步 __bool__ 方法，不推断未知返回值。
    """
    for location in invalid_literal_returns(node, "__bool__", (bool,)):
        yield "G.CLS.09", location


def check_repr_return(node):
    """G.CLS.09 重写类的魔法函数时必须返回其原型指定的类型--repr。

    检查字面量返回类型，仅允许 str，包含 f-string。
    仅检查未装饰、非生成器的同步 __repr__ 方法，不推断未知返回值。
    """
    for location in invalid_literal_returns(node, "__repr__", (str,)):
        yield "G.CLS.09", location


def check_length_hint_return(node):
    """G.CLS.09 重写类的魔法函数时必须返回其原型指定的类型--length_hint。

    字面量结果须为非负整数，允许 bool 整数和 NotImplemented。
    仅检查未装饰、非生成器的同步方法，不推断未知返回值。
    """
    for statement in magic_method_returns(node, "__length_hint__"):
        value_type = literal_type(statement.value)
        number = literal_number(statement.value)
        if (value_type is not None and value_type not in (int, bool)) or (isinstance(number, int) and number < 0):
            yield "G.CLS.09", statement.value if statement.value is not None else statement


def check_format_return(node):
    """G.CLS.09 重写类的魔法函数时必须返回其原型指定的类型--format。

    检查字面量返回类型，仅允许 str。
    仅检查未装饰、非生成器的同步 __format__ 方法，不推断未知返回值。
    """
    for location in invalid_literal_returns(node, "__format__", (str,)):
        yield "G.CLS.09", location


def check_getnewargs_return(node):
    """G.CLS.09 重写类的魔法函数时必须返回其原型指定的类型--getnewargs。

    检查字面量返回类型，仅允许 tuple。
    仅检查未装饰、非生成器的同步 __getnewargs__ 方法，不推断未知返回值。
    """
    for location in invalid_literal_returns(node, "__getnewargs__", (tuple,)):
        yield "G.CLS.09", location


def check_getnewargs_ex_return(node):
    """G.CLS.09 重写类的魔法函数时必须返回其原型指定的类型--getnewargs_ex。

    字面量结果须为 (tuple, dict) 结构的二元组，含解包或未知成员类型时不推断。
    仅检查未装饰、非生成器的同步方法。
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
    """G.NAM.03 “self”应是实例方法的第一个参数，“cls”应是类方法的第一个参数--没有参数。

    检查类内未装饰方法缺少位置参数且不接受 *args 的情形。
    不推断装饰器和自定义描述符；接收参数的名称继续由 Ruff 检查。
    """
    if isinstance(node, ast.ClassDef):
        for method in node.body:
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and not method.decorator_list:
                if not (method.args.posonlyargs or method.args.args or method.args.vararg):
                    yield "G.NAM.03", method


def check_redundant_reraise(node):
    """G.ERR.13 捕获异常后避免直接重新抛出。

    仅检查最后一个普通 except 块中唯一语句为无参 raise 的情形。
    保留用于避开后续处理器的提前重抛，不检查带日志、清理或 except* 的处理器。
    """
    if isinstance(node, ast.Try) and node.handlers:
        handler = node.handlers[-1]
        if len(handler.body) == 1 and isinstance(handler.body[0], ast.Raise) and handler.body[0].exc is None:
            yield "G.ERR.13", handler.body[0]


def check_unreachable_statements(node):
    """G.CTL.02 所有的代码都必须是逻辑可达的。

    仅检查同一语句块中 return、raise、break 或 continue 后的语句。
    不推断条件恒值或跨函数控制流。
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
    """G.CMT.02 类的文档字符串写在类声明(class ClassName:)所在行的下一行，并向后缩进4个空格。

    仅检查已有类文档字符串与 class 同行或相对缩进非4空格的情形。
    不要求补写缺失文档，也不检查文档字符串前的空行。
    """
    if isinstance(node, ast.ClassDef) and ast.get_docstring(node) is not None:
        docstring = node.body[0]
        if docstring.lineno == node.lineno or docstring.col_offset != node.col_offset + 4:
            yield "G.CMT.02", docstring


def check_public_docstring_layout(node):
    """G.CMT.03 公共函数的文档字符串写在函数声明(def function_name(param):)所在行的下一行，并向后缩进4个空格。

    仅检查已有公共函数文档字符串与 def 同行或相对缩进非4空格的情形。
    允许多行函数声明；不检查下划线开头的函数、缺失文档及文档字符串前的空行。
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
        if ast.get_docstring(node) is not None:
            docstring = node.body[0]
            if docstring.lineno == node.lineno or docstring.col_offset != node.col_offset + 4:
                yield "G.CMT.03", docstring


def check_todo_fixme_comments(tokens):
    """G.CMT.05 正式交付给客户的代码不应包含TODO/FIXME注释。

    仅匹配注释中大小写不敏感的完整 TODO/FIXME 标记，忽略字符串。
    调用方 custom_findings 会为本规则排除项目的 tests 目录。
    """
    for token in tokens:
        if token.type == tokenize.COMMENT:
            match = re.search(r"\b(?:TODO|FIXME)\b", token.string, re.IGNORECASE)
            if match:
                yield token.start[0], token.start[1] + match.start() + 1


def custom_findings(data: bytes, filename: str):
    """汇总具名检查函数的发现，并按行和规则编号处理例外。

    只解析源码，不执行源码；语法错误由 Ruff 报告。
    提示或拦截由 check_files 根据配置决定。
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
    """根据 pyproject.toml 的规则及排除配置构造 Ruff 检查命令。"""
    return [sys.executable, "-I", "-m", "ruff", "check", "--config", str(CONFIG), "--force-exclude", *options]


def report_finding(filename, line, column, code, message, rules, engine):
    """通过规则清单映射工具诊断，输出对应的 CI 规则编号、位置和文案。"""
    codes = sorted({rule["code"] for rule in rules if code in rule["local_rules"]})
    label = ", ".join([*codes, f"{engine}/{code}"])
    sys.stdout.write(f"{filename}:{line}:{column}: {label} {message}\n")


def python_sources_only(directory, names):
    """排除 Pylint 临时包视图中的非源码文件。"""
    return [
        name
        for name in names
        if name in ("__pycache__", "target")
        or name.startswith(".")
        or (not Path(directory, name).is_dir() and Path(name).suffix not in (".py", ".pyi"))
    ]


def pylint_checks(files, rules, settings):
    """调用 Pylint 检查配置的行宽、导入顺序和类成员规则。

    规则映射：
        G.FMT.02 行宽不超过120个字符，对应 C0301。
        G.FMT.07 导入部分(imports)应该按照标准库、第三方库、应用程序自定义模块的顺序排列导入，对应 C0411。
        G.CLS.08 避免在__init__方法外定义类实例属性，对应 W0201。
        G.CLS.11 避免在类外或者子类中访问父类受保护的成员，对应 W0212。

    检测边界遵循 Pylint 的推断和例外，包括允许子类访问受保护成员，以及从基类推断属性。
    保留项目的 ignore-paths 配置，使用源码包别名辅助导入分类和继承推断，无需安装 SDK。
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
    """G.FMT.03 合理安排空行--嵌套定义空1行。

    调用 pycodestyle 的 E306，只检查嵌套定义前的空行。
    类间、类内方法间空行等同编号的其他子项不在此处覆盖。
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
    """汇总 Ruff、自有检查、Pylint 和 pycodestyle 的只读诊断。

    Ruff 规则选择位于 pyproject.toml，CI 映射、覆盖范围和自有提示文案位于 codecheck_rules.json。
    G.TYP.05 和 G.OPR.04 默认通过 advisory 配置输出提示，严格模式将其作为拦截项。
    Ruff 和自有检查支持 .py、.pyi，其他工具只接收 .py。
    返回 0 表示通过，1 表示发现拦截项，2 表示检查未完成。
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
    """检查指定文件，或列出全部 CI 规则及本地覆盖范围。"""
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
