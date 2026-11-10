# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One bounded, synthetic SDK mechanism fixture; not an application task catalog."""

import hashlib
import json

from computer_use.programs.artifact import Artifact


DOCUMENT_PROGRAM = """import hashlib
import re

async def run(ctx, computer, p):
    async def environment():
        observed = await ctx.observe_environment()
        expected = dict(platform="test-os", app_version="1", locale="test", desktop_mode="shared",
                        foreground_assist=False, foreground_app="test.application")
        unknown = any(observed.get(k) is None for k in expected)
        ctx.check("compatible_environment", "UNKNOWN" if unknown else "TRUE" if all(observed[k] == v for k, v in expected.items()) else "FALSE")
    await ctx.step("environment_precheck", environment)
    apps = await ctx.step("discover", computer.list_apps)
    matches = [a for a in apps if a.is_running and a.display_name == p["document"]]
    ctx.check("unique_document", "TRUE" if len(matches) == 1 else "FALSE")
    app = computer.app(matches[0].id)
    observed = await ctx.step("observe", lambda: app.read(view="tree"))
    refs = re.findall(r"^(\\d+) editor$", observed.text, re.MULTILINE)
    ctx.check("editor_scope", "TRUE" if len(refs) == 1 else "UNKNOWN")
    editor = await ctx.step("bind", lambda: app.element(int(refs[0])))
    initial = await ctx.step("read_initial", editor.read_text)
    ctx.check("initial_content", "UNKNOWN" if initial.next else "TRUE" if initial.text == p["initial"] else "FALSE")
    await ctx.step("precheck_command", lambda: app.element([{"role":"button", "name":"Commit"}]))
    await ctx.step("replace_content", lambda: editor.type_text(p["replacement"], replace=True), write=True)
    changed = await ctx.step("read_replacement", editor.read_text)
    ctx.check("replacement_visible", "UNKNOWN" if changed.next else "TRUE" if changed.text == p["replacement"] else "FALSE")
    await ctx.step("environment_before_commit", environment)
    await ctx.step("observe_command", lambda: app.read(view="tree"))
    command = await ctx.step("bind_command", lambda: app.element([{"role":"button", "name":"Commit"}]))
    await ctx.step("commit", command.click, write=True)
"""


def artifact_at(path, *, source=DOCUMENT_PROGRAM, program_id="test.document", schema=None, acceptance=None):
    path.mkdir(parents=True, exist_ok=True)
    (path / "program.py").write_text(source)
    schema = schema or {
        key: {"type": "string", "min_length": 1, "max_length": 4096} for key in ("document", "initial", "replacement")
    }
    manifest = {
        "api": "computer-use.program.v1",
        "id": program_id,
        "version": "1",
        "entry": "program.py",
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "parameters": schema,
        "acceptance": acceptance
        or {"condition": "independent_result", "scope_parameter": "document", "expected_parameters": ["replacement"]},
        "constraints": "Synthetic mechanism fixture; no desktop application",
    }
    (path / "manifest.json").write_text(json.dumps(manifest))
    return Artifact.read(path)


def observed_editor_ref(observation):
    """Finite synthetic observation fixture for missing/ambiguous scope cases."""
    import re

    refs = re.findall(r"^\s*(\d+) [^\n]*, ID: fixture-editor$", observation, re.MULTILINE)
    if len(refs) != 1:
        raise RuntimeError("Observation does not identify one editor")
    return int(refs[0])
