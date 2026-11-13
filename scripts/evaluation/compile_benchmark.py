# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Paired desktop benchmark for the Agent's first published Compile profile."""

import argparse
import asyncio
import json
import math
import statistics
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

from computer_use_native_tools.console import report
from openjiuwen.core.foundation.llm.call_scope import get_current_llm_call_id
from openjiuwen.core.runner.callback.events import LLMCallEvents
from openjiuwen.core.runner.runner import Runner

from computer_use_agent import create_agent
from computer_use_agent.cli.config import build, load
from computer_use_agent.compile import calc
from computer_use_agent.compile.worker import make_fixture

from .provenance import runtime_snapshot

_COUNTERS = ("input", "output", "cache_hit")
_measurement = ContextVar("compile_benchmark_measurement", default=None)


@contextmanager
def _observe_calls(task_id):
    rows = {}

    async def started(**kwargs):
        call_id = get_current_llm_call_id()
        if _measurement.get() is rows and call_id:
            rows[call_id] = {
                "task_id": task_id,
                "call_id": call_id,
                "model": kwargs.get("model"),
                "status": "incomplete",
                "tokens": dict.fromkeys(_COUNTERS),
            }

    async def complete(*, usage=None, result=None, model_name=None, error=None, **_):
        call_id = get_current_llm_call_id()
        if _measurement.get() is not rows or call_id not in rows:
            return
        metadata = usage if usage is not None else getattr(result, "usage_metadata", None)
        if hasattr(metadata, "model_dump"):
            metadata = metadata.model_dump(exclude_unset=True)
        if isinstance(metadata, dict):
            cache = metadata.get("cache_read_tokens")
            if cache is None:
                cache = (metadata.get("prompt_tokens_details") or {}).get("cached_tokens")
            for key, value in zip(
                _COUNTERS,
                (
                    metadata.get("input_tokens", metadata.get("prompt_tokens")),
                    metadata.get("output_tokens", metadata.get("completion_tokens")),
                    cache,
                ),
            ):
                if type(value) is int and value >= 0:
                    rows[call_id]["tokens"][key] = value
        rows[call_id].update(
            status="failed" if error is not None else "succeeded",
            model=model_name or rows[call_id]["model"],
        )

    callbacks = (
        (LLMCallEvents.LLM_INVOKE_INPUT, started),
        (LLMCallEvents.LLM_STREAM_INPUT, started),
        (LLMCallEvents.LLM_OUTPUT, complete),
        (LLMCallEvents.LLM_INVOKE_OUTPUT, complete),
        (LLMCallEvents.LLM_STREAM_COMPLETED, complete),
        (LLMCallEvents.LLM_CALL_ERROR, complete),
    )
    token = _measurement.set(rows)
    framework = Runner.callback_framework
    try:
        for event, callback in callbacks:
            framework.register_sync(event, callback)
        yield rows
    finally:
        for event, callback in callbacks:
            framework.unregister_sync(event, callback)
        _measurement.reset(token)


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _model_usage(rows):
    counters = {}
    for key in _COUNTERS:
        values = [row["tokens"][key] for row in rows]
        counters[key] = sum(values) if all(value is not None for value in values) else None
    counters["total"] = (
        counters["input"] + counters["output"]
        if counters["input"] is not None and counters["output"] is not None
        else None
    )
    return {"model_calls": len(rows), **counters}


