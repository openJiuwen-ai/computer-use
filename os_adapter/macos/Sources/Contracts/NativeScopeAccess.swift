import AppKit
import ApplicationServices

/// Live scope facts and last-moment checks; no operation journal or input unit.
@MainActor protocol NativeScopeAccess: AnyObject {
    func inputBinding(_ raw: Any?, input: Bool, attachedMenuAction: Bool) throws -> (
        wire: [String: Any], blocked: [String], node: String?
    )
    func keyboardBoundaryAnchor(_ binding: [String: Any], application: NSRunningApplication, window: AXUIElement) throws
        -> NativeKeyboardBoundaryAnchor?
    func keyboardBoundary(_ anchor: NativeKeyboardBoundaryAnchor) throws -> NativeKeyboardBoundaryDecision
    func legacyKeyboardBoundary(_ anchor: NativeKeyboardBoundaryAnchor) throws -> [String]
    func checkContainment(_ binding: [String: Any], within: [String], op: NativeOperation) throws
    func checkActuator(_ binding: [String: Any], actuator: NativeInspectQuery.Actuator?, op: NativeOperation) throws
    func isDesktop(_ binding: [String: Any]) -> Bool
    func directoryTarget(_ raw: Any?) throws -> [String: Any]?
    func observationTarget(_ raw: Any?) throws -> (wire: [String: Any], node: String)
    func observePointer(_ binding: [String: Any])
    func aimPointer(_ binding: [String: Any], intent: NativeIntent, receiver: String?)
}

extension NativeScopeAccess {
    // Existing adapters do not opt into this new strategy implicitly.
    func keyboardBoundaryAnchor(_ binding: [String: Any], application: NSRunningApplication, window: AXUIElement) throws
        -> NativeKeyboardBoundaryAnchor?
    { nil }
    func keyboardBoundary(_ anchor: NativeKeyboardBoundaryAnchor) throws -> NativeKeyboardBoundaryDecision {
        .needsLegacy("keyboard boundary projection unavailable")
    }
    func legacyKeyboardBoundary(_ anchor: NativeKeyboardBoundaryAnchor) throws -> [String] {
        throw DriverError("fixed keyboard boundary adapter unavailable", code: .inputUnavailable)
    }
    func inputBinding(_ raw: Any?) throws -> (wire: [String: Any], blocked: [String], node: String?) {
        try inputBinding(raw, input: false, attachedMenuAction: false)
    }
}
