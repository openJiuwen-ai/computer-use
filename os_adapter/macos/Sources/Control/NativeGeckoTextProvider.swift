// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// Evidence from the editor's actual AX provider. The two Gecko delegate
/// parameters identify its implementation by advertisement; their debug
/// getters are never called and text markers are never decoded here.
@MainActor enum NativeGeckoTextProvider {
    struct Proof: Equatable {
        let element: AXUIElement
        let document: AXUIElement
        let window: AXUIElement
        let pid: pid_t

        static func == (lhs: Self, rhs: Self) -> Bool {
            lhs.pid == rhs.pid && CFEqual(lhs.element, rhs.element) && CFEqual(lhs.document, rhs.document)
                && CFEqual(lhs.window, rhs.window)
        }
    }

    static func exposesContract(_ names: [String]?) -> Bool {
        guard let names else { return false }
        return names.contains("AXMozDebugDescriptionForTextMarker")
            && names.contains("AXMozDebugDescriptionForTextMarkerRange")
    }

    private static func parameters(_ element: AXUIElement) -> [String]? {
        var names: CFArray?
        guard AXUIElementCopyParameterizedAttributeNames(element, &names) == .success else { return nil }
        return names as? [String]
    }

    static func prove(element: AXUIElement, presenterPID: pid_t, originalWindow: () throws -> AXUIElement?) throws
        -> Proof?
    {
        guard AXAccess.read(element, kAXRoleAttribute).value as? String == kAXTextFieldRole,
            AXAccess.read(element, kAXSubroleAttribute).value as? String == kAXSearchFieldSubrole,
            exposesContract(parameters(element)), let window = try originalWindow(),
            AXAccess.read(window, kAXRoleAttribute).value as? String == kAXWindowRole
        else { return nil }
        let scope = try NativeKeyboardDeliveryProposal.read(element: element, window: window, pid: presenterPID)
            .interpreter
        guard scope.kind == .localWeb, scope.complete, let document = scope.area, exposesContract(parameters(document))
        else { return nil }
        try RequestDiagnostics.checkCancellation()
        return Proof(element: element, document: document, window: window, pid: presenterPID)
    }

    static func needsAtomicReplacement(text: String, value: String?) -> Bool { !text.isEmpty && value?.isEmpty != true }

    static func focused(_ proof: Proof) -> Bool {
        let application = AXUIElementCreateApplication(proof.pid)
        AXUIElementSetMessagingTimeout(application, 0.15)
        guard let current = AXAccess.element(application, kAXFocusedUIElementAttribute), CFEqual(current, proof.element)
        else { return false }
        return AXAccess.read(proof.element, kAXFocusedAttribute).value as? Bool == true
    }

    static func permitsSelectedText(
        replace: Bool, text: String, value: String?, selection: NativeTextSelection?,
        pinnedSelection: NativeTextSelection?
    ) -> Bool {
        if text.isEmpty { return true }
        if replace { return value?.isEmpty == true }
        if let pinnedSelection, selection != pinnedSelection { return false }
        // Gecko's split setter ignores a sole collapsed range. Defaulting
        // to offset zero is faithful only for an empty buffer or known caret 0.
        guard let selection else { return value?.isEmpty == true }
        if selection.count == 0, value?.isEmpty == true { return true }
        return selection.contiguous && selection.location == 0 && selection.length == 0
    }
}
