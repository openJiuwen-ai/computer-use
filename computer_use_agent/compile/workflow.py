# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Named desktop functions and their independently observed outcomes."""

import ast
import asyncio
import builtins
import hashlib
import json
import keyword
import re
import symtable
from dataclasses import asdict
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from computer_use.client.execution import session_executor
from computer_use.errors import ComputerUseError
from computer_use.protocol.element_path import element_path

from .artifact import Artifact, canonical, fingerprint
from .model import Check, Selection, conclude
from .pipeline import ProgramContext, execute

ACCEPTANCE = "desktop-function-result"
SCOPE = "_workflow"
_READS = {"read", "read_text", "read_file", "list_apps", "list_windows"}
_HANDLES = {"app", "element"}
_WRITES = {
    "click",
    "scroll",
    "drag",
    "type_text",
    "paste",
    "press_key",
    "focus",
    "open_menu",
    "perform_action",
    "close_window",
}
_IMPORTS = {"datetime", "json", "math", "re", "time"}
_BLOCKED = {"__import__", "__builtins__", "eval", "exec", "compile", "open", "globals", "locals", "vars"}
_METADATA = "\n\n# Compile metadata and execution entry point.\nCOMPILED_FUNCTION = "


def revision():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _text(value, field, limit=2048):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Expected text for {field}")
    if len(value) > limit:
        raise ValueError(f"{field} has {len(value)} characters, maximum {limit}")


def _validate_code(code, method, parameters):
    """Adapt the original Compile's static initialization and name checks to synchronous SDK code."""
    _text(code, "Python function module", 32000)
    tree = ast.parse(code, filename="program.py")
    declared = set()
    entry = None
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [item.asname or item.name for item in node.names]
            if isinstance(node, ast.Import):
                allowed = all(item.name in _IMPORTS for item in node.names)
            else:
                allowed = not node.level and (
                    node.module in _IMPORTS or (node.module == "__future__" and names == ["annotations"])
                )
            if not allowed or "*" in names:
                raise ValueError("Use only datetime, json, math, re, time or postponed annotations imports")
        elif isinstance(node, ast.FunctionDef):
            if node.decorator_list or getattr(node, "type_params", ()):
                raise ValueError("Functions must not run decorators or type initialization during import")
            for default in [*node.args.defaults, *filter(None, node.args.kw_defaults)]:
                _literal(default, "Function defaults")
            names = [node.name]
            if node.name == method:
                entry = node
        elif isinstance(node, ast.Assign) and all(isinstance(target, ast.Name) for target in node.targets):
            _literal(node.value, "Module constants")
            names = [target.id for target in node.targets]
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        else:
            raise ValueError("Use only functions, literal constants, docstrings and approved imports at module scope")
        for name in names:
            if name in declared or name in {"run", "COMPILED_FUNCTION"}:
                raise ValueError(f"Duplicate or reserved module name {name}")
            declared.add(name)
    if entry is None:
        raise ValueError(f"The module must define {method}(computer, *, ...)")
    args = entry.args
    positional = [arg.arg for arg in (*args.posonlyargs, *args.args)]
    keyword_only = {arg.arg for arg in args.kwonlyargs}
    has_defaults = bool(args.defaults) or any(default is not None for default in args.kw_defaults)
    has_variadic = args.vararg is not None or args.kwarg is not None
    if (positional, keyword_only) != (["computer"], set(parameters)) or has_defaults or has_variadic:
        raise ValueError(f"{method} must take computer and exactly the declared keyword-only inputs")

    postponed = any(isinstance(node, ast.ImportFrom) and node.module == "__future__" for node in tree.body)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _BLOCKED:
            raise ValueError(f"{node.id} is outside the desktop function interface")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError("Use public SDK attributes")
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node not in tree.body:
            raise ValueError("Keep imports at module scope")
        annotation = None
        if isinstance(node, ast.arg):
            annotation = node.annotation
        elif isinstance(node, ast.FunctionDef):
            annotation = node.returns
        if not postponed and annotation is not None:
            raise ValueError("Use from __future__ import annotations for function annotations")
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        argument = node.args[0] if node.args else None
        window = next((arg.value for arg in node.keywords if arg.arg == "window"), None)
        uses_numeric_app_or_close = node.func.attr in {"app", "close_window"} and _is_numeric_reference(argument)
        if uses_numeric_app_or_close or _is_numeric_reference(window):
            raise ValueError("Resolve application and window references from the current desktop")
        if node.func.attr == "element":
            selector = (
                argument
                if argument is not None
                else next((arg.value for arg in node.keywords if arg.arg == "target"), None)
            )
            if selector is not None:
                try:
                    value = ast.literal_eval(selector)
                except (ValueError, TypeError, SyntaxError):
                    continue  # Computed selectors are checked by the SDK facade at invocation.
                _require_element_path(value)
    globals_allowed = declared | set(dir(builtins)) | {"__name__", "__doc__"}
    scopes = [symtable.symtable(code, "program.py", "exec")]
    while scopes:
        scope = scopes.pop()
        for symbol in scope.get_symbols():
            if symbol.is_referenced() and symbol.is_global() and symbol.get_name() not in globals_allowed:
                raise ValueError(f"{symbol.get_name()} is undeclared in the function module")
        scopes.extend(scope.get_children())
    compile(code, "program.py", "exec")


