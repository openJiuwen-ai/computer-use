"""Build and run native mechanism tests, without sending desktop input.

Run with ``uv run python tests/run_macos.py``. ``--all-python`` also runs the
complete Python suite with the freshly compiled native wire probes available.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from computer_use_native_tools.pointer import generatePointerAssets

ROOT = Path(__file__).resolve().parents[1]
NAMES = """Failure AgentPointerGeometry AgentPointerVisualState AgentPointerAppearance
DesktopGeometry AccessibilityActivation
ApplicationCatalog NativeInstallation ApplicationInstances WindowSelection
WindowServerInfo NativeWindowParents AppSelection NativeWindowBoundary NativeWindowCandidateQuery NativeWindowDeclarationCapture ElementReferences ElementIdentity
ObservedElements ObservedPath NativeContainment NativeReceiverEvidence NativeCellEditorEvidence NativeCurrentReceiverEvidence NativeKeyboardWindowEvidence
NativeReadiness NativeInputWindowHit NativeElementHitSelection KeyboardEventRoute RemoteApplicationKind PasteboardTransaction NativeMetrics
NativeAction NativeProviderTextValueEvidence NativeTextSelection NativeTextMarkerSelection NativeKeyboardSelection NativeKeyboardTransportPolicy NativeKeyboardCleanup PairedInput InputPacing NativeScrollAction
SharedDesktopPolicy ObservationBudget RequestLifetime DesktopLease
RequestLedger NativeOperation NativeProcessIdentity AXRead AXIdentity AXFactEncoding AXAttributeReader AXAttributeProfile NativeObservationEncoding AXNativeValueEncoding
NativeRelationPages AXRelationReader LocalMenuAttachment NativeTarget CapturedImage""".split()
# These mechanisms call the actual parent pipelines and need all non-main sources.
FULL_SOURCE_NAMES = (
    "NativeCellEditorPreparation",
    "ApplicationRegistration",
    "NativeMenuSurfaces",
    "NativeObjectIntent",
    "NativePointer",
    "NativeFocusNotification",
    "NativeSelectionBasis",
    "DriverDispatch",
    "NativeExecutionContract",
    "NativePipelineContract",
    "NativeClick",
    "ForegroundActivation",
    "NativeKeyboardBoundary",
)
EXTRA = {
    "NativeCellEditorEvidence": ["NativeCellEditorLifetime"],
    "NativeWindowDeclarationCapture": ["NativeWindowCandidateQuery", "AXRead", "NativeProcessIdentity"],
    "NativeKeyboardTransportPolicy": [
        "NativeKeyboardInterpreterEvidence",
        "NativeKeyboardReadinessGate",
        "PairedInput",
    ],
    "AccessibilityActivation": ["NativeProcessIdentity"],
    "ObservationBudget": ["Protocol", "NativeMetrics"],
    "AgentPointerGeometry": ["AgentPointerAssets", "AgentPointerAppearance"],
    "AgentPointerVisualState": ["AgentPointerAssets"],
    "AgentPointerAppearance": ["AgentPointerAssets"],
    "NativeOperation": [
        "RequestLedger",
        "Protocol",
        "NativeMetrics",
        "ObservationBudget",
        "NativeTarget",
        "NativeIntent",
    ],
    "NativeTarget": ["Protocol", "NativeMetrics", "ObservationBudget"],
    "CapturedImage": ["Protocol", "NativeMetrics", "ObservationBudget"],
    "NativeWindowBoundary": ["AppSelection"],
    "AXRelationReader": ["NativeRelationPages", "AXFactEncoding"],
    "AXNativeValueEncoding": ["NativeObservationEncoding"],
    "NativeTextSelection": ["AXRead"],
    "NativeTextMarkerSelection": ["AXRead"],
    "NativeKeyboardSelection": ["AXRead", "NativeTextSelection"],
    "LocalMenuAttachment": ["ObservedPath"],
    "AXIdentity": ["AXRead", "ElementIdentity"],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-python", action="store_true")
    parser.add_argument(
        "--native-only", action="store_true", help="Run only native mechanism binaries and source architecture checks"
    )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=NAMES + list(FULL_SOURCE_NAMES),
        help="Run selected native mechanism groups; requires --native-only",
    )
    args = parser.parse_args()
    if args.only and not args.native_only:
        parser.error("--only requires --native-only")
    if sys.platform != "darwin":
        parser.error("Native mechanism tests require macOS")
    print(generatePointerAssets(check=True))
    output = ROOT / ".artifacts/native-tests"
    output.mkdir(parents=True, exist_ok=True)
    native = ROOT / "os_adapter/macos"
    native_tests = ROOT / "tests/native/macos"
    sources = sorted((native / "Sources").rglob("*.swift"))
    full_sources = [file for file in sources if file.name != "main.swift"]
    env = dict(os.environ)

    def source(name):
        matches = [file for file in sources if file.name == name + ".swift"]
        if len(matches) != 1:
            raise ValueError(f"Expected one Swift source for {name}; found {len(matches)}")
        return matches[0]

    def run(*command):
        subprocess.run(list(map(str, command)), cwd=ROOT, env=env, check=True)

    def run_architecture():
        for name in ("test_source_architecture.py", "test_keyboard_boundary_projection.py"):
            architecture = native_tests / "Runtime" / name
            if architecture.exists():
                run(sys.executable, str(architecture))

    def compile_test(name, inputs):
        print(f"Building {name}Tests", flush=True)
        binary = output / (name + "Tests")
        test_sources = list(native_tests.rglob(name + "Tests.swift"))
        if len(test_sources) != 1:
            raise ValueError(f"Expected one Swift test source for {name}; found {len(test_sources)}")
        run("swiftc", "-swift-version", "6", "-parse-as-library", *inputs, test_sources[0], "-o", binary)
        return binary

    for name in args.only or NAMES:
        if name in FULL_SOURCE_NAMES:
            inputs = full_sources
        else:
            inputs = [] if name == "Failure" else [source("Failure")]
            inputs += [source(dep) for dep in EXTRA.get(name, [])] + [source(name)]
        run(compile_test(name, inputs))

    if args.only:
        run_architecture()
        return

    common = ["Failure", "Protocol", "NativeMetrics", "ObservationBudget", "NativeTarget"]
    for name, deps, fixtures, variable in (
        ("NativeInspectQuery", [], ["native-inspect-queries.json"], "QUERY"),
        ("NativeUnitCommand", ["NativeIntent"], ["native-unit-commands.json", "native-begin-requests.json"], "COMMAND"),
        ("NativeCaptureRequest", [], ["native-capture-requests.json"], "CAPTURE"),
    ):
        binary = compile_test(name, [source(dep) for dep in common + deps + [name]])
        run(binary, *(ROOT / "tests/fixtures" / file for file in fixtures))
        env[f"COMPUTER_USE_NATIVE_{variable}_PROBE"] = str(binary)

    for name in FULL_SOURCE_NAMES:
        binary = compile_test(name, full_sources)
        run(binary)
        if name in ("NativeObjectIntent", "NativePointer"):
            variable = "INPUT" if name == "NativeObjectIntent" else "POINTER"
            env[f"COMPUTER_USE_NATIVE_{variable}_PROBE"] = str(binary)

    if args.native_only:
        run_architecture()
        return

    patterns = (
        ["test_*.py"]
        if args.all_python
        else [
            "test_native_queries.py",
            "test_native_commands.py",
            "test_native_desktop*.py",
            "test_native_transport.py",
            "test_native_pointer.py",
        ]
    )
    for pattern in patterns:
        run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", pattern, "-v")


if __name__ == "__main__":
    main()
