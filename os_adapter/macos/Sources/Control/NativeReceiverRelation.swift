// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// Recheck selected receiver relations under their SelectionBasis. The same
/// explicit popup-ownership proof can preserve an original editor within one
/// keyboard primitive; it never searches the window for a replacement owner.
@MainActor struct NativeReceiverRelation {
    let editor: AXUIElement
    let related: AXUIElement
    let selection: NativeInspectQuery.Receiver

    var usesReportedContainer: Bool {
        if case .ancestorFocus = selection { return true }
        return false
    }

    func matches(focused: AXUIElement, window: AXUIElement, pid: pid_t) throws -> Bool {
        try RequestDiagnostics.checkCancellation()
        let anchor: AXUIElement
        let depth: Int
        var start = focused
        switch selection {
        case .containedFocus:
            guard CFEqual(focused, related) else { return false }
            anchor = editor
            depth = 8
        case .ancestorFocus:
            guard CFEqual(focused, related), AXAccess.read(editor, kAXFocusedAttribute).value as? Bool == true else {
                return false
            }
            // Python proved the layout-only path and editor semantics. Recheck
            // exact ownership, ancestry and live focus; never search for a leaf
            // or treat arbitrary descendants as input recipients.
            anchor = related
            start = editor
            depth = 8
        case .virtualFocus(_, let via):
            return try Self.matchesVirtualFocus(
                editor: editor, popup: related, focused: focused, window: window, pid: pid, via: via)
        }
        for element in [editor, related, focused] {
            var actual: pid_t = 0
            guard AXUIElementGetPid(element, &actual) == .success, actual == pid,
                let owner = AXAccess.element(element, kAXWindowAttribute), CFEqual(owner, window)
            else { return false }
        }
        var current = start, seen = [AXUIElement]()
        for _ in 0..<depth {
            try RequestDiagnostics.checkCancellation()
            if CFEqual(current, anchor) { return true }
            guard !seen.contains(where: { CFEqual($0, current) }),
                let parent = AXAccess.element(current, kAXParentAttribute)
            else { return false }
            seen.append(current)
            current = parent
        }
        return CFEqual(current, anchor)
    }

    /// A popup changes the reported active descendant, not the retained text
    /// owner. Reuse this proof for a selected relation and one live input unit.
    static func matchesVirtualFocus(
        editor: AXUIElement, popup: AXUIElement, focused: AXUIElement, window: AXUIElement, pid: pid_t, via: String
    ) throws -> Bool {
        try RequestDiagnostics.checkCancellation()
        guard ["owns", "linked"].contains(via) else { return false }
        let property = via == "owns" ? "AXOwns" : "AXLinkedUIElements"
        guard
            [kAXTextFieldRole, kAXTextAreaRole, kAXComboBoxRole].contains(
                AXAccess.read(editor, kAXRoleAttribute).value as? String ?? ""),
            AXAccess.read(editor, "AXHasPopup").value as? Bool == true,
            [kAXListRole, kAXOutlineRole, kAXTableRole].contains(
                AXAccess.read(popup, kAXRoleAttribute).value as? String ?? ""),
            AXAccess.read(focused, kAXFocusedAttribute).value as? Bool == true,
            let focusedRole = AXAccess.read(focused, kAXRoleAttribute).value as? String, !focusedRole.isEmpty,
            ![kAXTextFieldRole, kAXTextAreaRole, kAXComboBoxRole, kAXIncrementorRole].contains(focusedRole),
            let owned = AXAccess.elements(editor, property, limit: 64), owned.contains(where: { CFEqual($0, popup) }),
            let selected = AXAccess.elements(popup, kAXSelectedChildrenAttribute, limit: 2), selected.count == 1,
            CFEqual(selected[0], focused)
        else { return false }
        for node in [editor, popup, focused] {
            var actual: pid_t = 0
            guard AXUIElementGetPid(node, &actual) == .success, actual == pid,
                let owner = AXAccess.element(node, kAXWindowAttribute), CFEqual(owner, window)
            else { return false }
        }
        var current = focused, seen = [AXUIElement]()
        for _ in 0..<11 {  // Same 12 visited-node budget as VirtualFocusQuery.
            try RequestDiagnostics.checkCancellation()
            if CFEqual(current, popup) { return true }
            guard !seen.contains(where: { CFEqual($0, current) }),
                let parent = AXAccess.element(current, kAXParentAttribute)
            else { return false }
            seen.append(current)
            current = parent
        }
        return CFEqual(current, popup)
    }

    /// Only the original editor's explicit relationships may explain a changed
    /// focus. No window search, editor adoption or cached relatedEditor hint.
    static func ownsVirtualFocus(editor: AXUIElement, focused: AXUIElement, window: AXUIElement) throws -> Bool {
        guard
            [kAXTextFieldRole, kAXTextAreaRole, kAXComboBoxRole].contains(
                AXAccess.read(editor, kAXRoleAttribute).value as? String ?? ""),
            AXAccess.read(editor, "AXHasPopup").value as? Bool == true
        else { return false }
        var pid: pid_t = 0
        guard AXUIElementGetPid(window, &pid) == .success else { return false }
        var seen = [AXUIElement](), matched = [AXUIElement]()
        for (via, property) in [("owns", "AXOwns"), ("linked", "AXLinkedUIElements")] {
            try RequestDiagnostics.checkCancellation()
            var count: CFIndex = 0
            let status = AXUIElementGetAttributeValueCount(editor, property as CFString, &count)
            if [.attributeUnsupported, .notImplemented, .noValue].contains(status) { continue }
            guard status == .success, count >= 0, count <= 64,
                let popups = AXAccess.elements(editor, property, limit: 64)
            else { return false }
            for popup in popups {
                try RequestDiagnostics.checkCancellation()
                guard !seen.contains(where: { CFEqual($0, popup) }) else { continue }
                seen.append(popup)
                if try matchesVirtualFocus(
                    editor: editor, popup: popup, focused: focused, window: window, pid: pid, via: via)
                {
                    matched.append(popup)
                }
                if matched.count > 1 { return false }
            }
        }
        return matched.count == 1
    }
}
