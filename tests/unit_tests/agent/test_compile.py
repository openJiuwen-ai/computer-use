# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise published Compile snapshots through the normal Agent and MCP entry points."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openjiuwen.core.foundation.tool.schema import ToolOutput

from computer_use_agent import AgentConfig, ComputerUseToolkit, create_agent
from computer_use_agent.compile import calc
from computer_use_agent.compile.library import Library
from computer_use_agent.compile.worker import _run, make_fixture

from .conftest import answer, tool_call

openpyxl = pytest.importorskip("openpyxl")


def _parameters(path, *, sheet="Samples", cell_range="B2:B3", decimals=3):
    return {"document": str(path), "sheet": sheet, "range": cell_range, "decimals": decimals}


def _report(artifact, parameters):
    cases = []
    for name, args in (
        ("positive", _parameters(artifact.path / "a.xlsx")),
        ("different_parameters", _parameters(artifact.path / "b.xlsx", sheet="Other", cell_range="D3", decimals=2)),
    ):
        scope, expectation = artifact.expectation(args)
        cases.append(
            {
                "case": name,
                "status": "succeeded",
                "effect": "confirmed",
                "parameters": args,
                "execution_id": name,
                "acceptance": {
                    "condition": calc.ACCEPTANCE,
                    "scope": scope,
                    "value": "TRUE",
                    "evidence": {"execution_id": name, "expected_sha256": expectation},
                },
            }
        )
    cases.append(
        {
            "case": "missing_sheet",
            "status": "refused",
            "effect": "none",
            "unchanged": True,
            "parameters": _parameters(artifact.path / "c.xlsx", sheet="Absent"),
        }
    )
    return {
        "definition": artifact.definition,
        "source": {"status": "succeeded", "parameters": parameters},
        "cases": cases,
    }


def _compiled_call(parameters):
    message = tool_call("")
    function = message["tool_calls"][0]["function"]
    function.update(
        name="run_compiled",
        arguments=json.dumps({"program_id": calc.ID, "parameters": parameters, "reply_language": "en"}),
    )
    return message


def test_compile_is_explicit_and_reuses_a_published_snapshot_in_one_mcp_call(model_server, tmp_path):
    path = tmp_path / "source.xlsx"
    make_fixture(path, "Samples", ("B2", "B3"))
    workbook = openpyxl.load_workbook(path)
    for address in ("B2", "B3"):
        workbook["Samples"][address].number_format = "0.000"
    workbook.save(path)
    workbook.close()
    parameters = _parameters(path)
    server = model_server(
        [
            tool_call(f"print({str(path)!r}, 'Samples B2:B3 verified')"),
            answer("Saved the workbook"),
            _compiled_call(parameters),
            _compiled_call(parameters),
        ]
    )
    events = []
    config = AgentConfig(workspace=str(tmp_path), compile_dir=str(tmp_path / "library"), check_desktop=False)

    async def scenario():
        async with create_agent(
            server.model(), config=config, on_event=lambda kind, data: events.append((kind, data))
        ) as agent:
            query = {"query": f"Save 3 decimals in Samples B2:B3 of {path}", "conversation_id": "source"}
            await agent.invoke(query)
            source_id = agent.last_task_id
            assert source_id
            compiler = agent.toolkit.compiler
            assert compiler.catalog() is None
            assert not (Path(config.compile_dir) / "drafts").exists()
            runner = compiler._run_code

            async def run_code(title, code):
                if not title.startswith("Validate"):
                    return await runner(title, code)
                draft = next((Path(config.compile_dir) / "drafts").iterdir())
                from computer_use_agent.compile.artifact import Artifact

                return ToolOutput(
                    success=True,
                    data={
                        "mcp_result": {
                            "structuredContent": _report(Artifact.read(draft), parameters),
                        }
                    },
                )

            compiler._run_code = run_code
            model = AsyncMock(return_value=SimpleNamespace(content=json.dumps(parameters)))
            agent._compile_model = SimpleNamespace(invoke=model)
            with pytest.raises(ValueError, match="No completed"):
                await agent.compile_task(conversation_id="another-conversation")
            published = await agent.compile_task(source_id)
            assert published["id"] == calc.ID and published["task_id"] == source_id
            assert str(path) in model.await_args.args[0][1]["content"]

        async with create_agent(
            server.model(), config=config, on_event=lambda kind, data: events.append((kind, data))
        ) as agent:
            events.clear()
            reply = await agent.invoke(
                {"query": f"Set 3 decimals in Samples B2:B3 of {path}", "conversation_id": "reuse"}
            )
            assert reply["result_type"] == "answer"
            assert "Samples!B2:B3 displays 3 decimal places" in reply["output"]
            calls = [data["name"] for kind, data in events if kind == "tool_call"]
            assert calls == ["run_compiled", "computer_use"]
            event = next(data for kind, data in events if kind == "compile_execution")
            assert event["status"] == "succeeded" and event["acceptance"]["value"] == "TRUE"
            assert event["definition"]["version"] == published["version"]

            events.clear()
            chunks = [
                chunk
                async for chunk in agent.stream(
                    {"query": f"Set 3 decimals in Samples B2:B3 of {path}", "conversation_id": "stream-reuse"}
                )
            ]
            assert any(
                chunk.type == "answer" and "displays 3 decimal places" in chunk.payload["output"] for chunk in chunks
            )
            assert [data["name"] for kind, data in events if kind == "tool_call"] == ["run_compiled", "computer_use"]
        offers = [
            any(tool["function"]["name"] == "run_compiled" for tool in request["tools"])
            for request in server.requests
            if request.get("tools")
        ]
        assert offers == [False, False, True, True]

    asyncio.run(scenario())


