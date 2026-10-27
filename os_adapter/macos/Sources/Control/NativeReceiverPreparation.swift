import AppKit
import ApplicationServices

/// Establish AX context on an already selected object. This is preparation,
/// not a declaration that text can be edited or keyboard events delivered.
@MainActor struct NativeReceiverPreparation {
    struct Contract: Equatable { let role: String }
    let application: NSRunningApplication
    let window: AXUIElement
    let element: AXUIElement?
    var receiverRelation: NativeReceiverRelation? = nil
    var cellEditor: NativeCellEditorHandoff? = nil

    func contract() -> Contract? {
        guard let element, let role = AXAccess.read(element, kAXRoleAttribute).value as? String, !role.isEmpty,
            AXAccess.writable(element, kAXFocusedAttribute).value == true
        else { return nil }
        return Contract(role: role)
    }

    func readiness() throws -> NativeReadiness {
        let current = try NativeReceiverSnapshot.read(
            application: application, window: window, receiverRelation: receiverRelation)
        let receiver = current.receiver(element)
        if receiver == "yes", let cellEditor,
            try cellEditor.association(window: window).map({ proof in element.map { CFEqual($0, proof.editor) } == true }) != true {
            return NativeReadiness([("receiver", "unknown")])
        }
        return NativeReadiness([("receiver", receiver)])
    }

    func validate(_ expected: Contract, check: @MainActor () throws -> Void) throws {
        try check()
        guard let element, AXAccess.read(element, kAXRoleAttribute).value as? String == expected.role,
            AXAccess.read(element, kAXEnabledAttribute).value as? Bool != false
        else { throw DriverError("the selected receiver preparation contract changed", code: .inputUnavailable) }
    }

    /// One native focus mutation. The caller supplies fresh scope/ownership
    /// checks; the narrow setter port permits a no-desktop ordering regression.
    static func setFocus(
        _ element: AXUIElement, detail: String, check: () throws -> Void, onPreparation: (String) -> Void,
        set: (AXUIElement, CFString, CFTypeRef) -> AXError = AXMessaging.setAttributeValue
    ) throws {
        try check()
        onPreparation(detail)
        _ = set(element, kAXFocusedAttribute as CFString, kCFBooleanTrue)
    }

    /// The caller supplies its route-specific readiness and boundary guard.
    /// Selection editing and keyboard delivery must not share a readiness flag.
    func prepare(
        foreground: Bool, check: @escaping @MainActor () throws -> Void, ready: @escaping @MainActor () throws -> Bool,
        onPreparation: @escaping @MainActor (String) -> Void, onCleanupUnknown: @escaping @MainActor () -> Void = {}
    ) async throws {
        try check()
        let keyboardWindow = try WindowGeometry.keyboardWindow(window, pid: application.processIdentifier)
        func validate() throws {
            try check()
            try BackgroundTarget(application).validate(input: true)
            try RequestDiagnostics.checkCancellation()
            guard try CFEqual(WindowGeometry.keyboardWindow(window, pid: application.processIdentifier), keyboardWindow)
            else {
                throw DriverError(
                    "the selected surface changed its keyboard window during preparation", code: .staleTarget)
            }
        }
        func validateEffect() throws {
            try validate()
            try cellEditor?.requireAssociation(element: element, window: window)
        }
        try validate()
        try BackgroundTarget.validateReleasedInput()
        if try ready() { return }
        if foreground {
            let mapping = try NativeWindowUnit(application: application, window: keyboardWindow).mapping()
            try await ForegroundAssist().prepare(
                BackgroundTarget(application), window: mapping.id,
                onAction: { onPreparation("foreground window activation") }, onCleanupUnknown: onCleanupUnknown)
        }
        let root = AXUIElementCreateApplication(application.processIdentifier)
        if AXAccess.element(root, kAXFocusedWindowAttribute).map({ CFEqual($0, keyboardWindow) }) != true,
            AXAccess.writable(keyboardWindow, kAXFocusedAttribute).value == true
        {
            try Self.setFocus(
                keyboardWindow, detail: "native window focus", check: validate, onPreparation: onPreparation)
        }
        if let element,
            try NativeReceiverSnapshot.read(
                application: application, window: window, receiverRelation: receiverRelation
            ).receiver(element) != "yes"
        {
            guard AXAccess.writable(element, kAXFocusedAttribute).value == true else {
                throw NativePreparationUnavailable(
                    description: "the selected receiver does not provide direct native focus")
            }
            try Self.setFocus(
                element, detail: "native element focus", check: validateEffect, onPreparation: onPreparation)
        }
        for attempt in 0..<12 {
            try validate()
            if try ready() { return }
            if attempt < 11 { try await Task.sleep(for: .milliseconds(25)) }
        }
        throw NativePreparationUnavailable(
            description: "native receiver context is not ready; no requested input was sent")
    }
}