def _is_numeric_reference(node):
    return isinstance(node, ast.Constant) and type(node.value) is int


def _literal(node, purpose):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError) as error:
        raise ValueError(f"{purpose} must be literal data") from error


def _require_element_path(value):
    try:
        element_path(value)
    except ValueError as error:
        raise ValueError(f"Compile requires a semantic element path. {error}") from error


def prepare(proposal, *, read_only):
    required = {"method", "title", "intent", "inputs", "code", "verify", "source_execution_ids"}
    if not isinstance(proposal, dict) or set(proposal) != required:
        raise ValueError("Expected a named Python function, inputs, verification and source execution IDs")
    method = proposal["method"]
    if not isinstance(method, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", method) or keyword.iskeyword(method):
        raise ValueError("Use a descriptive snake_case function name")
    _text(proposal["title"], "title", 180)
    _text(proposal["intent"], "matching task", 2048)
    inputs = proposal["inputs"]
    if not isinstance(inputs, dict):
        raise TypeError("Function inputs must be an object")
    examples, parameters = {}, {}
    for name, spec in inputs.items():
        valid_name = re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) and name != "computer" and not keyword.iskeyword(name)
        valid_spec = isinstance(spec, dict) and set(spec) == {"value", "meaning"}
        if not valid_name or not valid_spec:
            raise ValueError("Each input needs a name, original scalar value and meaning")
        _text(spec["meaning"], f"meaning of {name}", 512)
        value = spec["value"]
        kind = {str: "string", bool: "boolean", int: "integer", float: "number"}.get(type(value))
        if kind is None:
            raise ValueError("Function inputs must be scalar values")
        examples[name], parameters[name] = value, {"type": kind, "description": spec["meaning"]}
    _validate_code(proposal["code"], method, parameters)
    metadata = {
        "method": method,
        "title": proposal["title"],
        "intent": proposal["intent"],
        "parameters": parameters,
        "verify": proposal["verify"],
        "read_only": read_only,
    }
    return metadata, examples


def program(code, metadata):
    method = metadata["method"]
    kwargs = "".join(f", {name}=parameters[{name!r}]" for name in sorted(metadata["parameters"]))
    entry = (
        "\n\nasync def run(ctx, computer, parameters):\n"
        f"    value = await ctx.step({method!r}, lambda: {method}(computer{kwargs}))\n"
        "    ctx.event('function_return', value=value)\n"
    )
    return (code.strip() + _METADATA + repr(canonical(metadata)) + entry).encode()


def function_from(artifact):
    code, separator, tail = artifact.source.decode().rpartition(_METADATA)
    if not separator or artifact.manifest["constraints"] != revision():
        raise ValueError("The desktop function runtime has changed. Compile the task again.")
    metadata = json.loads(ast.literal_eval(tail.splitlines()[0]))
    if program(code, metadata) != artifact.source:
        raise ValueError("Invalid desktop function entry point")
    return metadata


