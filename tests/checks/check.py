import argparse
import ast
import re
import sys
import unicodedata
from urllib.parse import unquote

from computer_use.paths import checkoutRoot
from computer_use_native_tools.pointer import generatePointerAssets


"Check current docs, public signatures and protocol module boundaries."


SKIP = {"node_modules", "dist", "outputs", "runs", "__pycache__", "build", "computer_use_toolkit.egg-info"}
FENCE = re.compile("^(`{3,}|~{3,})[^\\n]*\\n[\\s\\S]*?^\\1\\s*$", re.M)


def files(root):
    for child in root.iterdir():
        if child.name.startswith(".") or child.name in SKIP or child.is_symlink():
            continue
        if child.is_dir():
            yield from files(child)
        elif child.suffix == ".md":
            yield child


def prose(path):
    return FENCE.sub("", path.read_text(encoding="utf-8"))


def anchors(path):
    seen = {}
    result = set()
    source = prose(path)
    for title in re.findall("^#{1,6}\\s+(.+?)\\s*#*$", source, re.M):
        title = re.sub("<[^>]*>", "", title).lower()
        title = "".join(c for c in title if c in "-_" or unicodedata.category(c)[0] not in "PS")
        title = re.sub("\\s", "-", title)
        n = seen.get(title, 0)
        seen[title] = n + 1
        result.add(title + (f"-{n}" if n else ""))
    result.update(re.findall("<(?:a|span)\\s+(?:name|id)=[\\'\"]([^\\'\"]+)", source))
    return result


def signature(node):
    return (
        type(node).__name__,
        [(a.arg, ast.unparse(a.annotation) if a.annotation else None) for a in node.args.args],
        [ast.unparse(d) for d in node.args.defaults],
        [
            (a.arg, ast.unparse(a.annotation) if a.annotation else None, ast.unparse(d) if d is not None else None)
            for a, d in zip(node.args.kwonlyargs, node.args.kw_defaults)
        ],
        ast.unparse(node.returns) if node.returns else None,
    )


def surface(source):
    return {
        cls.name: {
            method.name: signature(method)
            for method in cls.body
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and not method.name.startswith("_")
        }
        for cls in ast.parse(source).body
        if isinstance(cls, ast.ClassDef) and cls.name in ("Computer", "UI", "App", "Window", "Element")
    }


