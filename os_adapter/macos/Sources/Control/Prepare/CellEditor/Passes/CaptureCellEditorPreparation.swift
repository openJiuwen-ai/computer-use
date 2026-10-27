import AppKit
import ApplicationServices

/// QUERY: capture the selected cell before its editor can be instantiated.
@MainActor enum CaptureCellEditorPreparationPass {
    static func run(element: AXUIElement, application: NSRunningApplication, window: AXUIElement) throws
        -> NativeCellEditorPreparationPlan
    {
        let before = try NativeReceiverSnapshot.read(application: application, window: window)
        guard let handoff = try NativeCellEditorHandoff.capture(
            element: element, window: window, focus: before.focus, pid: application.processIdentifier)
        else { throw NativePreparationUnavailable(description: "the original cell has no confirmed editing preparation") }
        return NativeCellEditorPreparationPlan(
            application: application, window: window, handoff: handoff,
            pointer: NativeElementPointerUnit(application: application, window: window, element: element),
            activation: try NativePointerIntent(
                unit: "pointer_sequence", kind: "click", parameters: ["button": "left", "count": 2],
                foreground: false, element: true))
    }
}
