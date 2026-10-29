# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compile and run the macOS query and click mechanism regressions."""

import argparse
import logging
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
GROUPS = (
    "NativeInspectQuery",
    "NativePopupChoiceBoundary",
    "NativePopupAXFacts",
    "NativeReplacementReceiver",
    "NativeWindowQueryTiming",
    "NativeReadyPhysicalClickPreference",
    "NativeInputWindowScope",
    "NativeVisualDirectory",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+", choices=GROUPS)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("These native mechanism tests require macOS.")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sources = sorted((ROOT / "os_adapter/macos/Sources").rglob("*.swift"))
    sources = [str(path) for path in sources if path.name != "main.swift"]
    output = ROOT / ".artifacts/native-query-tests"
    output.mkdir(parents=True, exist_ok=True)
    for name in args.only or GROUPS:
        tests = list((ROOT / "tests/native/macos").rglob(name + "Tests.swift"))
        if len(tests) != 1:
            raise ValueError(f"Expected one test source for {name}, got {len(tests)}")
        binary = output / (name + "Tests")
        logging.info("Building and running %s", name)
        subprocess.run(
            ["swiftc", "-swift-version", "6", "-parse-as-library", *sources, str(tests[0]), "-o", str(binary)],
            cwd=ROOT,
            check=True,
        )
        arguments = [str(ROOT / "tests/fixtures/native-inspect-queries.json")] if name == "NativeInspectQuery" else []
        subprocess.run([str(binary), *arguments], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
