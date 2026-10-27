"""Opt-in TextEdit AXPress acceptance using isolated disposable documents.

Requires COMPUTER_USE_DESKTOP=shared. Run once with foreground assistance off
to verify rejection, then once with COMPUTER_USE_FOREGROUND_ASSIST=1 to verify
actual formatting. The oracle sets selection only during fixture setup.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import computer_use
from computer_use import Computer
from computer_use._process import resolve_executable
from computer_use.client.host import setDiagnosticHandler

ROOT = Path(__file__).resolve().parents[4]


async def run(options):
    folder = options.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    assert os.environ.get("COMPUTER_USE_DESKTOP", "shared") == "shared"
    assert Path(computer_use.__file__).resolve() == ROOT / "computer_use_engine/__init__.py"
    driver = options.driver.resolve()
    oracle_binary = folder / "font-oracle"
    subprocess.run(
        [resolve_executable("swiftc"), str(ROOT / "tests/fixtures/DocumentFontOracle.swift"), "-o", str(oracle_binary)],
        check=True,
    )
    paths = [folder / (name + "-" + folder.name + ".rtf") for name in ("ContextTargetA", "ContextOtherB")]
    for index, path in enumerate(paths):
        path.write_text(
            r"{\rtf1\ansi\deff0{\fonttbl{\f0 Helvetica;}}\f0\fs28 "
            + ("Target document A" if index == 0 else "Other document B")
            + " untouched.}"
        )
    previous = set(
        subprocess.run([resolve_executable("pgrep"), "-x", "TextEdit"], text=True, capture_output=True).stdout.split()
    )
    subprocess.run([resolve_executable("open"), "-n", "-a", "TextEdit", *map(str, paths)], check=True)
    await asyncio.sleep(1)
    launched = (
        set(subprocess.check_output([resolve_executable("pgrep"), "-x", "TextEdit"], text=True).split()) - previous
    )
    assert len(launched) == 1, launched
    pid = int(next(iter(launched)))
    computer = Computer()
    computer._controller.transport.driver = {"command": str(driver), "args": []}
    report = {
        "passed": False,
        "fixturePid": pid,
        "driver": str(driver),
        "sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "sdk": computer_use.__file__,
        "foregroundAssist": os.environ.get("COMPUTER_USE_FOREGROUND_ASSIST", "0"),
    }

    def diagnostic(event):
        with (folder / "commands.jsonl").open("a") as stream:
            stream.write(json.dumps(event, default=str, ensure_ascii=False) + "\n")

    def oracle(select=False):
        command = [str(oracle_binary), str(pid), *[p.name for p in paths]]
        return json.loads(subprocess.check_output(command + (["--select"] if select else []), text=True))

    setDiagnosticHandler(diagnostic)
    try:
        entries = await computer.listApps()
        apps = [computer.app(next(entry.id for entry in entries if path.name in entry.displayName)) for path in paths]
        report["before"] = oracle(select=True)
        assert report["before"]["focusedTitle"] == paths[1].name, report["before"]
        for index, app in enumerate(apps):
            observation = await app.read(view="both", full=True)
            (folder / f"before-{index}.txt").write_text(observation.text)
            (folder / f"before-{index}.png").write_bytes(observation.screenshot)
        observation = await apps[0].read(full=True)
        # TextEdit localizes the visible label. Resolve only the observed row.
        match = re.search(r"(?m)^\s*(\d+) (?:复选框 粗体|checkbox Bold)\b", observation.text)
        assert match, observation.text
        try:
            result = await (await apps[0].element(int(match[1]))).click()
            report["result"] = {"status": result.status, "detail": result.detail}
        except Exception as error:
            report["dispatchError"] = str(error)
        await asyncio.sleep(0.3)
        report["after"] = oracle()
        assert all(
            document["status"] == 0 for state in (report["before"], report["after"]) for document in state["documents"]
        )
        assert [d["text"] for d in report["before"]["documents"]] == [d["text"] for d in report["after"]["documents"]]
        for index, app in enumerate(apps):
            observation = await app.read(view="both", full=True)
            (folder / f"after-{index}.txt").write_text(observation.text)
            (folder / f"after-{index}.png").write_bytes(observation.screenshot)
        bold = [document["bold"] for document in report["after"]["documents"]]
        report["passed"] = (
            bold == [True, False] and "result" in report
            if report["foregroundAssist"] == "1"
            else bold == [False, False] and "dispatchError" in report
        )
    except Exception as error:
        report["error"] = repr(error)
    finally:
        computer._controller.transport.close()
        await computer._controller.transport.waitClosed()
        setDiagnosticHandler(None)
        os.kill(pid, 15)
        (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(json.dumps(report, ensure_ascii=False))
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(0 if asyncio.run(run(parser.parse_args())) else 1)
