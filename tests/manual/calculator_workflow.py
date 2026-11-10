# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Opt-in macOS SDK acceptance: read, find, click, verify, then clear.

Run with the same desktop mode and permissions before and after the refactor.
Only run on a disposable calculator session: this clears its current expression.
"""

import asyncio
import json
import re
import sys
from pathlib import Path
from time import monotonic

from computer_use import Computer


async def main():
    computer = Computer()
    started = monotonic()
    try:
        apps = await computer.list_apps()
        candidates = [app for app in apps if app.display_name.split(" — ", 1)[0] in ("Calculator", "计算器")]
        if len(candidates) != 1:
            raise RuntimeError(f"Expected one calculator instance, found {len(candidates)}")
        app = computer.app(candidates[0].id)
        await app.read(view="tree")
        window = app.window(0)
        initial = str(await window.read(view="tree"))
        clear = next((name for name in ("全部清除", "清除", "All Clear", "Clear") if name in initial), None)
        if clear is None:
            raise RuntimeError("No observed clear control")
        await (await window.element(clear)).click()
        before = str(await window.read(view="tree"))
        if not re.search(r"(?:文本|text) [\u200e\u200f]*0(?:\n|$)", before):
            raise AssertionError("Calculator did not clear to zero")
        seven = await window.element("7")
        result = await seven.click()
        after = str(await window.read(view="tree"))
        if not re.search(r"(?:文本|text) [\u200e\u200f]*7(?:\n|$)", after):
            raise AssertionError("Calculator did not display seven")
        clear = next(name for name in ("全部清除", "清除", "All Clear", "Clear") if name in after)
        await (await window.element(clear)).click()
        final = str(await window.read(view="tree"))
        if not re.search(r"(?:文本|text) [\u200e\u200f]*0(?:\n|$)", final):
            raise AssertionError("Calculator did not return to zero")
        report = dict(
            python=sys.version,
            receipt=dict(result),
            seconds=round(monotonic() - started, 3),
            before=before,
            after=after,
            final=final,
            source=str(Path(__file__).resolve().parents[2]),
            driver=computer._controller.transport.driver,
        )
        Path(sys.argv[1]).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        sys.stdout.write("Calculator workflow passed: 0 → 7 → 0\n")
    finally:
        computer._controller.transport.close()


if __name__ == "__main__":
    asyncio.run(main())