def readme(metadata):
    args = "".join(f", {name}=..." for name in metadata["parameters"])
    parameters = "".join(
        f"- `{name}` ({spec['type']}) — {spec['description']}\n" for name, spec in metadata["parameters"].items()
    )
    return (
        f"# {metadata['title']}\n\n{metadata['intent']}\n\n"
        f"Import `program.py` and call `{metadata['method']}(computer{args})` "
        "with the current Computer session. In Agent, use the library entry with the same named inputs.\n\n"
        f"{parameters}"
    )


def _resolve(value, inputs):
    if isinstance(value, dict):
        if set(value) in ({"input"}, {"file_uri"}):
            field, name = next(iter(value.items()))
            if not isinstance(name, str) or name not in inputs:
                raise ValueError("Verification references an undeclared input")
            return Path(inputs[name]).as_uri() if field == "file_uri" else inputs[name]
        return {key: _resolve(child, inputs) for key, child in value.items()}
    if isinstance(value, list):
        return [_resolve(child, inputs) for child in value]
    return value


def verification(metadata, inputs):
    step = _resolve(metadata["verify"], inputs)
    if not isinstance(step, dict) or step.get("op") not in _READS:
        raise ValueError("Verification must independently read the requested outcome")
    op = step["op"]
    fields = {"op", "contains", "absent", "equals"}
    if op in {"read", "read_text", "list_windows"}:
        _text(step.get("app"), "verification application")
        fields.add("app")
    if op in {"read", "read_text"}:
        fields.add("window")
        if "window" in step:
            _text(step["window"], "verification window title")
    if op == "read_text":
        fields.add("element")
        _require_element_path(step.get("element"))
    if op == "read_file":
        fields.add("path")
        _text(step.get("path"), "verification path", 4096)
        if not Path(step["path"]).is_absolute():
            raise ValueError("File verification needs an absolute path")
    if op == "list_apps":
        fields.add("running_only")
        if not isinstance(step.get("running_only", False), bool):
            raise ValueError("running_only must be boolean")
    for field in ("contains", "absent"):
        markers = step.get(field, [])
        if not isinstance(markers, list):
            raise TypeError(f"{field} must list observed text")
        for marker in markers:
            _text(marker, f"{field} marker")
    if "equals" in step and (not isinstance(step["equals"], str) or len(step["equals"]) > 12000):
        raise ValueError("Exact verification needs at most 12000 characters")
    if set(step) - fields:
        raise ValueError(f"Unknown verification fields for {op}")
    if not metadata["read_only"] and not _has_check(step):
        raise ValueError("A function that changes the desktop needs an observable result check")
    return step


def _has_check(step):
    return bool(step.get("contains") or step.get("absent") or "equals" in step)


def _observed(step, text):
    return (
        all(marker in text for marker in step.get("contains", []))
        and all(marker not in text for marker in step.get("absent", []))
        and ("equals" not in step or text == step["equals"])
    )


def had_input(call):
    return (
        any(row.get("status") in {"dispatched", "verified"} for row in call["progress"].get("receipts", []))
        or "\nLaunch: " in "\n" + call["content"]
    )


def check_history(metadata, examples, evidence, execution_ids):
    step = verification(metadata, examples)
    if not isinstance(execution_ids, list) or not execution_ids or any(type(item) is not int for item in execution_ids):
        raise ValueError("Name the successful source execution IDs that support the function")
    calls = []
    for call in evidence["calls"]:
        progress = call["progress"]
        if (
            progress.get("executionId") in execution_ids
            and progress.get("completion") == "finished"
            and not call["is_error"]
        ):
            calls.append(call)
    if {call["progress"]["executionId"] for call in calls} != set(execution_ids):
        raise ValueError("The function cites an incomplete or unsuccessful source execution")
    if not metadata["read_only"] and not any(had_input(call) for call in calls):
        raise ValueError("The selected source executions do not show the desktop action")
    start = max((index for index, call in enumerate(calls) if had_input(call)), default=0)
    for call in calls[start:]:
        if _recorded_observation(step, call):
            return
    raise ValueError(f"The selected source executions do not establish the {step['op']} outcome")


