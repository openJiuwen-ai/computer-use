import AppKit
import ApplicationServices

/// QUERY: distinguish actual foreground, provider state and transport preparation.
@MainActor enum SampleFocusStatePass {
    static func run(_ request: NativeFocusRequest) throws -> NativeFocusSnapshot {
        try request.check()
        try request.target.validate(input: true)
        let root = AXUIElementCreateApplication(request.window.pid)
        AXUIElementSetMessagingTimeout(root, 0.1)
        let focused = AXAccess.element(root, kAXFocusedWindowAttribute).flatMap(BackgroundEvents.nativeWindowID)
        return NativeFocusSnapshot(
            foreground: request.target.isForeground,
            reportedActive: AXAccess.read(root, kAXFrontmostAttribute).value as? Bool, focusedWindow: focused,
            prepared: SyntheticFocusCoordinator.shared.isCurrent(
                request.target, window: request.window, needsKeyWindow: request.needsKeyWindow))
    }
}
