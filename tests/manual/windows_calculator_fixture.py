# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Launch and independently score a disposable Windows Calculator process."""

import csv
import json
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path


def calculator_pids() -> list[str]:
    listed = subprocess.check_output(
        ["tasklist.exe", "/FI", "IMAGENAME eq CalculatorApp.exe", "/FO", "CSV", "/NH"],
        encoding="mbcs", timeout=5,
    )
    return [row[1] for row in csv.reader(listed.splitlines()) if row and row[0].casefold() == "calculatorapp.exe"]


def calculator_display(oracle: Path, pid: str) -> dict:
    result = subprocess.run(
        [str(oracle), pid], capture_output=True, timeout=12, encoding="utf-8", check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return json.loads(result.stdout) if result.returncode == 0 else {"oracle_exit": result.returncode}


def calculator_operands(nonce: str) -> tuple[int, int]:
    return 21 + int(nonce[:2], 16) % 37, 12 + int(nonce[2:4], 16) % 31


@contextmanager
def calculator_fixture(directory: Path):
    if calculator_pids():
        raise RuntimeError("Calculator acceptance requires no pre-existing CalculatorApp process")
    framework = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319"
    oracle = directory / "CalculatorOracle.exe"
    subprocess.run(
        [
            str(framework / "csc.exe"), "/nologo", "/target:exe", "/platform:x64", "/warnaserror+",
            "/reference:System.Web.Extensions.dll",
            *(f"/reference:{framework / 'WPF' / name}" for name in
              ("UIAutomationClient.dll", "UIAutomationTypes.dll", "WindowsBase.dll")),
            f"/out:{oracle}", str(Path(__file__).with_name("windows_calculator_oracle.cs")),
        ],
        check=True,
    )
    try:
        subprocess.run(["calc.exe"], check=True, timeout=10)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            pids = calculator_pids()
            if len(pids) > 1:
                raise RuntimeError("The disposable launch produced multiple CalculatorApp processes")
            if pids:
                yield oracle, pids[0]
                return
            time.sleep(0.2)
        raise RuntimeError("The disposable CalculatorApp process did not start")
    finally:
        # The exclusive fixture preflight established that no process existed.
        owned = set(calculator_pids())
        for pid in owned:
            subprocess.run(
                ["taskkill.exe", "/F", "/PID", pid],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=5,
            )
        if owned:
            deadline = time.monotonic() + 4
            while owned.intersection(calculator_pids()):
                if time.monotonic() >= deadline:
                    raise RuntimeError("The disposable CalculatorApp process did not exit")
                time.sleep(0.1)