def _recorded_observation(step, call):
    text, code, op = call["content"], call["code"], step["op"]
    if op == "read_file":
        if step["path"] not in code and step["path"] not in text:
            return False
        observed_call = re.search(r"\bopen\s*\(|\.\s*read_text\s*\(", code)
    else:
        observed_call = re.search(rf"\.\s*{op}\s*\(", code) or (
            op == "read" and step["app"] in text and "Window " in text
        )
    if not observed_call:
        return False
    if "equals" in step:
        expected = step["equals"]
        if any(line.strip() in {repr(expected), repr(expected.encode())} for line in text.splitlines()):
            text = expected
    return bool(_observed(step, text) and (_has_check(step) or text.strip()))


class _Desktop:
    """Record actual SDK input attempts in a single invocation, including partial effects."""

    def __init__(self, target, writes, *, read_only=False, root=False):
        self._target, self._writes, self._read_only, self._root = target, writes, read_only, root
        self._executor = getattr(target, "_executor", None)

    def __getattr__(self, name):
        if name == "target" and not self._root:
            return self._target.target
        if name not in _HANDLES | _READS | _WRITES | {"point"} or name == "read_file":
            raise AttributeError(name)
        operation = getattr(self._target, name)
        read_only_app_lookup = self._root and name == "app" and self._read_only
        can_report_launch = name == "read" and not self._root and not self._read_only

        def invoke(*args, **kwargs):
            if name in _HANDLES:
                if name == "element":
                    _require_element_path(args[0] if args else kwargs.get("target"))
                if read_only_app_lookup and (args or "selector" in kwargs):
                    selector = args[0] if args else kwargs.pop("selector")
                    matches = [
                        row
                        for row in self._target.list_apps()
                        if row.is_running and selector in (row.id, row.display_name)
                    ]
                    if len(matches) != 1:
                        raise ValueError("No unique running application for the verification")
                    args = (matches[0].id,)
                return _Desktop(operation(*args, **kwargs), self._writes, read_only=self._read_only)
            if name not in _WRITES:
                try:
                    value = operation(*args, **kwargs)
                except ComputerUseError as error:
                    if error.effect != "none":
                        self._writes.append({"method": name, "effect": error.effect})
                    raise
                if can_report_launch and "\nLaunch: " in "\n" + value.text:
                    self._writes.append({"method": "launch", "effect": "unknown"})
                return value
            if self._read_only:
                raise ValueError(f"Read-only function cannot dispatch {name}")
            attempt = {"method": name, "effect": "unknown"}
            self._writes.append(attempt)
            try:
                value = operation(*args, **kwargs)
            except ComputerUseError as error:
                attempt["effect"] = error.effect
                raise
            attempt["status"] = value.status
            return value

        return invoke


def _application(computer, name):
    matches = [row for row in computer.list_apps() if row.display_name == name and row.is_running]
    if len(matches) != 1:
        raise ValueError(f"No unique running application named {name}")
    return computer.app(matches[0].id)


def _read(computer, step):
    op = step["op"]
    if op == "read_file":
        with Path(step["path"]).open(encoding="utf-8") as stream:
            return stream.read(12001)
    if op == "list_apps":
        return "\n".join(
            row.display_name for row in computer.list_apps() if row.is_running or not step.get("running_only")
        )
    name = step["app"]
    if op == "list_windows" or "window" in step:
        windows = computer.list_windows(app_name=name).windows
        if op == "list_windows":
            return "\n".join(window.title or "" for window in windows)
        matches = [window for window in windows if window.title == step["window"]]
        if len(matches) != 1:
            raise ValueError(f"No unique current window {step['window']!r} for {name}")
        app, scope = computer.app(matches[0].app_id), {"window": matches[0].window_ref}
    else:
        app, scope = _application(computer, name), {}
    if op == "read_text":
        return app.element(step["element"], fresh=True, **scope).read_text(length=12000).text
    return app.read(full=True, **scope).text


