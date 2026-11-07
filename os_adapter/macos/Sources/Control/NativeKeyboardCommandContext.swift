// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// AppKit command context can be stricter than ordinary text reception.
/// These observations belong only to the selected paste or service shortcut.
@MainActor enum NativeKeyboardCommandContext {
    struct ClipboardEvidence {
        let standardWindow: Bool
        let windowFocused: Bool?
        let windowFocusWritable: Bool?
        let applicationFocusWritable: Bool?

        var needsForeground: Bool {
            standardWindow && windowFocused == false
                && windowFocusWritable == false && applicationFocusWritable == false
        }
    }

    static func clipboardNeedsForeground(_ window: AXUIElement, pid: pid_t) throws -> Bool {
        try RequestDiagnostics.checkCancellation()
        guard AXAccess.read(window, kAXRoleAttribute).value as? String == kAXWindowRole,
            AXAccess.read(window, kAXSubroleAttribute).value as? String == kAXStandardWindowSubrole,
            AXAccess.read(window, kAXFocusedAttribute).value as? Bool == false
        else { return false }
        let writable = AXAccess.writable(window, kAXFocusedAttribute).value
        guard writable == false else { return false }
        let appWritable = AXAccess.writable(AXUIElementCreateApplication(pid), kAXFocusedWindowAttribute).value
        try RequestDiagnostics.checkCancellation()
        return ClipboardEvidence(
            standardWindow: true, windowFocused: false, windowFocusWritable: writable,
            applicationFocusWritable: appWritable).needsForeground
    }

    static func isViewService(_ recipient: NativeKeyboardRecipient?) -> Bool {
        if case .viewService = recipient?.remoteRoute { return true }
        return false
    }

    /// An attached service may own the panel body before focus is installed.
    /// This query only qualifies preparation. Dispatch still resolves the
    /// actual focus and validates the native endpoint independently.
    static func coldService(
        window: AXUIElement, mapping: WindowGeometry.WindowMapping,
        children: (AXUIElement) -> [AXUIElement]? = { AXAccess.elements($0, kAXChildrenAttribute, limit: 16) },
        role: (AXUIElement) -> String? = { AXAccess.read($0, kAXRoleAttribute).value as? String },
        subrole: (AXUIElement) -> String? = { AXAccess.read($0, kAXSubroleAttribute).value as? String },
        query: NativeKeyboardRecipient.Queries = .init()
    ) throws -> Bool {
        let kind = role(window)
        guard kind == kAXSheetRole || (kind == kAXWindowRole && subrole(window) == kAXDialogSubrole),
            let children = children(window), children.count <= 16
        else { return false }
        var service: NativeProcessIdentity?
        for child in children {
            try RequestDiagnostics.checkCancellation()
            guard let actual = query.actualPID(child), actual != mapping.pid, query.belongs(child, mapping),
                let endpoint = try query.endpoint(child), endpoint.kind == .prohibitedApplication,
                endpoint.pid != mapping.pid, let process = query.process(endpoint.pid)
            else { continue }
            if let area = query.webArea(child, mapping, actual),
                KeyboardEventRoute.resolve(
                    hostWindow: mapping.id, focusWindow: query.windowID(child),
                    webAreaWindow: query.windowID(area), hasHostedWebArea: true) == .host
            { continue }
            guard service == nil || service == process else { return false }
            service = process
        }
        try RequestDiagnostics.checkCancellation()
        return service != nil
    }

    /// Only the standard-window clipboard mechanism retains the stricter
    /// key-window proof. A generic activation may have prepared a different
    /// supported receiver, including a focused AX root without AXFocusedWindow.
    static func validateClipboard(
        _ state: NativeReceiverSnapshot, window: AXUIElement, requiredKeyWindow: AXUIElement?,
        needsForeground: () throws -> Bool
    ) throws {
        if let requiredKeyWindow {
            guard CFEqual(requiredKeyWindow, window) else {
                throw DriverError("the clipboard command's original key window changed", code: .staleTarget)
            }
            try requireForeground(state, window: window, exactKey: true)
        } else if !state.foreground, try needsForeground() {
            throw DriverError("the clipboard command requires the original key window in foreground",
                              code: .inputUnavailable)
        }
    }

    static func requireForeground(_ state: NativeReceiverSnapshot, window: AXUIElement, exactKey: Bool) throws {
        guard state.foreground else {
            throw DriverError("the command's original receiving window is no longer foreground", code: .inputUnavailable)
        }
        if exactKey {
            guard let value = state.focusedWindowRead?.value, CFGetTypeID(value) == AXUIElementGetTypeID(),
                CFEqual(value, window)
            else { throw DriverError("the clipboard command's original key window is unconfirmed", code: .inputUnavailable) }
        }
    }
}
