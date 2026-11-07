// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// A fixed window's text command can open its own native editor after the
/// first character. Only a proved direct attachment can receive the suffix.
@MainActor final class NativeAppEditorHandoff {
    @MainActor struct Access {
        var receiver = NativeKeyboardRecipient.Queries()
        var read: @MainActor (AXUIElement, String) -> AXRead<CFTypeRef> = AXAccess.read
        var writable: @MainActor (AXUIElement, String) -> Bool? = { AXAccess.writable($0, $1).value }
        var owner: (CGWindowID) -> pid_t? = {
            (WindowServerInfo.window($0)?[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value
        }
        var parents: @MainActor ([CGWindowID]) -> [CGWindowID: CGWindowID]? = NativeWindowParents.read
        var ownedWindows: (pid_t) -> Set<CGWindowID>? = { pid in
            guard let rows = CGWindowListCopyWindowInfo(.optionAll, kCGNullWindowID) as? [[String: Any]] else {
                return nil
            }
            var ids = Set<CGWindowID>()
            for row in rows where (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid {
                guard let id = (row[kCGWindowNumber as String] as? NSNumber)?.uint32Value else { return nil }
                ids.insert(id)
            }
            return ids
        }
        var delivery: @MainActor (NSRunningApplication, AXUIElement) throws -> NativeKeyboardRecipient = {
            try BackgroundEvents.keyboardRecipient(NativeWindowUnit(application: $0, window: $1).mapping())
        }

        func element(_ node: AXUIElement, _ attribute: String) -> AXUIElement? {
            guard let value = read(node, attribute).value, CFGetTypeID(value) == AXUIElementGetTypeID() else {
                return nil
            }
            return unsafeDowncast(value, to: AXUIElement.self)
        }

        func contains(_ node: AXUIElement, in window: AXUIElement) throws -> Bool {
            var current: AXUIElement? = node, seen = [AXUIElement]()
            for _ in 0..<16 {
                try RequestDiagnostics.checkCancellation()
                guard let currentNode = current, !seen.contains(where: { CFEqual($0, currentNode) }) else { return false }
                if CFEqual(currentNode, window) { return true }
                guard let role = receiver.role(currentNode),
                    ![kAXWindowRole, kAXSheetRole, kAXApplicationRole, "AXWebArea"].contains(role)
                else { return false }
                seen.append(currentNode)
                current = element(currentNode, kAXParentAttribute)
            }
            return false
        }
    }

    @MainActor struct Proof {
        let recipient: NativeKeyboardRecipient
        fileprivate let originalID: CGWindowID
        fileprivate let childID: CGWindowID
        fileprivate let child: AXUIElement
        fileprivate let editor: AXUIElement
        fileprivate let process: NativeProcessIdentity
        fileprivate let access: Access

        func authorizes(_ initial: NativeKeyboardRecipient, lifetime: NativeProcessIdentity?) -> Bool {
            lifetime == process && NativeAppEditorHandoff.local(initial, pid: process.pid, window: originalID)
                && NativeAppEditorHandoff.local(recipient, pid: process.pid, window: childID)
        }

        func validateFocus() throws {
            try RequestDiagnostics.checkCancellation()
            guard access.receiver.process(process.pid) == process,
                access.owner(originalID) == process.pid, access.owner(childID) == process.pid,
                access.receiver.windowID(child) == childID,
                access.parents([originalID, childID])?[childID] == originalID,
                access.read(child, kAXModalAttribute).value as? Bool == false,
                access.element(editor, kAXWindowAttribute).map({ CFEqual($0, child) }) == true,
                access.receiver.actualPID(editor) == process.pid,
                access.receiver.focus(process.pid).map({ CFEqual($0, editor) }) == true,
                access.read(editor, kAXFocusedAttribute).value as? Bool == true
            else { throw DriverError("the proved attached text editor changed before the remaining input", code: .staleTarget) }
            try RequestDiagnostics.checkCancellation()
        }
    }

    private let application: NSRunningApplication
    private let original: AXUIElement
    private let table: AXUIElement
    private let process: NativeProcessIdentity
    private let originalID: CGWindowID
    private let initialWindows: Set<CGWindowID>
    private let access: Access
    private var retained: Proof?
    var transitioned: Bool { retained != nil }

    private init(
        application: NSRunningApplication, original: AXUIElement, table: AXUIElement,
        process: NativeProcessIdentity, originalID: CGWindowID, initialWindows: Set<CGWindowID>, access: Access
    ) {
        self.application = application
        self.original = original
        self.table = table
        self.process = process
        self.originalID = originalID
        self.initialWindows = initialWindows
        self.access = access
    }

    private static func local(_ recipient: NativeKeyboardRecipient, pid: pid_t, window: CGWindowID) -> Bool {
        recipient.pid == pid && recipient.presenter == pid && recipient.window == window
            && recipient.remoteElement == nil && recipient.remoteRoute == nil
    }

    static func capture(
        application: NSRunningApplication, window: AXUIElement, state: NativeReceiverSnapshot,
        delivery: NativeKeyboardDelivery, access: Access = Access()
    ) throws -> Self? {
        guard state.windowRoute == "yes", state.scopeRoute == "yes", let table = state.focus,
            state.window.map({ CFEqual($0, window) }) == true, access.receiver.role(table) == kAXTableRole,
            let recipient = delivery.processRecipient, let id = access.receiver.windowID(window), id != 0,
            local(recipient, pid: application.processIdentifier, window: id),
            let process = access.receiver.process(application.processIdentifier),
            let initialWindows = access.ownedWindows(process.pid), initialWindows.contains(id)
        else { return nil }
        try RequestDiagnostics.checkCancellation()
        return Self(application: application, original: window, table: table, process: process,
                    originalID: id, initialWindows: initialWindows, access: access)
    }

    func resolve(_ state: NativeReceiverSnapshot) throws -> Proof? {
        guard let editor = state.rawFocus, !CFEqual(editor, table), state.rawLocalFocus == true else { return nil }
        try RequestDiagnostics.checkCancellation()
        guard access.receiver.process(process.pid) == process,
            access.receiver.role(table) == kAXTableRole, access.receiver.role(original) == kAXWindowRole,
            access.receiver.actualPID(table) == process.pid, access.receiver.windowID(original) == originalID,
            access.element(table, kAXWindowAttribute).map({ CFEqual($0, original) }) == true,
            try access.contains(table, in: original),
            let child = access.element(editor, kAXWindowAttribute), !CFEqual(child, original),
            access.receiver.role(child) == kAXWindowRole, let childID = access.receiver.windowID(child), childID != 0,
            childID != originalID, !initialWindows.contains(childID),
            access.receiver.role(editor) == kAXTextAreaRole,
            access.read(editor, kAXEnabledAttribute).value as? Bool != false,
            access.read(editor, "AXEditable").value as? Bool != false,
            access.writable(editor, kAXSelectedTextAttribute) == true, try access.contains(editor, in: child)
        else { return nil }
        if let retained {
            guard retained.childID == childID, CFEqual(retained.child, child), CFEqual(retained.editor, editor) else {
                return nil
            }
            return retained
        }
        // Prove ownership before querying a new event endpoint. A foreign or
        // already existing window is not a successor of this command.
        guard access.owner(originalID) == process.pid, access.owner(childID) == process.pid,
            access.parents([originalID, childID])?[childID] == originalID,
            access.read(child, kAXModalAttribute).value as? Bool == false,
            access.receiver.actualPID(editor) == process.pid
        else { return nil }
        let recipient = try access.delivery(application, child)
        guard Self.local(recipient, pid: process.pid, window: childID) else { return nil }
        let proof = Proof(recipient: recipient, originalID: originalID, childID: childID, child: child,
                          editor: editor, process: process, access: access)
        retained = proof
        diagnose(["kind": "attached_editor_handoff", "originalWindow": originalID, "window": childID,
                  "pid": process.pid, "delivery": "process", "continuation": "remainingTextOnly"] as [String: Any])
        return proof
    }
}
