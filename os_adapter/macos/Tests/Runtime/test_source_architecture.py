"""Static boundaries for the native directory contract; no UI or native input.

Run: uv run python os_adapter/macos/Tests/test_source_architecture.py
"""

import re
import unittest
from pathlib import Path

SOURCES = Path(__file__).resolve().parents[2] / "Sources"


class NativeDirectoryArchitectureTests(unittest.TestCase):
    def test_source_groups_and_unique_compilation_names(self):
        self.assertEqual(
            {p.name for p in SOURCES.iterdir() if p.is_dir()},
            {"Runtime", "Read", "Control", "Capture", "Objects", "Windowing", "Contracts", "Preview"},
        )
        names = [p.name for p in SOURCES.rglob("*.swift")]
        self.assertEqual(len(names), len(set(names)))
        for old in (
            "NativeQueryPipeline",
            "NativeQueryPasses",
            "NativeInputContextPipeline",
            "NativeWritePipeline",
            "NativeRoutePipeline",
            "NativeUnitBoundary",
            "NativeResourceScope",
        ):
            self.assertFalse((SOURCES / "Runtime" / (old + ".swift")).exists())

    def test_each_leaf_has_one_file_and_an_explicit_pipeline_registration(self):
        leaves = sorted(SOURCES.rglob("Passes/*.swift"))
        self.assertGreaterEqual(len(leaves), 42)
        registrations = set(SOURCES.rglob("*Pipeline.swift")) | set(SOURCES.rglob("*Scope.swift"))
        pipelines = "\n".join(p.read_text() for p in registrations)
        for path in leaves:
            with self.subTest(path=path.relative_to(SOURCES)):
                source = path.read_text()
                declarations = re.findall(r"\benum\s+(\w+Pass)\b", source)
                self.assertEqual(declarations, [path.stem + "Pass"])
                self.assertIn(declarations[0] + ".run(", pipelines)
                self.assertRegex(source, r"/// (PURE|QUERY|RESOURCE|PREPARE|DISPATCH)")

    def test_business_passes_have_no_full_executor_or_nested_pipeline(self):
        for path in SOURCES.rglob("Passes/*.swift"):
            with self.subTest(path=path.relative_to(SOURCES)):
                source = path.read_text()
                self.assertNotRegex(source, r"\b(?:NativeExecution|NativeDesktopState|Driver)\b")
                self.assertNotRegex(source, r"\b\w+Pipeline\.(?:run|collect|execute|perform|select)\(")
                own = path.stem + "Pass"
                foreign = set(re.findall(r"\b(\w+Pass)\.run\(", source)) - {own}
                self.assertFalse(foreign)

    def test_business_modules_do_not_extend_or_accept_executor(self):
        for folder in ("Read", "Control", "Capture", "Contracts"):
            for path in (SOURCES / folder).rglob("*.swift"):
                with self.subTest(path=path.relative_to(SOURCES)):
                    self.assertNotRegex(path.read_text(), r"\bNativeExecution\b")

    def test_contracts_only_expose_narrow_owner_views(self):
        contracts = "\n".join(p.read_text() for p in (SOURCES / "Contracts").glob("*.swift"))
        self.assertNotRegex(contracts, r"\b(?:DriverPipeline|ReadPipeline|ControlPipeline|CapturePipeline)\b")
        view = (SOURCES / "Contracts/NativeContractAccess.swift").read_text()
        self.assertIn("protocol NativeContractAccess", view)
        self.assertNotIn("[String: [", view)
        query = (
            (SOURCES / "Contracts/NativeObjectAccess.swift").read_text().split("/// Addressed control capabilities.")[0]
        )
        self.assertNotIn("executeNative", query)

    def test_operation_and_request_journals_have_original_separate_owners(self):
        owner = (SOURCES / "Runtime/NativeExecution.swift").read_text()
        operation = (SOURCES / "Runtime/NativeOperation.swift").read_text()
        driver = (SOURCES / "Runtime/Driver.swift").read_text()
        self.assertIn("var retained = [String: NativeOperation]()", owner)
        self.assertIn("var contracts = [String: [NativePrimitiveContract]]()", owner)
        self.assertIn("var selections = [String: NativeSelectionBasis]()", owner)
        self.assertIn("private let steps = RequestLedger()", operation)
        self.assertIn("private let ledger = RequestLedger()", driver)
        all_source = "\n".join(p.read_text() for p in SOURCES.rglob("*.swift"))
        self.assertEqual(all_source.count("= RequestLedger()"), 2)
        self.assertEqual(all_source.count("let registry = ApplicationElements()"), 1)

    def test_window_context_preparation_cannot_emit_content_mouse_input(self):
        # Window preparation is shared by click, keyboard and menus. A provider's
        # activation point is not permission to send a second content gesture.
        paths = list((SOURCES / "Control/Prepare/Focus").rglob("*.swift"))
        paths.append(SOURCES / "Control/ForegroundAssist.swift")
        paths.append(SOURCES / "Control/Focus/NativeDocumentContext.swift")
        paths.extend((SOURCES / "Control/Click/Passes").glob("*ActionDocument*.swift"))
        for path in paths:
            with self.subTest(path=path.relative_to(SOURCES)):
                self.assertNotRegex(
                    path.read_text(),
                    r"\bNativePointerEvents\b|CGEvent\(mouseEventSource:|BackgroundEvents\.post\("
                    r"|SLPSPostEventRecordTo|\b[Pp]ostRecord\b",
                )
        # The former foreground "make-key" helper hid raw mouse type 1/2
        # records in a nominal activation request. Forbid that transport too,
        # rather than only checking CGEvent's mouse constructor at leaf passes.
        background = (SOURCES / "Control/BackgroundEvents.swift").read_text()
        self.assertNotRegex(background, r"SLPSPostEventRecordTo|\b[Pp]ostRecord\b")
        selection = re.search(
            r"static func selectForegroundWindow\([^)]*\) throws \{(.*?)\n    \}", background, re.DOTALL
        )
        self.assertIsNotNone(selection)
        self.assertNotRegex(selection.group(1), r"\bNativePointerEvents\b|CGEvent\(|\.post\(|\.postToPid\(")

    def test_activation_helper_precedes_application_initialization(self):
        main = (SOURCES / "Runtime/main.swift").read_text()
        self.assertLess(
            main.index("ForegroundActivation.runIfRequested("), main.index("let app = NSApplication.shared")
        )
        activation = (SOURCES / "Control/ForegroundActivation.swift").read_text()
        code = re.sub(r"//[^\n]*", "", activation)
        self.assertNotRegex(
            code,
            r"NSApplication\.shared|NSWorkspace|\bDriver\(|\.open\(|openApplication|yieldActivation|CGEvent\(|\.post\(",
        )
        self.assertEqual(code.count(".activate(options: [])"), 1)
        self.assertNotIn("waitUntilExit", code)
        foreground = (SOURCES / "Control/ForegroundAssist.swift").read_text()
        self.assertNotIn("BackgroundEvents.selectForegroundWindow(", foreground)

    def test_native_guard_and_dispatch_remain_one_execute_admission(self):
        write = (SOURCES / "Control/Write/NativeWritePipeline.swift").read_text()
        self.assertLess(write.index("op.execute("), write.index("ResolveNativeTargetPass.run("))
        self.assertLess(write.index("CheckNativeWriteBoundaryPass.run("), write.index("NativeRoutePipeline.select("))
        action = (SOURCES / "Control/NativeActionUnit.swift").read_text()
        self.assertLess(action.index("try onDispatch()"), action.index("AXMessaging.performAction("))
        value = (SOURCES / "Control/NativeTextUnit.swift").read_text()
        self.assertLess(value.index("try onDispatch()"), value.index("AXMessaging.setAttributeValue("))
        resource = (SOURCES / "Runtime/Resources/NativeResourceScope.swift").read_text()
        self.assertLess(
            resource.index("await DesktopLease.acquire()"), resource.index("CheckNativeResourceBoundaryPass.run(")
        )
        self.assertRegex(
            resource,
            r"release:\s*\{\s*selection\.clear\(\)\s*;?\s*releaseNode\(\)"
            r"\s*;?\s*lease\?\.release\(\)\s*\}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
