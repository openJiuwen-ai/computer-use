import AppKit
import ApplicationServices

/// PREPARE helper for an already selected App-scoped HID plan. It cannot
/// activate another App, click content, select text or send requested keys.
@MainActor enum NativeScopedKeyboardPreparation {
    static func prepare(
        _ scoped: NativeScopedKeyboardDelivery, check: () throws -> Void, onPreparation: (String) -> Void
    ) throws {
        try check()
        try BackgroundTarget.validateReleasedInput()
        try scoped.requireCurrentBoundary()
        // A popover or attached editor retains its smaller original surface;
        // raising/selecting a parent document could dismiss that current UI.
        guard CFEqual(scoped.surface, scoped.window) else { return }
        // Foreground App + a remembered AX window is insufficient. Address the
        // retained window explicitly; no content/activation-point primer exists.
        var names: CFArray?
        if AXUIElementCopyActionNames(scoped.window, &names) == .success,
            (names as? [String])?.contains(kAXRaiseAction) == true
        {
            try check()
            try scoped.requireCurrentBoundary()
            onPreparation("scoped keyboard exact-window AXRaise")
            let status = AXMessaging.performAction(scoped.window, kAXRaiseAction as CFString)
            if status == .invalidUIElement {
                throw DriverError("scoped keyboard window disappeared", code: .staleTarget)
            }
        }
        try NativeDocumentContext(application: scoped.application, window: scoped.window).select(
            requireSelection: true,
            check: {
                try check()
                try scoped.requireCurrentBoundary()
            }, onPreparation: onPreparation, onNativePreparation: { _ in })
        try check()
        try scoped.requireCurrentBoundary()
    }
}
