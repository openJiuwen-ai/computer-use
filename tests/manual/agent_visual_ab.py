# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run a small baseline/treatment comparison for OCR visual references.

Requires an Anthropic-compatible model endpoint and a built Windows driver:

  set MODEL_PROVIDER=Anthropic
  set API_BASE=http://127.0.0.1:8080/
  set API_KEY=...
  set MODEL_NAME=...
  python tests/manual/agent_visual_ab.py

The fixture is restarted for each case. The treatment must use VisualRef; the
baseline is told to compute coordinates from OCR rectangles and not use it.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig

from computer_use_agent import AgentConfig, ComputerUseAgent, McpConfig, RoutingConfig

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TITLE = "CUA Visual Fixture"
LOG = Path(tempfile.gettempdir()) / "cua_visual_fixture.log"


def _config(workspace: Path) -> AgentConfig:
    return AgentConfig(
        max_iterations=12,
        context_window=200_000,
        workspace=str(workspace),
        routing=RoutingConfig(enabled=False),
        mcp=McpConfig(
            command=sys.executable,
            args=("-m", "computer_use_mcp"),
            env={"PYTHONPATH": os.environ.get("PYTHONPATH", "")},
        ),
    )


async def _run_case(name: str, prompt: str) -> dict:
    workspace = Path(tempfile.mkdtemp(prefix=f"cua-ab-{name}-"))
    fixture = subprocess.Popen(
        [sys.executable, "tests/manual/visual_fixture.py"],
        cwd=Path(__file__).resolve().parents[2],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    LOG.unlink(missing_ok=True)
    await asyncio.sleep(3)

    calls = []
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider=os.environ.get("MODEL_PROVIDER", "Anthropic"),
            api_base=os.environ["API_BASE"],
            api_key=os.environ["API_KEY"],
            timeout=300,
        ),
        model_config=ModelRequestConfig(
            model=os.environ["MODEL_NAME"],
            max_tokens=4096,
        ),
    )
    agent = ComputerUseAgent(
        model,
        config=_config(workspace),
        on_event=lambda kind, data: calls.append((kind, data))
        if kind in ("tool_call", "tool_result")
        else None,
    )
    started = time.monotonic()
    error = None
    answer = None
    try:
        answer = await asyncio.wait_for(
            agent.invoke({"query": prompt, "conversation_id": f"ab-{name}"}),
            timeout=600,
        )
    except Exception as exc:  # noqa: BLE001 - report the comparison result
        error = f"{type(exc).__name__}: {exc}"
    finally:
        elapsed = time.monotonic() - started
        await agent.close()
        fixture.terminate()
        try:
            fixture.wait(timeout=5)
        except subprocess.TimeoutExpired:
            fixture.kill()

    tool_calls = [data for kind, data in calls if kind == "tool_call"]
    log_lines = LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []
    return {
        "case": name,
        "seconds": round(elapsed, 2),
        "tool_calls": len(tool_calls),
        "tools": [item.get("name") for item in tool_calls],
        "click_log": [line for line in log_lines if "name=" in line],
        "error": error,
        "answer": str(answer)[:2000] if answer is not None else None,
    }


TREATMENT = """
任务：打开标题为 "CUA Visual Fixture" 的窗口，点击其中的 "Export" 文本，并确认窗口标题变成包含 clicks=1 且 last=Export。

请严格按以下步骤执行，不要调用 dir()、inspect 或探索 API：
1. 先执行 `from computer_use import VisualRef`。
2. 执行 `app = computer.app(window_title="CUA Visual Fixture")`。
3. 执行 `obs = app.read(view="image", visual="always")`。
4. 执行 `print([(e.ref, e.label) for e in obs.visual.elements])`。
5. 找到 label 为 "Export" 的元素，执行 `app.element(VisualRef(ref)).click()`。
6. 执行 `after = app.read(view="image", visual="never")`，并打印 `after.text`。
7. 报告 after.text 中的窗口标题和使用的 VisualRef 编号。

只点击一次 Export，不要点击其他元素。
"""

BASELINE = """
任务：打开标题为 "CUA Visual Fixture" 的窗口，点击其中的 "Export" 文本，并确认窗口标题变成包含 clicks=1 且 last=Export。

请严格按以下步骤执行：
1. 执行 `app = computer.app(window_title="CUA Visual Fixture")`。
2. 执行 `obs = app.read(view="both")` 并打印 `obs.text`。
3. 从 OCR 文本中找到 "Export" 的矩形，计算矩形中心坐标。
4. 禁止使用 VisualRef；执行 `p = app.point((x, y), window=0)`，再执行 `app.click(at=p)`。
5. 再次读取窗口并打印标题。
6. 报告窗口标题和你使用的坐标。

只点击一次 Export，不要点击其他元素。
"""


async def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("treatment", "baseline", "both"), default="both")
    args = parser.parse_args(argv)
    required = ("API_BASE", "API_KEY", "MODEL_NAME")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        sys.stderr.write(f"Missing environment variables: {', '.join(missing)}\n")
        return 2
    selected = (
        (("treatment", TREATMENT), ("baseline", BASELINE))
        if args.case == "both"
        else ((args.case, TREATMENT if args.case == "treatment" else BASELINE),)
    )
    results = []
    for name, prompt in selected:
        result = await _run_case(name, prompt)
        results.append(result)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.write("\nSUMMARY\n")
    for result in results:
        clicked = any("name=Export" in line for line in result["click_log"])
        answer = result["answer"] or ""
        success = clicked and "clicks=1" in answer
        sys.stdout.write(
            f"{result['case']}: seconds={result['seconds']} tools={result['tool_calls']} "
            f"clicked_export={clicked} success={success} error={result['error']}\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
