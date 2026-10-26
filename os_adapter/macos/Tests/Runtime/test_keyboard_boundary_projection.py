"""Keyboard boundary contracts; complements the unchanged source architecture rules.

These are static wiring checks, not native AX/runtime performance evidence.
"""

import unittest
from pathlib import Path

SOURCES = Path(__file__).resolve().parents[2] / "Sources"


def read(relative):
    return (SOURCES / relative).read_text()


def section(text, start, end):
    opening = text.index(start)
    return text[opening : text.index(end, opening)]


class KeyboardBoundaryProjectionContracts(unittest.TestCase):
    def test_projection_keeps_input_validation_sources_and_strict_arrays(self):
        reader = read("Read/NativeApplicationReader.swift")
        self.assertIn("purpose: .inputValidation, projection: .keyboardDispatch", reader)
        self.assertIn("purpose == .inputValidation ? process.map", reader)
        self.assertIn("WindowGeometry.inputWindow(pid: app.processIdentifier, onWindowsRead: observer)", reader)
        self.assertIn("capture.take(currentProcess: NativeProcessIdentity.read(app.processIdentifier))", reader)
        self.assertEqual(reader.count("NativeWindowDeclarationCapture.completeElements("), 2)
        self.assertIn("for child in sheets", reader)
        self.assertIn("for child in try nestedSurfaces(element, recursive: recursive)", reader)

    def test_catalogue_keeps_enrichment_in_its_original_combination_points(self):
        reader = read("Read/NativeApplicationReader.swift")
        node = section(reader, "            let subrole =", "            // Sheets")
        order = ["kAXSubroleAttribute", "registry.reference(", "kAXTitleAttribute", "kAXModalAttribute"]
        self.assertEqual([node.index(item) for item in order], sorted(node.index(item) for item in order))
        self.assertLess(reader.index("return .boundary(.init(windows: rows"), reader.index("// Merge only one-to-one"))
        self.assertIn("return WindowGeometry.hint(item.element)", reader)
        self.assertIn("let relations = windowParents(mapped, native: nativeWindows", reader)
        self.assertIn("kAXDocumentAttribute", reader)

    def test_selected_attached_surface_is_excluded_before_anchor(self):
        route = read("Control/Routes/Passes/BuildNativeKeyboardRoute.swift")
        unit = read("Control/NativeKeyboardUnit.swift")
        self.assertIn("sameKeyboardSurface: CFEqual(state.window ?? window, window)", unit)
        self.assertIn("noObservedWeb: proposal?.interpreter.kind == .noObservedWeb", unit)
        self.assertRegex(unit, r"recipient\.remoteElement == nil\s*&& recipient\.hostRoute == nil")
        self.assertLess(
            route.index("routeFacts?.sameKeyboardSurface == true"), route.index("scopes.keyboardBoundaryAnchor(")
        )
        eligible = section(
            route, "            if eligible, let anchor {", "            } else {\n                dispatchCheck"
        )
        self.assertIn("routeFacts?.nativeID == anchor.nativeID", eligible)
        self.assertIn('bound["node"] is NSNull && contract.within.isEmpty', route)
        self.assertRegex(
            route,
            r"contract\.actuator == nil\s*&& contract\.receiver == nil\s*&& unit\.receiverRelation == nil",
        )

    def test_parent_gate_transitions_before_full_query_and_logs_once(self):
        route = read("Control/Routes/Passes/BuildNativeKeyboardRoute.swift")
        gate = section(route, "    static func sampleKeyboardBoundary(", "\n}\n")
        self.assertLess(gate.index("policy.recordProjection(decision)"), gate.index("return try legacy()"))
        self.assertLess(gate.index("policy.recordLegacySample()"), gate.index("return try legacy()"))
        self.assertEqual(gate.count("try legacy()"), 1)
        self.assertRegex(route, r"try sampleKeyboardBoundary\(\s*&policy,")
        self.assertIn("legacy: { try context.access.scopes.legacyKeyboardBoundary(anchor) }", route)
        self.assertEqual(route.count('"keyboard_boundary_summary"'), 1)
        policy = read("Windowing/NativeKeyboardBoundaryPolicy.swift")
        self.assertNotRegex(
            policy, r"AXAccess\.|AXUIElement|WindowServerInfo|inputScope\(|inputBinding\(|NativeOperation|receipt"
        )
        self.assertIn("guard mode == .projection else { return }", policy)

    def test_alias_is_read_only_and_legacy_rechecks_captured_ax(self):
        registry = read("Objects/ElementReferences.swift")
        self.assertIn("func knownReference(for identity: Identity) -> String? { references[identity] }", registry)
        scope = read("Windowing/NativeScopeResolver.swift")
        anchor = section(scope, "    func keyboardBoundaryAnchor(", "    /// QUERY only.")
        self.assertIn("CFEqual(original, window)", anchor)
        self.assertIn("BackgroundEvents.accessibilityProcessID(window) == application.processIdentifier", anchor)
        self.assertIn("id == native.mapping.id", anchor)
        sample = section(scope, "    func keyboardBoundary(_", "    /// Explicit full-query port")
        self.assertNotIn("NativeWindows.list", sample)
        self.assertNotIn("inputScope(", sample)
        legacy = section(scope, "    func legacyKeyboardBoundary(", "    func checkContainment(")
        self.assertIn("let scope = try inputScope(anchor.binding)", legacy)
        self.assertIn("CFEqual(window, anchor.window)", legacy)

    def test_receiver_remains_independent_with_single_existing_mapping_query(self):
        unit = read("Control/NativeKeyboardUnit.swift")
        sample = section(unit, "    func sample(", "    func textProposal(")
        self.assertRegex(sample, r"NativeReceiverSnapshot\.read\(\s*application: application,\s*window: window,")
        self.assertEqual(sample.count("NativeWindowUnit("), 1)
        self.assertLess(sample.index(".mapping()"), sample.index("mapping.id != expected"))
        self.assertLess(sample.index("mapping.id != expected"), sample.index("keyboardPreparationIsCurrent"))
        self.assertIn("code: .staleTarget", sample)
        for relative in ["Control/Input.swift", "Control/InputPacing.swift", "Control/NativeReceiverSnapshot.swift"]:
            self.assertNotIn("NativeKeyboardBoundary", read(relative))

    def test_contract_decision_is_independent_of_policy(self):
        contract = read("Contracts/NativeKeyboardBoundary.swift")
        scope = read("Contracts/NativeScopeAccess.swift")
        self.assertIn("enum NativeKeyboardBoundaryDecision", contract)
        self.assertIn("throws -> NativeKeyboardBoundaryDecision", scope)
        self.assertNotIn("NativeKeyboardBoundaryPolicy", scope)
        self.assertRegex(scope, r"window: AXUIElement\) throws\s*-> NativeKeyboardBoundaryAnchor\?\s*\{\s*nil\s*\}")

    def test_unknown_aux_is_kept_without_claiming_complete_native_parent_graph(self):
        reader = read("Read/NativeApplicationReader.swift")
        self.assertRegex(reader, r'Window\(\s*ref: "boundary:\\\(result\.count\)"')
        self.assertIn("parentKnown: !unknownBoundaryParents.contains", reader)
        policy = read("Windowing/NativeKeyboardBoundaryPolicy.swift")
        self.assertIn("bound[0].parentKnown", policy)
        self.assertIn('$0.modal == true || ($0.role == "AXSheet" && $0.modal == nil)', policy)
        self.assertNotIn("allSatisfy", policy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
