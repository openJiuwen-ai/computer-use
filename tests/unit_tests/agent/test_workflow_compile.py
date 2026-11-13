# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compile named functions, independently verify them, and rediscover them in another session."""

import asyncio
import json
import runpy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openjiuwen.core.foundation.tool.schema import ToolOutput

from computer_use_agent import AgentConfig, create_agent
from computer_use_agent.compile import workflow
from computer_use_agent.compile.compiler import Compiler
from computer_use_agent.compile.library import Library

from .conftest import answer, tool_call


def _call(name, arguments):
    message = tool_call("")
    message["tool_calls"][0]["function"].update(name=name, arguments=json.dumps(arguments))
    return message


def test_compile_a_regular_task_from_the_conversation(model_server, tmp_path):
    source = (
        "from types import SimpleNamespace\n"
        "computer = SimpleNamespace(list_apps=lambda: [SimpleNamespace(display_name='Sample', is_running=True)])\n"
        "print([app.display_name for app in computer.list_apps() if app.is_running])"
    )
    server = model_server([tool_call(source), answer("Here are the running applications."), _call("compile_task", {})])
    proposal = {
        "method": "list_running_apps",
        "title": "List running applications",
        "intent": "List the applications currently running on the desktop",
        "inputs": {},
        "code": (
            "def list_running_apps(computer):\n"
            "    return [app.display_name for app in computer.list_apps() if app.is_running]\n"
        ),
        "verify": {"op": "list_apps", "running_only": True},
        "source_execution_ids": [1],
    }

    async def scenario():
        library_path = tmp_path / "library"
        config = AgentConfig(workspace=str(tmp_path), compile_dir=str(library_path), check_desktop=False)
        events = []
        async with create_agent(
            server.model(), config=config, on_event=lambda kind, data: events.append((kind, data))
        ) as agent:
            proposals = (
                {**proposal, "code": "def list_running_apps(computer):\n    return missing_name()\n"},
                proposal,
            )
            compile_model = AsyncMock(
                side_effect=[SimpleNamespace(content=json.dumps({"function": value})) for value in proposals]
            )
            agent._compile_model = SimpleNamespace(invoke=compile_model)
            await agent.invoke({"query": "List running apps", "conversation_id": "same"})
            assert not library_path.exists()
            count = sum(kind == "tool_call" and data["name"] == "computer_use" for kind, data in events)
            reply = await agent.invoke({"query": "Compile the task you just did", "conversation_id": "same"})
            assert reply["output"].startswith("Published workflow-")
            assert "list_running_apps()" in reply["output"]
            published = next(data for kind, data in events if kind == "compile_published")
            artifact = Library(library_path).lookup(published["id"])
            package = runpy.run_path(str(artifact.path / "program.py"))
            desktop = SimpleNamespace(
                list_apps=lambda: [
                    SimpleNamespace(display_name="Running", is_running=True),
                    SimpleNamespace(display_name="Closed", is_running=False),
                ]
            )
            assert package["list_running_apps"](desktop) == ["Running"]
            assert "list_running_apps" in (artifact.path / "README.md").read_text()
            assert "missing_name is undeclared" in compile_model.await_args.args[0][-1]["content"]
            assert sum(kind == "tool_call" and data["name"] == "computer_use" for kind, data in events) == count + 1
            server.replies.extend(
                [
                    _call("run_compiled", {"program_id": published["id"], "parameters": {}}),
                    answer("Sample is running."),
                ]
            )
            await agent.invoke({"query": "List running apps", "conversation_id": "reuse"})
            execution = next(data for kind, data in events if kind == "compile_execution")
            assert execution["status"] == "succeeded"
            assert execution["effect"] == "none"
            assert "Sample" in execution["detail"]
            assert execution["value"] == ["Sample"]
            assert sum(kind == "tool_call" and data["name"] == "computer_use" for kind, data in events) == count + 2
            assert any(
                "list_running_apps" in json.dumps(request["tools"])
                for request in server.requests
                if request.get("tools")
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("selector", ["42", "'Apply'", "[42]", "[{'id': 42}]"])
def test_compile_rejects_a_non_path_element_selector(selector):
    proposal = {
        "method": "apply_settings",
        "title": "Apply settings",
        "intent": "Apply the current settings in Sample Editor",
        "inputs": {},
        "code": (
            f"def apply_settings(computer):\n    computer.app('Sample Editor').element(target={selector}).click()\n"
        ),
        "verify": {"op": "read", "app": "Sample Editor", "contains": ["Applied"]},
        "source_execution_ids": [1],
    }

    with pytest.raises(ValueError, match="semantic (element )?path"):
        workflow.prepare(proposal, read_only=False)


@pytest.mark.parametrize("state", ["ready", "wrong_window", "write_not_observed", "numeric_selector"])
def test_function_draft_rebinds_and_only_publishes_after_a_verified_repeat(tmp_path, state):
    source_path, target_path = tmp_path / "source.txt", tmp_path / "repeat.txt"
    target_path.write_text("Initial value", encoding="utf-8")
    proposal = {
        "method": "replace_note_text",
        "title": "Replace note text",
        "intent": "Replace the text in a named document in Sample Editor.",
        "inputs": {
            "text": {"value": "First result", "meaning": "New document text"},
            "document": {"value": "Original", "meaning": "Exact document window title"},
            "path": {"value": str(source_path), "meaning": "Absolute path to the document"},
        },
        "code": (
            "def replace_note_text(computer, *, text, document, path):\n"
            "    windows = computer.list_windows(app_name='Sample Editor').windows\n"
            "    matches = [w for w in windows if w.title == document]\n"
            "    if len(matches) != 1:\n"
            "        raise ValueError('Expected one matching document')\n"
            "    window = matches[0]\n"
            "    app = computer.app(window.app_id)\n"
            "    editor = app.element([{'role': 'textArea'}], fresh=True, window=window.window_ref)\n"
            "    editor.type_text(text, replace=True)\n"
        ),
        "verify": {"op": "read_file", "path": {"input": "path"}, "equals": {"input": "text"}},
        "source_execution_ids": [1, 2, 3],
    }
    if state == "numeric_selector":
        proposal["code"] = proposal["code"].replace(
            "    editor = app.element([{'role': 'textArea'}], fresh=True, window=window.window_ref)\n",
            "    element_ref = int(app.read(window=window.window_ref).text.split()[0])\n"
            "    editor = app.element(target=element_ref, window=window.window_ref)\n",
        )
    library_path = tmp_path / "library"
    ordinary_run = AsyncMock(side_effect=AssertionError("Compilation repeated a desktop write"))
    compiler = Compiler(library_path, ordinary_run, lambda *_: None)
    source = compiler.begin("recorded-turn", "first-conversation", "Replace and save the document")
    calls = [
        ("app.read()", "Document Original\n41 text area", []),
        (
            "app.element(41).type_text('First result', replace=True)",
            "Input dispatched",
            [{"status": "dispatched", "intent": "replace", "target": "Window 3 / Element 41"}],
        ),
        (f"print(repr(open({str(source_path)!r}, 'rb').read()))", "b'First result'", []),
        *[("print('additional observation')", "additional observation", [])] * 13,
    ]
    for execution, (code, content, receipts) in enumerate(calls, start=1):
        compiler.record(
            source,
            {"code": code},
            {
                "mcp_result": {"content": [{"type": "text", "text": content}]},
                "execution_progress": {"completion": "finished", "executionId": execution, "receipts": receipts},
            },
        )
    compiler.finish(source, {"result_type": "answer", "output": "Replaced and saved."})

    class Desktop:
        element_ref = 92

        def __init__(self):
            self.lookups = []
            self.writes = []

        def list_windows(self, *, app_name):
            assert app_name == "Sample Editor"
            name = "Unrelated" if state == "wrong_window" else "New document"
            return SimpleNamespace(windows=[SimpleNamespace(title=name, app_id=14, window_ref=77)])

        def app(self, ref):
            assert ref == 14
            return self

        def read(self, *, window):
            assert window == 77
            return SimpleNamespace(text=f"{self.element_ref} text area")

        def element(self, target, *, window, fresh=False):
            assert window == 77
            self.lookups.append(target)
            assert target == self.element_ref or (fresh and target == [{"role": "textArea"}])
            resolved_ref = self.element_ref

            def type_text(text, *, replace):
                assert replace
                self.writes.append(resolved_ref)
                if state != "write_not_observed":
                    target_path.write_text(text, encoding="utf-8")
                return SimpleNamespace(status="dispatched", detail="Text input dispatched")

            return SimpleNamespace(type_text=type_text)

    desktop = Desktop()
    parameters = {"document": "New document", "text": "Second result", "path": str(target_path)}

    async def scenario():
        model = SimpleNamespace(
            invoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps({"function": proposal})))
        )
        result = await compiler.compile_task(model, source.id)
        assert result["status"] == "ready_to_validate"
        ordinary_run.assert_not_called()
        assert compiler.catalog() is None
        draft_id = result["draft_id"]
        artifact, report = compiler.library.lookup_draft(draft_id)
        assert len(json.loads((artifact.path / "source.json").read_text())["calls"]) == len(calls)
        assert report["cases"] == []
        assert "First result" not in artifact.source.decode()

        async def run_code(title, code):
            assert title == proposal["title"] and "replace_note_text" in code
            supplied = {**parameters, workflow.SCOPE: artifact.definition["id"]}
            reply = await workflow._run(desktop, artifact, supplied, workflow.function_from(artifact))
            return ToolOutput(success=True, data={"mcp_result": {"structuredContent": reply}})

        restarted = Compiler(library_path, run_code, lambda *_: None)
        assert restarted.catalog() is None
        assert restarted.catalog(include_drafts=True)[0]["program_id"] == draft_id
        reply = await restarted.run(draft_id, parameters)
        if state == "ready":
            assert (reply["status"], reply["effect"], desktop.writes) == ("succeeded", "confirmed", [92])
            assert target_path.read_text(encoding="utf-8") == "Second result"
            assert restarted.library.lookup(artifact.definition["id"]).source == artifact.source
            assert restarted.catalog()[0]["function"] == "replace_note_text"
        elif state == "numeric_selector":
            assert (reply["status"], reply["effect"]) == ("stopped", "none")
            assert "semantic element path" in reply["detail"]
            assert not desktop.lookups and not desktop.writes
            assert reply["fallback_allowed"] and restarted.catalog() is None
        elif state == "wrong_window":
            assert (reply["status"], reply["effect"], desktop.writes) == ("stopped", "none", [])
            assert reply["fallback_allowed"] and restarted.catalog() is None
        else:
            assert (reply["status"], reply["effect"], desktop.writes) == ("failed", "unknown", [92])
            assert not reply["fallback_allowed"] and restarted.catalog() is None

    asyncio.run(scenario())
