# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build the disposable AppKit fixture (macOS Command Line Tools required).

python tests/manual/build_save_probe.py --output .artifacts/q4/SaveProbe.app
python tests/manual/save_shortcut_workflow.py --bundle .artifacts/q4/SaveProbe.app \
    --output .artifacts/q4/native-runs --route key --target responder --initial-focus foreground

Run the second command from the permission-authorized terminal. The harness
uses public SDK calls; the fixture only records native events and file output.
"""

import argparse
import plistlib
import subprocess
from pathlib import Path


def build(output: Path):
    output = output.resolve()
    binary = output / "Contents/MacOS/SaveProbe"
    binary.parent.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).with_name("fixtures") / "SaveProbe.swift"
    subprocess.run(
        ["swiftc", "-module-cache-path", str(output.parent / "swift-cache"), str(source), "-o", str(binary)],
        check=True,
    )
    (output / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "local.computer-use.q4-save-probe",
                "CFBundleName": "Q4 Save Probe",
                "CFBundleExecutable": "SaveProbe",
                "CFBundlePackageType": "APPL",
                "CFBundleVersion": "1",
                "CFBundleShortVersionString": "1.0",
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output)