def documentation(root):
    failures = []
    count = 0
    for file in files(root):
        count += 1
        source = re.sub("(`+)[\\s\\S]*?\\1", "", prose(file))
        for match in re.finditer('!?\\[[^\\]\\n]*\\]\\((<[^>]+>|[^\\s)]+)(?:\\s+"[^"]*")?\\)', source):
            url = match[1].strip("<>")
            if url.startswith("~") or re.match("^[a-z][a-z0-9+.-]*:", url, re.I):
                continue
            target, _, fragment = url.partition("#")
            path = (file.parent / re.sub(":\\d+$", "", unquote(target))).resolve() if target else file
            if not path.is_relative_to(root):
                continue
            local = path.relative_to(root).as_posix()
            if local.startswith((".artifacts/", "outputs/", "computer_use_agent/runs/")):
                continue
            if not path.exists():
                failures.append(f"{file.relative_to(root)}: missing {url}")
            elif fragment and path.suffix == ".md" and unquote(fragment) not in anchors(path):
                failures.append(f"{file.relative_to(root)}: missing anchor {url}")
    expected = surface((root / "computer_use_engine/__init__.pyi").read_text(encoding="utf-8"))
    for name in ["docs/api/agent.md"]:
        source = (root / name).read_text(encoding="utf-8")
        blocks = re.findall("```python\\n([\\s\\S]*?)\\n```", source)
        if not any(surface(code) == expected for code in blocks):
            failures.append(f"{name}: Python API signatures differ from public SDK stub")
        for match in re.finditer("```python\\n([\\s\\S]*?)\\n```", source):
            try:
                compile(match[1], name, "exec", ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
            except SyntaxError as error:
                failures.append(f"{name}: invalid Python example: {error}")
    if failures:
        raise ValueError("\n".join(failures))
    print((f"Documentation links/anchors checked ({count} files); Python API signatures match SDK."))


def module_imports(file, base):
    "Resolve static imports without importing desktop or provider modules."
    parts = list(file.relative_to(base).with_suffix("").parts)
    package = ["computer_use", *parts[:-1]]
    for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # level=1 retains this package; higher levels remove parents.
                prefix = package[: len(package) - node.level + 1]
                module = ".".join([*prefix, *(node.module or "").split(".")]).rstrip(".")
            else:
                module = node.module or ""
            if module:
                yield module, node.lineno
            # ``from .. import observation`` also names a domain dependency.
            for alias in node.names:
                if alias.name != "*":
                    yield ".".join((module, alias.name)).strip("."), node.lineno


def sdk_sources(base):
    "The SDK package maps to computer_use_engine; native sources remain a separate domain."
    for file in sorted(base.rglob("*.py")):
        parts = file.relative_to(base).parts
        if parts[0] not in ("backends", "_resources") and "__pycache__" not in parts:
            yield file


def pipeline_dependencies(base):
    """Validate actual v6 dependency directions, including local imports.

    Static imports cannot prove Python effect purity. Native objects/fields and
    domain records may be shared; flows and state ownership cannot point back
    at their consumers. No old-directory fallback silently weakens this check.
    """
    failures = []
    runner_stdlib = {
        "__future__",
        "abc",
        "collections",
        "contextlib",
        "dataclasses",
        "enum",
        "functools",
        "inspect",
        "types",
        "typing",
    }

    def inside(module, prefix):
        return module == prefix or module.startswith(prefix + ".")

    external = (
        "computer_use_mcp",
        "computer_use_react_demo",
        "computer_use_experience",
        "computer_use_decision",
        "computer_use_devtools",
        "computer_use_native_tools",
        "computer_use_tool_shared",
        "computer_use_pointer_preview",
        "computer_use_examples",
    )
    high_level = ("computer_use.client", "computer_use.interaction", *external)
    for file in sdk_sources(base):
        relative = file.relative_to(base)
        if not relative.parts:
            continue
        folder = relative.parts[0]
        display = relative.parts[:3] == ("interaction", "read", "presentation")
        write = relative.parts[:2] == ("interaction", "write")
        seen = set()
        for imported, line in module_imports(file, base):
            reason = None
            if any(inside(imported, prefix) for prefix in external):
                reason = "SDK must not depend on external integrations, tools, extensions or experiments"
            elif folder == "pipeline":
                if not inside(imported, "computer_use.pipeline") and imported.split(".")[0] not in runner_stdlib:
                    reason = "pipeline runner must remain domain-independent and free of native I/O"
            elif folder == "native":
                if any(inside(imported, prefix) for prefix in (*high_level, "computer_use.session")):
                    reason = "native must not depend on interaction, session or public/host adapters"
            elif folder == "session":
                if any(inside(imported, prefix) for prefix in high_level):
                    reason = "session must not depend on interaction or public/host adapters"
                elif any(
                    inside(imported, prefix)
                    for prefix in (
                        "computer_use.native.connection",
                        "computer_use.native.port",
                        "computer_use.native.transport",
                        "computer_use.native.query",
                    )
                ):
                    reason = "session must not query native systems or own transport"
            elif folder == "interaction":
                if inside(imported, "computer_use.client"):
                    reason = "interaction must not depend on public client wrappers"
                elif write and inside(imported, "computer_use.interaction.read"):
                    reason = "write must not depend on read/presentation rules"
                elif display:
                    allowed_native = (
                        "computer_use.native.contracts",
                        "computer_use.native.scopes",
                        "computer_use.native.semantics",
                    )
                    if inside(imported, "computer_use.native") and not any(
                        inside(imported, prefix) for prefix in allowed_native
                    ):
                        reason = "presentation must not depend on native I/O or execution services"
                    elif inside(imported, "computer_use.session"):
                        reason = "presentation must not depend on persistent session services"
                    elif inside(imported, "computer_use.interaction") and not any(
                        inside(imported, prefix)
                        for prefix in (
                            "computer_use.interaction.read.presentation",
                            "computer_use.interaction.read.facts.groups",
                            "computer_use.interaction.read.facts.table",
                            "computer_use.interaction.read.facts.platforms",
                        )
                    ):
                        reason = "presentation must not invoke runtime read/write/query flows"
                    elif any(inside(imported, prefix) for prefix in ("computer_use_mcp", "computer_use_react_demo")):
                        reason = "presentation must not invoke public/host adapters"
            if reason is not None and (line, reason) not in seen:
                failures.append(f"{relative}:{line}: {reason}: {imported}")
                seen.add((line, reason))
    for obsolete in ("execution", "observation"):
        if any((base / obsolete).rglob("*.py")):
            failures.append(f"{obsolete}: obsolete top-level branch must not remain beside interaction")
    return failures


def component_dependencies(root):
    "Optional SDK consumers must not acquire a dependency on their hosts."
    failures = []
    for directory, package in (
        ("extensions/experience", "computer_use_experience"),
        ("experiments/decision", "computer_use_decision"),
    ):
        base = root / directory
        for file in sorted(base.rglob("*.py")):
            for imported, line in module_imports(file, base):
                # Absolute imports are the cross-component contract; local
                # relative imports remain inside this consumer's own package.
                if imported.startswith(
                    (
                        "computer_use_mcp",
                        "computer_use_react_demo",
                        "computer_use_devtools",
                    )
                ):
                    failures.append(
                        (f"{file.relative_to(root)}:{line}: {package} must not depend on its host: {imported}")
                    )
    return failures


def pass_declaration(call, factories):
    """Find legacy callbacks and explicit definitions without executing code.

    ``Pass.factory`` names the unbound factory; legacy ``Pass`` receives its
    result. Both forms must identify the same implementation for registration
    counts, closure checks and leaf-to-leaf boundaries.
    """
    if not isinstance(call, ast.Call):
        return None

    def symbol(value):
        if isinstance(value, ast.Name):
            return factories.get(value.id)
        if isinstance(value, ast.Attribute) and value.attr in ("Pass", "PassDefinition"):
            return value.attr
        return None

    method = call.func.attr if isinstance(call.func, ast.Attribute) else None
    definition = method in ("define", "factory") and symbol(call.func.value) == "Pass"
    if definition:
        keyword = "factory" if method == "factory" else "function"
        value = next((item.value for item in call.keywords if item.arg == keyword), None)
        if value is None and len(call.args) >= 2:
            value = call.args[1]
        return (value, method == "factory") if value is not None else None
    constructor = symbol(call.func)
    if constructor is not None:
        keyword = "factory" if constructor == "PassDefinition" else "function"
        value = next((item.value for item in call.keywords if item.arg == keyword), None)
        if value is None and len(call.args) >= 2:
            value = call.args[1]
        if value is not None:
            return value, constructor == "PassDefinition" or isinstance(value, ast.Call)
    return None


def pipeline_symbols(tree):
    "Resolve generic runner and Pass aliases in one source module."
    runners = {"Pass", "PassDefinition", "Pipeline", "Sequence", "Select", "Repeat", "Scope"}
    leaves = {"Pass": "Pass", "PassDefinition": "PassDefinition"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("computer_use.pipeline", "computer_use.pipeline.core"):
            for alias in node.names:
                if alias.name in runners:
                    runners.add(alias.asname or alias.name)
                if alias.name in ("Pass", "PassDefinition"):
                    leaves[alias.asname or alias.name] = alias.name
    return runners, leaves


def pass_factories(tree, leaves):
    "Include existing narrow composition wrappers without importing them."
    return {
        **leaves,
        **{
            node.name: "Pass"
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(part, ast.Return) and pass_declaration(part.value, leaves) is not None
                for part in ast.walk(node)
            )
        },
    }


def pass_dependencies(base):
    """Reject explicit leaf-to-leaf calls and runner entry inside leaf Passes.

    Arbitrary dynamic Python dispatch cannot be proven statically. Narrow
    helper chains additionally need domain regression tests (for example the
    candidate predicate/phase-table probe).
    """
    failures = []
    for folder in ("interaction",):
        for file in sorted((base / folder).rglob("*.py")):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

            def enclosing(node, kinds, *, parents=parents, tree=tree):
                while node in parents:
                    node = parents[node]
                    if isinstance(node, kinds):
                        return node
                return tree

            functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]

            def resolve(value, origin, *, functions=functions, parents=parents, tree=tree):
                if isinstance(value, ast.Lambda):
                    return value
                if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id == "self":
                    owner = enclosing(origin, ast.ClassDef)
                    return next((fn for fn in functions if fn.name == value.attr and parents.get(fn) is owner), None)
                if isinstance(value, ast.Name):
                    scope = enclosing(origin, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    while True:
                        match = next((fn for fn in functions if fn.name == value.id and parents.get(fn) is scope), None)
                        if match is not None or scope is tree:
                            return match
                        scope = enclosing(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                return None

            # Local leaf factories such as resolution.query(name, function)
            # wrap Pass and carry the same no-hidden-runner restriction.
            runner_aliases, leaves = pipeline_symbols(tree)
            factories = pass_factories(tree, leaves)
            registered = set()
            for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                declaration = pass_declaration(call, factories)
                if declaration is None:
                    continue
                value, factory = declaration
                function = resolve(value.func if isinstance(value, ast.Call) else value, call)
                if function is not None:
                    registered.add(function)
                    if factory:
                        for statement in ast.walk(function):
                            if (
                                isinstance(statement, ast.Return)
                                and enclosing(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) is function
                            ):
                                callback = resolve(statement.value, statement)
                                if callback is not None:
                                    registered.add(callback)
            for assignment in ast.walk(tree):
                if (
                    isinstance(assignment, (ast.Assign, ast.AnnAssign))
                    and pass_declaration(assignment.value, leaves) is not None
                ):
                    targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
                    runner_aliases.update(ast.unparse(target) for target in targets)

            def runner_constructor(value, *, runner_aliases=runner_aliases):
                if not isinstance(value, ast.Call):
                    return False
                name = (
                    value.func.id
                    if isinstance(value.func, ast.Name)
                    else value.func.attr
                    if isinstance(value.func, ast.Attribute)
                    else ""
                )
                return name in runner_aliases or name.endswith("Pipeline")

            runner_values = set()
            for assignment in ast.walk(tree):
                if isinstance(assignment, (ast.Assign, ast.AnnAssign)) and runner_constructor(assignment.value):
                    targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
                    runner_values.update(ast.unparse(target) for target in targets)
            for function in registered:
                body = function.body if isinstance(function, ast.Lambda) else function
                for call in (node for node in ast.walk(body) if isinstance(node, ast.Call)):
                    callee = resolve(call.func, call)
                    reason = None
                    if callee in registered:
                        reason = "a leaf Pass must not call another registered Pass"
                    elif (
                        isinstance(call.func, ast.Attribute)
                        and call.func.attr in ("run", "arun")
                        and (runner_constructor(call.func.value) or ast.unparse(call.func.value) in runner_values)
                    ):
                        reason = "a leaf Pass must not start another pipeline runner"
                    if reason:
                        failures.append(f"{file.relative_to(base)}:{call.lineno}: {reason}")
    failures.extend(pass_file_contracts(base))
    return failures


def pass_file_contracts(base):
    """Track imported Pass callbacks, factory closures and obvious hidden runners.

    A file may include narrow helpers for its one Pass. It may not implement
    two separately registered leaves, import a sibling Pass, or start a flow.
    Generic IR editor adapters remain allowed: an editor named run is not a
    pipeline runner. Dynamic Python dispatch still requires domain tests.
    """
    failures, registrations, trees, modules = [], {}, {}, {}
    root = base / "interaction"
    for file in sorted(root.rglob("*.py")):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        module = "computer_use." + ".".join(file.relative_to(base).with_suffix("").parts)
        trees[file], modules[module] = tree, file
    for file, tree in trees.items():
        module = "computer_use." + ".".join(file.relative_to(base).with_suffix("").parts)
        imports = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports[alias.asname or alias.name.split(".")[0]] = (
                        alias.name if alias.asname else alias.name.split(".")[0],
                        None,
                    )
            elif isinstance(node, ast.ImportFrom):
                package = module.split(".")[:-1]
                prefix = package[: len(package) - node.level + 1] if node.level else []
                imported = ".".join([*prefix, *(node.module or "").split(".")]).rstrip(".")
                for alias in node.names:
                    imports[alias.asname or alias.name] = (imported, alias.name)

        def implementation(value, *, imports=imports, module=module):
            if isinstance(value, ast.Name):
                return imports.get(value.id, (module, value.id))
            parts = []
            while isinstance(value, ast.Attribute):
                parts.insert(0, value.attr)
                value = value.value
            if isinstance(value, ast.Name) and value.id in imports and parts:
                source, imported = imports[value.id]
                owner = ".".join(part for part in (source, imported, *parts[:-1]) if part)
                return owner, parts[-1]
            return None

        functions = {
            node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        runner_aliases, leaves = pipeline_symbols(tree)
        factories = pass_factories(tree, leaves)
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            declaration = pass_declaration(call, factories)
            if declaration is None:
                continue
            value, _ = declaration
            if isinstance(value, ast.Lambda) and file.name == "pipeline.py":
                editor = (
                    isinstance(value.body, ast.Call)
                    and isinstance(value.body.func, ast.Attribute)
                    and value.body.func.attr == "run"
                    and isinstance(value.body.func.value, ast.Attribute)
                    and value.body.func.value.attr == "edit"
                )
                if not editor:
                    failures.append(
                        (
                            f"{file.relative_to(base)}"
                            f":"
                            f"{call.lineno}"
                            f": inline Pass implementation must live in its own passes file"
                        )
                    )
            value = value.func if isinstance(value, ast.Call) else value
            origin = implementation(value)
            if origin is not None:
                imported, symbol = origin
                target = modules.get(imported)
                if target is not None:
                    registrations.setdefault(target, set()).add(symbol)
                    # A generic composition factory's formal callback is not
                    # another concrete registered Pass implementation.
                    if target == file and symbol not in functions:
                        continue
                    target_functions = {
                        node.name: node
                        for node in ast.walk(trees[target])
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    }
                    if target.parent.name != "passes" and file.name == "pipeline.py" and symbol in target_functions:
                        fn = target_functions[symbol]
                        calls = [node for node in ast.walk(fn) if isinstance(node, ast.Call)]
                        editor_only = bool(calls) and all(
                            isinstance(node.func, ast.Attribute)
                            and node.func.attr == "run"
                            and isinstance(node.func.value, ast.Attribute)
                            and node.func.value.attr == "edit"
                            for node in calls
                        )
                        if not editor_only:
                            failures.append(
                                (
                                    f"{file.relative_to(base)}"
                                    f":"
                                    f"{call.lineno}"
                                    f": registered Pass implementation must live in its own passes "
                                    f"file"
                                )
                            )
        if file.parent.name != "passes":
            continue
        if file.name == "__init__.py":
            if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for node in tree.body):
                failures.append(f"{file.relative_to(base)}: passes package cannot aggregate implementations")
            continue
        for imported, line in module_imports(file, base):
            if imported in modules and modules[imported].parent.name == "passes" and modules[imported] != file:
                failures.append(
                    (
                        f"{file.relative_to(base)}"
                        f":"
                        f"{line}"
                        f": a leaf Pass must not import a sibling Pass; extract shared "
                        f"support"
                    )
                )
            if imported in modules and modules[imported].name == "pipeline.py":
                failures.append(f"{file.relative_to(base)}:{line}: a leaf Pass must not import a pipeline flow")
        runner_values = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and pass_declaration(node.value, leaves) is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                runner_aliases.update(ast.unparse(target) for target in targets)

        def constructor(node, *, runner_aliases=runner_aliases):
            if not isinstance(node, ast.Call):
                return False
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            return name in runner_aliases or name.endswith("Pipeline")

        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and constructor(node.value):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                runner_values.update(ast.unparse(target) for target in targets)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("run", "arun")
            ):
                if constructor(node.func.value) or ast.unparse(node.func.value) in runner_values:
                    failures.append(
                        (f"{file.relative_to(base)}:{node.lineno}: a leaf Pass must not start another pipeline runner")
                    )
    for file, symbols in registrations.items():
        if file.parent.name == "passes" and len(symbols) > 1:
            failures.append(
                (
                    f"{file.relative_to(base)}"
                    f": each passes file must implement one registered Pass; found "
                    f"{sorted(symbols)}"
                )
            )
    return failures


def architecture(root):
    base = root / "computer_use_engine"
    failures = []
    for file in (base / "protocol").glob("*.py"):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                if node.level == 1 or node.module in ("__future__", None):
                    continue
                if node.level == 2 and node.module == "errors":
                    continue
                if node.level > 0 or (node.module or "").startswith("computer_use."):
                    failures.append(f"{file.name}: forbidden protocol dependency {node.module}")
    pure = base / "native/semantics.py"
    allowed = {"__future__", "collections.abc", "dataclasses", "enum", "re", "typing"}
    for node in ast.walk(ast.parse(pure.read_text(encoding="utf-8") if pure.exists() else "")):
        if isinstance(node, ast.ImportFrom):
            if node.level or node.module not in allowed:
                failures.append(f"{pure.name}: non-pure dependency {node.module}")
        elif isinstance(node, ast.Import):
            for name in node.names:
                if name.name not in allowed:
                    failures.append(f"{pure.name}: non-pure dependency {name.name}")
    failures.extend(pipeline_dependencies(base))
    failures.extend(pass_dependencies(base))
    failures.extend(component_dependencies(root))
    if failures:
        raise ValueError("\n".join(failures))
    print(
        (
            "SDK/component boundaries, protocol/native/session contracts, pure "
            "semantics, interaction separation and per-file Pass/runner contracts "
            "checked."
        )
    )


def cli(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", action="store_true")
    parser.add_argument("--architecture", action="store_true")
    args = parser.parse_args(argv)
    root = checkoutRoot()
    if root is None:
        raise SystemExit("Source checkout required for development checks.")
    try:
        if args.docs or not args.architecture:
            documentation(root)
        if args.architecture or not args.docs:
            architecture(root)
        if not args.docs and not args.architecture:
            for directory in ("computer_use_engine", "interface_adapter", "os_adapter", "scripts", "tests"):
                for file in (root / directory).rglob("*.py"):
                    if not any(
                        part.startswith(".") or part in ("__pycache__", "node_modules", "runs")
                        for part in file.relative_to(root).parts
                    ):
                        compile(file.read_text(encoding="utf-8"), str(file), "exec")
            print(generatePointerAssets(check=True))
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    cli()
