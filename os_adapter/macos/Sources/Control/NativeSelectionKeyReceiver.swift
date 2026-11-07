// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// A Shift-arrow command can act on a selected range after focus moves to
/// another row in that range. The table/outline, window and selection are fixed.
@MainActor struct NativeSelectionKeyReceiver {
    @MainActor struct Access {
        var read: (AXUIElement, String) -> AXRead<CFTypeRef> = { AXAccess.read($0, $1) }
        var selectedRows: (AXUIElement) -> [AXUIElement]? = {
            AXAccess.elements($0, kAXSelectedRowsAttribute, limit: 65)
        }
        var pid: (AXUIElement) -> pid_t? = {
            var value: pid_t = 0
            return AXUIElementGetPid($0, &value) == .success ? value : nil
        }
    }

    let owner: AXUIElement
    let rows: [AXUIElement]

    func matches(_ current: Self?) -> Bool {
        guard let current, CFEqual(owner, current.owner), rows.count == current.rows.count else { return false }
        return zip(rows, current.rows).allSatisfy { CFEqual($0, $1) }
    }

    static func accepts(_ kind: NativeKeyboardIntent.Kind, key: String?) -> Bool {
        kind == .key && (key == "shift+Up" || key == "shift+Down")
    }

    static func capture(
        original: AXUIElement, state: NativeReceiverSnapshot, window: AXUIElement, pid: pid_t,
        using access: Access = .init()
    ) throws -> Self? {
        // Use the current receiver sample. Neither membership in AXSelectedRows
        // nor the original row's AXSelected bit establishes keyboard focus.
        guard access.read(original, kAXRoleAttribute).value as? String == kAXRowRole,
            state.windowRoute == "yes", state.scopeRoute == "yes", state.rawLocalFocus == true,
            let focused = state.rawFocus,
            state.rawFocusOwner.map({ CFEqual($0, window) }) == true,
            state.window.map({ CFEqual($0, window) }) == true, access.pid(window) == pid
        else { return nil }

        func object(_ node: AXUIElement, _ key: String) -> AXUIElement? {
            guard let value = access.read(node, key).value, CFGetTypeID(value) == AXUIElementGetTypeID()
            else { return nil }
            return unsafeDowncast(value, to: AXUIElement.self)
        }
        func owned(_ node: AXUIElement) -> Bool {
            access.pid(node) == pid && object(node, kAXWindowAttribute).map { CFEqual($0, window) } == true
                && access.read(node, kAXEnabledAttribute).value as? Bool != false
        }
        func owner(of row: AXUIElement) throws -> AXUIElement? {
            guard access.read(row, kAXRoleAttribute).value as? String == kAXRowRole, owned(row) else { return nil }
            var current = row
            for _ in 0..<8 {
                try RequestDiagnostics.checkCancellation()
                guard let parent = object(current, kAXParentAttribute), owned(parent) else { return nil }
                let role = access.read(parent, kAXRoleAttribute).value as? String
                if role == kAXTableRole || role == kAXOutlineRole { return parent }
                guard role == kAXGroupRole else { return nil }
                current = parent
            }
            return nil
        }
        guard let table = try owner(of: original), let focusOwner = try owner(of: focused),
            CFEqual(table, focusOwner), let rows = access.selectedRows(table), rows.count <= 64,
            rows.contains(where: { CFEqual($0, original) }), rows.contains(where: { CFEqual($0, focused) })
        else { return nil }
        for (index, row) in rows.enumerated() {
            guard !rows.prefix(index).contains(where: { CFEqual($0, row) }) else { return nil }
        }
        return Self(owner: table, rows: rows)
    }
}