async def _measure(output, task_id, action):
    output.mkdir(mode=0o700)
    started = monotonic()
    with _observe_calls(task_id) as calls:
        try:
            result = await action()
            error = None
        except Exception as failure:  # noqa: BLE001 - persist failed samples in the paired benchmark
            result, error = None, f"{type(failure).__name__}: {failure}"
    elapsed = monotonic() - started
    rows = list(calls.values())
    (output / "model_calls.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    return {"elapsed_s": elapsed, **_model_usage(rows), "result": result, "error": error}


async def _setup(agent, function, path):
    reply = await agent.toolkit.connection.call(
        "computer_use",
        {
            "title": f"Benchmark fixture {function}",
            "code": (
                f"from computer_use_agent.compile.worker import {function}\n"
                f"from pathlib import Path\n{function}(computer, Path({str(path)!r}))"
            ),
        },
    )
    if reply.isError:
        raise RuntimeError(next((block.text for block in reversed(reply.content) if block.type == "text"), ""))


async def _trial(agent, events, output, parameters, prompt, arm):
    path, cells = calc.workbook_target(parameters)
    before = calc.snapshot(path)
    await _setup(agent, "open_fixture", path)
    try:
        events.clear()
        query = {"query": prompt, "conversation_id": uuid4().hex}
        row = await _measure(output, query["conversation_id"], lambda: agent.invoke(query))
        try:
            correct, detail = calc.verify(
                before, calc.snapshot(path), parameters["sheet"], cells, calc.number_format(parameters)
            )
        except Exception as error:  # noqa: BLE001 - retain unverified trial and counters
            correct, detail = False, str(error)
        executions = [data for kind, data in events if kind == "compile_execution"]
        calls = [data for kind, data in events if kind == "tool_call" and data["name"] == "computer_use"]
        metadata = [
            data["result"].get("_meta", {}).get("computer-use", {})
            for kind, data in events
            if kind == "tool_result" and data["name"] == "computer_use"
        ]
        native = [meta.get("nativeCommandCount") for meta in metadata]
        regular_calls = sum(call["arguments"].get("title") != "Run compiled Calc formatting" for call in calls)
        row.update(
            arm=arm,
            correct=correct,
            agent_returned=(row["result"] or {}).get("result_type") == "answer",
            verification=detail,
            computer_use_calls=len(calls),
            native_commands=sum(native) if len(native) == len(calls) and all(n is not None for n in native) else None,
            compile_attempted=any(kind == "tool_call" and data["name"] == "run_compiled" for kind, data in events),
            compiled_status=executions[-1]["status"] if executions else None,
            fallback=bool(regular_calls and any(result.get("fallback_allowed") for result in executions)),
            follow_up_calls=regular_calls if executions and executions[-1]["status"] == "succeeded" else 0,
            desktop_logs=[meta.get("logPath") for meta in metadata if meta.get("logPath")],
        )
        _write_json(output / "result.json", row)
        return row
    finally:
        await _setup(agent, "close_fixture", path)


def _distribution(values):
    values = sorted(value for value in values if value is not None)
    return {
        "samples": len(values),
        "p50": statistics.median(values) if values else None,
        "p95": values[math.ceil(len(values) * 0.95) - 1] if values else None,
    }


def _summarize(pairs, compile_cost):
    summaries = {}
    for arm in ("baseline", "compiled"):
        rows = [pair[arm] for pair in pairs]
        summaries[arm] = {
            "correct": sum(row["correct"] for row in rows),
            "trials": len(rows),
            "compile_attempts": sum(row["compile_attempted"] for row in rows),
            "fallbacks": sum(row["fallback"] for row in rows),
            "follow_up_calls": sum(row["follow_up_calls"] for row in rows),
            **{
                key: _distribution(row[key] for row in rows)
                for key in ("elapsed_s", "model_calls", "total", "cache_hit", "computer_use_calls", "native_commands")
            },
        }
    eligible = [pair for pair in pairs if all(row["correct"] and row["agent_returned"] for row in pair.values())]
    deltas = {}
    for key in ("elapsed_s", "total"):
        available = [
            (pair["baseline"][key], pair["compiled"][key])
            for pair in eligible
            if pair["baseline"][key] is not None and pair["compiled"][key] is not None
        ]
        saved = _distribution(base - reused for base, reused in available)
        saved["fraction"] = _distribution((base - reused) / base for base, reused in available if base)
        cost, median = compile_cost[key], saved["p50"]
        saved["break_even_repeats"] = (
            math.ceil(cost / median) if median is not None and median > 0 and cost is not None else None
        )
        deltas[key] = saved
    return {**summaries, "successful_pairs": len(eligible), "paired_savings": deltas, "compile_cost": compile_cost}


async def benchmark(settings, output, pairs):
    output.mkdir(parents=True, mode=0o700)
    events = []

    def on_event(kind, data):
        events.append((kind, data))

    model, config = build(settings, workspace=output / "agent-workspace")
    config = replace(config, allow_global_input=False, compile_enabled=True, compile_dir=str(output / "library"))
    if config.mcp is not None:
        raise ValueError("This benchmark requires the default local macOS MCP host")
    _write_json(
        output / "run.json",
        {
            "model": settings["model"],
            "pairs": pairs,
            "runtime": runtime_snapshot(Path(__file__).resolve().parents[2]),
        },
    )
    with TemporaryDirectory(prefix="cua-compile-benchmark-") as directory:
        root = Path(directory).resolve()
        async with (
            create_agent(model, config=config, on_event=on_event) as enabled,
            create_agent(model, config=replace(config, compile_enabled=False), on_event=on_event) as baseline,
        ):
            await enabled.toolkit.check_desktop()
            await baseline.toolkit.check_desktop()
            source = root / f"compile-{root.name}-source.xlsx"
            parameters = {"document": str(source), "sheet": "Samples", "range": "B2:B3", "decimals": 3}
            make_fixture(source, "Samples", ("B2", "B3"))
            # Only the seed task gets workflow hints. Paired trials use the same
            # natural request, and seed task timing is excluded from the deltas.
            prompt = _prompt(parameters) + (
                "\n操作提示：直接使用电脑操作工具。查看窗口后，用 Name Box 的 combo box 元素"
                "执行 type_text(range, replace=True)，然后向窗口发送 Return。用 super+1 打开"
                " Format Cells，在 Numbers 页将 Format Code 设置为 0.000，确认后用 super+s 保存。"
            )
            original = await _trial(enabled, events, output / "source", parameters, prompt, "source")
            if not original["correct"] or enabled.last_task_id is None:
                raise RuntimeError("The baseline source task did not produce a verified saved result")
            cost = await _measure(output / "compile", enabled.last_task_id, enabled.compile_task)
            _write_json(output / "compile" / "result.json", cost)
            if cost["error"]:
                raise RuntimeError(cost["error"])

            results = []
            for index in range(pairs):
                path = root / f"compile-{root.name}-pair-{index + 1}.xlsx"
                parameters = {**parameters, "document": str(path), "decimals": 2 if index % 2 else 3}
                make_fixture(path, "Samples", ("B2", "B3"))
                golden = path.read_bytes()
                trial_order = ("baseline", "compiled") if index % 2 == 0 else ("compiled", "baseline")
                pair = {}
                for arm in trial_order:
                    path.write_bytes(golden)
                    agent = baseline if arm == "baseline" else enabled
                    pair[arm] = await _trial(
                        agent, events, output / f"pair-{index + 1:03d}-{arm}", parameters, _prompt(parameters), arm
                    )
                results.append(pair)
                summary = _summarize(results, cost)
                _write_json(output / "summary.json", summary)
                report(
                    f"Pair {index + 1}/{pairs}: baseline={pair['baseline']['correct']} "
                    f"compiled={pair['compiled']['correct']}"
                )
    return summary


def _prompt(parameters):
    return (
        f"请在 LibreOffice Calc 中，把已经打开的工作簿 {parameters['document']} 的 "
        f"{parameters['sheet']} 工作表 {parameters['range']} 设置为显示 {parameters['decimals']} 位小数。"
        "只改数字显示格式，保留单元格原值，并保存原文件。"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New directory for metrics and per-call evidence")
    parser.add_argument("--pairs", type=int, default=30)
    args = parser.parse_args()
    if args.pairs < 1:
        parser.error("--pairs must be positive")
    summary = asyncio.run(benchmark(load(args.config), args.output.resolve(), args.pairs))
    report(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
