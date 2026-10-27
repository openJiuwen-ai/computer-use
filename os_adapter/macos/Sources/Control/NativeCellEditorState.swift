import AppKit
import ApplicationServices

/// One registered cell lifetime owns its established editor association. It is
/// never a global focus cache: every borrow repeats structural ownership proof;
/// each input unit separately establishes its current keyboard readiness.
@MainActor final class NativeCellEditorState {
    private var lifetime = NativeCellEditorLifetime<NativeCellEditorHandoff>()
    nonisolated init() {}

    func retain(_ handoff: NativeCellEditorHandoff) {
        if handoff.editor != nil { lifetime.retain(handoff) }
    }

    func resolve(element: AXUIElement, application: NSRunningApplication, window: AXUIElement) throws
        -> (element: AXUIElement, proof: NativeCellEditorHandoff)?
    {
        try lifetime.resolve { retained in
            guard retained.belongs(to: element, window: window, pid: application.processIdentifier),
                let proof = try retained.association(window: window)
            else { return nil }
            return (proof.editor, retained)
        }
    }
}
