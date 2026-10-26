import AppKit
import ApplicationServices

/// Fixed objects and identities captured by this selected keyboard unit only.
/// No live window/focus/modal facts or operation journal are retained here.
@MainActor struct NativeKeyboardBoundaryAnchor {
    let binding: [String: Any]
    let application: NSRunningApplication
    let process: NativeProcessIdentity
    let rootReference: String
    let window: AXUIElement
    let nativeID: CGWindowID
}

/// Typed evidence emitted at the original delivery selection; never a log parse.
struct NativeKeyboardBoundaryRouteFacts {
    let localProcess: Bool
    let noObservedWeb: Bool
    let sameKeyboardSurface: Bool
    let nativeID: CGWindowID?
}

/// Query decision consumed by the parent Gate, independent of strategy state.
enum NativeKeyboardBoundaryDecision: Equatable { case confirmed, changed, needsLegacy(String) }

struct NativeKeyboardBoundaryProjection<Element> {
    struct Window {
        let key: String
        let element: Element
        let role: String
        let modal: Bool?
        let parent: String?
        let parentKnown: Bool
    }
    let windows: [Window]
    let issue: String?
}