def confirmed(artifact, parameters, reply):
    acceptance = reply.get("acceptance") or {}
    evidence = acceptance.get("evidence") or {}
    scope, expected = artifact.expectation(parameters)
    return (
        reply.get("status") == "succeeded"
        and reply.get("definition") == artifact.definition
        and reply.get("parameters") == parameters
        and acceptance.get("value") == "TRUE"
        and acceptance.get("scope") == scope
        and acceptance.get("condition") == artifact.definition["acceptance"]
        and bool(reply.get("execution_id"))
        and evidence.get("execution_id") == reply["execution_id"]
        and evidence.get("expected_sha256") == expected
    )


async def _run(computer, artifact, parameters, metadata):
    verify = verification(metadata, parameters)
    writes = []
    desktop = _Desktop(computer, writes, read_only=metadata["read_only"], root=True)
    returned = {}

    def capture(event):
        if event["kind"] == "function_return" and event["value"] is not None:
            try:
                encoded = canonical(event["value"])
            except (TypeError, ValueError):
                return
            if len(encoded) <= 4000:
                returned["value"] = json.loads(encoded)

    result = await execute(
        artifact.path,
        desktop,
        parameters,
        None,
        emit=capture,
        checkpoint=lambda: None,
        select=lambda definition: Selection(
            definition == artifact.definition, "Agent-selected revision", "selected", fingerprint(definition)
        ),
        timeout_s=40,
    )
    detail = (result.failure or {}).get("detail", "")
    if result.status == "awaiting_acceptance":
        try:
            verifier = ProgramContext(
                artifact,
                parameters,
                result.deadline_at_s - monotonic(),
                lambda _: None,
                lambda: None,
                None,
                getattr(computer, "_executor", None),
            )
            async with asyncio.timeout_at(result.deadline_at_s):
                observed = await verifier.step(verify["op"], partial(_read, computer, verify))
            value = "TRUE" if _observed(verify, observed) else "FALSE"
            detail = observed[:4000]
        except Exception as error:  # noqa: BLE001 - the outcome can still be uncertain after successful dispatch
            value, detail = "UNKNOWN", str(error)
        result = conclude(
            result,
            Check(
                result.acceptance_condition,
                value,
                result.scope,
                {"expected_sha256": result.expected_sha256, "execution_id": result.execution_id},
                monotonic(),
            ),
        )
    affected = any(write["effect"] != "none" for write in writes)
    effect = "none" if not affected else "confirmed" if result.status == "succeeded" else "unknown"
    return {
        "status": result.status,
        "effect": effect,
        "detail": detail,
        "definition": result.program,
        "execution_id": result.execution_id,
        "parameters": parameters,
        "duration_s": result.duration_s,
        "steps": result.steps,
        "input_attempts": writes,
        "failure": result.failure,
        "acceptance": asdict(result.acceptance) if result.acceptance else None,
        **(returned if result.status == "succeeded" else {}),
    }


async def _dispatch(computer, bundle, parameters):
    with TemporaryDirectory(prefix="computer-use-workflow-") as directory:
        path = Path(directory)
        (path / "manifest.json").write_text(canonical(bundle["manifest"]), encoding="utf-8")
        (path / "program.py").write_text(bundle["source"], encoding="utf-8")
        try:
            artifact = Artifact.read(path)
            if artifact.definition != bundle["definition"] or artifact.definition["acceptance"] != ACCEPTANCE:
                raise ValueError("Desktop function implementation differs on the MCP host")
            metadata = function_from(artifact)
            parameters = artifact.parameters(parameters)
            verification(metadata, parameters)
        except (ValueError, SyntaxError, TypeError, KeyError) as error:
            return {"status": "refused", "effect": "none", "detail": str(error)}
        return await _run(computer, artifact, parameters, metadata)


def run_worker(computer, bundle, parameters):
    return session_executor().run(lambda: _dispatch(computer, bundle, parameters))


def script(artifact, parameters):
    bundle = {"manifest": artifact.manifest, "source": artifact.source.decode(), "definition": artifact.definition}
    return (
        "from computer_use_agent.compile.workflow import run_worker as _compile_function\n"
        f"report_result(_compile_function(computer, {bundle!r}, {parameters!r}))"
    )
