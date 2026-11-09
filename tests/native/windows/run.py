# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compile the complete Windows driver and run portable native mechanism tests.

On Windows the Framework compiler and references are discovered automatically.
The hosted App identity test uses its own hidden windows without input.
Other hosts can supply a Roslyn compiler, Framework references and a UIA COM
interop assembly exported on Windows. These tests replace OS ports; they do not
claim to validate a Windows desktop session. On Windows, --desktop-lease also
checks native admission and cleanup against the real session mutex without input.
"""

import argparse
import json
import os
import subprocess
from pathlib import Path

REFERENCES = (
    "mscorlib.dll",
    "System.dll",
    "System.Core.dll",
    "System.Web.Extensions.dll",
    "System.Drawing.dll",
    "System.Windows.Forms.dll",
    "WindowsBase.dll",
    "UIAutomationClient.dll",
    "UIAutomationTypes.dll",
)


def main():
    root = Path(__file__).resolve().parents[3]
    native = root / "os_adapter/windows"
    framework = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path, default=framework / "csc.exe")
    parser.add_argument("--framework", type=Path, default=framework)
    parser.add_argument("--interop", type=Path, default=native / ".build/Interop.NativeUIA.dll")
    parser.add_argument("--dotnet", type=Path)
    parser.add_argument("--runtime-version", default="8.0.0")
    parser.add_argument("--desktop-lease", action="store_true", help="Exercise the real Windows session mutex")
    parser.add_argument("--repeat", type=int, default=1, help="Repeat each built test executable without rebuilding")
    parser.add_argument("--output", type=Path, default=root / ".artifacts/windows-unified-write-tests")
    args = parser.parse_args()
    if args.desktop_lease and os.name != "nt":
        parser.error("The desktop lease test requires Windows")
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    refs = []
    for name in REFERENCES:
        path = args.framework / name
        if not path.exists():
            path = args.framework / "WPF" / name
        if not path.exists():
            parser.error(f"Missing Framework reference: {name}")
        refs.append(f"/reference:{path}")
    if not args.compiler.is_file() or not args.interop.is_file():
        parser.error("The compiler and exported UIA interop assembly must exist")
    compiler = ([str(args.dotnet)] if args.dotnet else []) + [str(args.compiler)]
    sources = sorted((native / "Sources").rglob("*.cs"))
    common = ["/nologo", "/nostdlib+", "/optimize+", "/warnaserror+", *refs, f"/reference:{args.interop}"]

    def compile_target(name, options, extra_sources=()):
        output = args.output / f"{name}.exe"
        response = args.output / f"{name}.rsp"
        response.write_text(
            "\n".join(
                f'"{value}"'
                for value in [*common, f"/out:{output}", *options, *map(str, sources), *map(str, extra_sources)]
            ),
            encoding="utf-8",
        )
        subprocess.run([*compiler, "/noconfig", f"@{response}"], check=True)
        return output

    compile_target("computer-use-driver", ["/target:exe", "/platform:x64"])
    control_groups = (
        "UnifiedWriteTests", "NativeScrollTests", "NativeExplicitActionTests", "NativeCapturedPointerTests",
        "NativeValueReplacementTests", "NativeMultilineReplacementTests", "NativeGlobalInputTests",
        "NativeKeyboardModalPreparationTests",
    )
    groups = [("Control", group) for group in control_groups] + [
        ("Read", "NativeObservationPreparationTests"), ("Read", "NativeCaptureRecoveryTests"),
        ("Read", "NativeObservationWireBudgetTests"),
    ]
    if os.name == "nt" and not args.dotnet:
        groups.append(("Read", "NativeHostedApplicationIdentityTests"))
    if args.desktop_lease:
        groups.append(("Runtime", "NativeResourceLeaseTests"))
    for folder, group in groups:
        tests = compile_target(
            group,
            ["/target:exe", "/platform:anycpu", f"/main:ComputerUse.Windows.{group}"],
            [Path(__file__).parent / folder / f"{group}.cs"],
        )
        if args.dotnet:
            tests.with_suffix(".runtimeconfig.json").write_text(
                json.dumps(
                    dict(
                        runtimeOptions=dict(
                            tfm=f"net{args.runtime_version.rsplit('.', 1)[0]}",
                            framework=dict(name="Microsoft.NETCore.App", version=args.runtime_version),
                        )
                    )
                ),
                encoding="utf-8",
            )
        for _ in range(args.repeat):
            subprocess.run(([str(args.dotnet)] if args.dotnet else []) + [str(tests)], check=True)


if __name__ == "__main__":
    main()
