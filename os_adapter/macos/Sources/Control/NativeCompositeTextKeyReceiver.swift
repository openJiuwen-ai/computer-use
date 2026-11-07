// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// A focused incrementor can own the keyboard receiver of its sole text
/// field. The field remains the SDK target. Use this proof for its explicit
/// Return command, since the provider's text reads may affect selection.
@MainActor struct NativeCompositeTextKeyReceiver {
    @MainActor struct Access {
        var read: (AXUIElement, String) -> AXRead<CFTypeRef> = { AXAccess.read($0, $1) }
        var valueWritable: (AXUIElement) -> Bool = { AXAccess.writable($0, kAXValueAttribute).value == true }
        var parent: (AXUIElement) -> AXUIElement? = { AXAccess.element($0, kAXParentAttribute) }
        var children: (AXUIElement) -> [AXUIElement]? = { AXAccess.elements($0, kAXChildrenAttribute, limit: 1) }
        var pid: (AXUIElement) -> pid_t? = {
            var pid: pid_t = 0
            return AXUIElementGetPid($0, &pid) == .success ? pid : nil
        }
        var focus: (pid_t) -> AXUIElement? = { AXAccess.element(AXUIElementCreateApplication($0), kAXFocusedUIElementAttribute) }
        var string: (AXUIElement, Int) -> String? = { object, count in
            var all = CFRange(location: 0, length: count)
            guard let range = AXValueCreate(.cfRange, &all) else { return nil }
            var value: CFTypeRef?
            guard AXUIElementCopyParameterizedAttributeValue(
                object, kAXStringForRangeParameterizedAttribute as CFString, range, &value) == .success
            else { return nil }
            return value as? String
        }
    }

    let owner: AXUIElement
    let window: AXUIElement

    static func accepts(_ kind: NativeKeyboardIntent.Kind, key: String?) -> Bool {
        kind == .key && key == "Return"
    }

    func matches(_ current: Self?) -> Bool {
        guard let current else { return false }
        return CFEqual(owner, current.owner) && CFEqual(window, current.window)
    }

    static func capture(
        field: AXUIElement, state: NativeReceiverSnapshot, window: AXUIElement, pid: pid_t,
        using access: Access = .init()
    ) throws -> Self? {
        try RequestDiagnostics.checkCancellation()
        guard state.windowRoute == "yes", state.scopeRoute == "yes", state.rawLocalFocus == true,
            state.window.map({ CFEqual($0, window) }) == true,
            state.rawFocusOwner.map({ CFEqual($0, window) }) == true,
            let owner = state.rawFocus, !CFEqual(field, owner),
            state.focus.map({ CFEqual($0, owner) }) == true,
            access.read(owner, kAXRoleAttribute).value as? String == kAXIncrementorRole,
            access.read(field, kAXRoleAttribute).value as? String == kAXTextFieldRole,
            access.read(owner, kAXEnabledAttribute).value as? Bool != false,
            access.read(field, kAXEnabledAttribute).value as? Bool != false,
            access.read(field, "AXEditable").value as? Bool != false,
            access.read(field, "AXReadOnly").value as? Bool != true,
            access.valueWritable(field), access.pid(field) == pid, access.pid(owner) == pid, access.pid(window) == pid
        else { return nil }

        func ownsSoleChild() -> Bool {
            guard let parent = access.parent(field), CFEqual(parent, owner),
                let children = access.children(owner), children.count == 1 else { return false }
            return CFEqual(children[0], field)
        }
        func belongsToWindow() throws -> Bool {
            var node = owner, seen = [AXUIElement]()
            for _ in 0..<16 {
                try RequestDiagnostics.checkCancellation()
                if CFEqual(node, window) { return true }
                guard access.pid(node) == pid, !seen.contains(where: { CFEqual($0, node) }),
                    let parent = access.parent(node) else { return false }
                if !CFEqual(parent, window) {
                    guard let role = access.read(parent, kAXRoleAttribute).value as? String,
                        ![kAXApplicationRole, kAXWindowRole, kAXSheetRole].contains(role)
                    else { return false }
                }
                seen.append(node)
                node = parent
            }
            return false
        }
        guard ownsSoleChild(), let text = access.read(field, kAXValueAttribute).value as? String,
            (1...4096).contains(text.utf16.count)
        else { return nil }
        let count = text.utf16.count
        guard let number = access.read(owner, kAXNumberOfCharactersAttribute).value as? NSNumber,
            CFGetTypeID(number) != CFBooleanGetTypeID(), Int(number.stringValue) == count,
            let delegated = access.string(owner, count), delegated.utf16.elementsEqual(text.utf16)
        else { return nil }
        // The complete text query may call into provider code. Finish with
        // fresh ownership and focus on this same compound control.
        try RequestDiagnostics.checkCancellation()
        guard let current = access.read(field, kAXValueAttribute).value as? String,
            current.utf16.elementsEqual(text.utf16), ownsSoleChild(),
            try belongsToWindow(),
            access.read(owner, kAXFocusedAttribute).value as? Bool == true,
            let focused = access.focus(pid), CFEqual(focused, owner)
        else { return nil }
        try RequestDiagnostics.checkCancellation()
        return Self(owner: owner, window: window)
    }
}