def test_publication_rejects_failed_case_and_tampered_program(tmp_path):
    library = Library(tmp_path)
    parameters = _parameters(tmp_path / "original.xlsx")
    draft = library.draft("source", parameters)
    report = _report(draft, parameters)
    report["cases"][-1]["unchanged"] = False
    with pytest.raises(ValueError, match="Missing-sheet"):
        library.publish(draft, report)
    assert library.lookup() is None
    assert (draft.path / "check.json").is_file()
    report["cases"][-1]["unchanged"] = True
    artifact = library.publish(draft, report)
    assert library.lookup().definition == artifact.definition
    (artifact.path / "program.py").write_text("raise AssertionError", encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        library.lookup()


def test_missing_sheet_stops_before_desktop_and_saved_readback_detects_unrelated_changes(tmp_path):
    path = tmp_path / "source.xlsx"
    make_fixture(path, "Samples", ("B2", "B3"))
    params = _parameters(path, sheet="Absent")
    draft = Library(tmp_path / "library").draft("source", params)
    original = path.read_bytes()
    result = asyncio.run(_run(None, draft, params, development=True))
    assert result["status"] == "refused" and result["effect"] == "none"
    assert result["steps"] == () and path.read_bytes() == original

    before = calc.snapshot(path)
    book = openpyxl.load_workbook(path)
    for cell in ("B2", "B3"):
        book["Samples"][cell].number_format = "0.000"
    book["Control"]["A1"] = "changed"
    book.save(path)
    book.close()
    valid, detail = calc.verify(before, calc.snapshot(path), "Samples", {"B2", "B3"}, "0.000")
    assert not valid and "Control!A1" in detail


@pytest.mark.parametrize(
    "status,effect,can_fallback",
    [
        ("refused", "none", True),
        ("stopped", "unknown", False),
        ("failed", "unknown", False),
    ],
)
def test_fallback_respects_the_whole_program_effect(tmp_path, status, effect, can_fallback):
    async def scenario():
        config = AgentConfig(compile_dir=str(tmp_path), check_desktop=False)
        async with ComputerUseToolkit(config) as toolkit:
            compiler = toolkit.compiler
            parameters = _parameters(tmp_path / "original.xlsx")
            draft = compiler.library.draft("source", parameters)
            published = compiler.library.publish(draft, _report(draft, parameters))
            compiler._run_code = AsyncMock(
                return_value=ToolOutput(
                    success=True,
                    data={
                        "mcp_result": {
                            "structuredContent": {
                                "definition": published.definition,
                                "parameters": parameters,
                                "status": status,
                                "effect": effect,
                                "detail": "Test outcome",
                            }
                        },
                    },
                )
            )
            result = await toolkit.run_program(calc.ID, parameters)
            assert result["fallback_allowed"] is can_fallback
            assert compiler._run_code.await_count == 1

    asyncio.run(scenario())


def test_structured_mcp_result_is_scoped_to_the_current_script():
    async def scenario():
        async with ComputerUseToolkit(AgentConfig(check_desktop=False)) as toolkit:
            report = await toolkit.call("computer_use", {"code": "report_result({'status': 'verified'})"})
            assert report.data["mcp_result"]["structuredContent"] == {"status": "verified"}
            ordinary = await toolkit.call("computer_use", {"code": "print('regular script')"})
            assert "structuredContent" not in ordinary.data["mcp_result"]

    asyncio.run(scenario())
