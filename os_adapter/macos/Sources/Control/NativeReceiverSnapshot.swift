// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Current AX receiver in one fixed window. This establishes object/editor
/// context only; posting keyboard events additionally needs a delivery route.
@MainActor struct NativeReceiverSnapshot {
    let windowRoute: String
    let focus: AXUIElement?
    let ancestors: [AXUIElement]
    let foreground: Bool
    var relatedEditor: AXUIElement? = nil
    var window: AXUIElement? = nil
    var surface: AXUIElement? = nil
    var scopeRoute = "yes"
    var reportedFocus: AXUIElement? = nil
    var focusReportAbsent = false
    /// Raw status belongs to this one sample; nil is not a window change.
    var focusedWindowRead: AXRead<CFTypeRef>? = nil
    var focusedWindowStatus: AXError? = nil
    /// Fresh unpromoted facts for a selected route's explicit alternative proof.
    var rawFocus: AXUIElement? = nil
    var rawFocusOwner: AXUIElement? = nil
    var rawLocalFocus: Bool? = nil

    func receives(_ element: AXUIElement?) -> Bool { receiver(element) == "yes" }

    func receiver(_ element: AXUIElement?) -> String {
        guard windowRoute == "yes" else { return windowRoute }
        guard let element else { return scopeRoute }
        if let surface = surface ?? window, CFEqual(surface, element) { return scopeRoute }
        if let relatedEditor, CFEqual(relatedEditor, element) { return "yes" }
        guard let focus else {
            // AXFocused is Apple's per-element keyboard-focus fact, separate
            // from the optional application-wide lookup. Use it only on this
            // exact target, in the confirmed key window, when that lookup is
            // absent/unsupported. Never override another reported object or
            // convert a messaging/permission failure into positive evidence.
            return NativeReceiverEvidence.localElementRoute(
                reportAbsent: focusReportAbsent,
                windowMatches: AXAccess.element(element, kAXWindowAttribute).flatMap { owner in
                    window.map { CFEqual(owner, $0) }
                }, focused: AXAccess.read(element, kAXFocusedAttribute).value as? Bool)
        }
        // Parentage is observation, not authority to send input to an
        // independent nested control. Python must select any related focus.
        return CFEqual(focus, element) ? "yes" : "no"
    }

    static func read(
        application: NSRunningApplication, window: AXUIElement, receiverRelation: NativeReceiverRelation? = nil,
        includeAncestors: Bool = false
    ) throws -> Self {
        let background = BackgroundTarget(application)
        try background.validate(input: true)
        try RequestDiagnostics.checkCancellation()
        let root = AXUIElementCreateApplication(application.processIdentifier)
        AXUIElementSetMessagingTimeout(root, 0.15)
        let originalKeyboardWindow = try WindowGeometry.keyboardWindow(window, pid: application.processIdentifier)
        var rawFocusedWindow: CFTypeRef?
        let focusedWindowStatus = AXUIElementCopyAttributeValue(
            root, kAXFocusedWindowAttribute as CFString, &rawFocusedWindow)
        let windowRead = AXRead<CFTypeRef>.result(focusedWindowStatus, rawFocusedWindow)
        let focusedWindow = windowRead.value.flatMap { value -> AXUIElement? in
            guard CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
            return unsafeDowncast(value, to: AXUIElement.self)
        }
        let focusRead = AXAccess.read(root, kAXFocusedUIElementAttribute)
        let focused = focusRead.value.flatMap { value -> AXUIElement? in
            guard CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
            return unsafeDowncast(value, to: AXUIElement.self)
        }
        let focusReportAbsent: Bool
        switch focusRead {
        case .unsupported, .unavailable(.noValue): focusReportAbsent = true
        default: focusReportAbsent = false
        }
        let localFocus = focused.flatMap { AXAccess.read($0, kAXFocusedAttribute).value as? Bool }
        let windowReportAbsent: Bool
        switch windowRead {
        case .unsupported, .unavailable(.noValue): windowReportAbsent = true
        default: windowReportAbsent = false
        }
        let attachedRoot = try NativeReceiverEvidence.attachedTextRoot(
            surface: window, keyboardWindow: originalKeyboardWindow, focus: focused,
            windowReportAbsent: windowReportAbsent, locallyFocused: localFocus, same: { CFEqual($0, $1) },
            role: { AXAccess.read($0, kAXRoleAttribute).value as? String },
            declaredRoot: { element in
                (AXAccess.read(root, kAXWindowsAttribute).value as? [AXUIElement])?.contains { CFEqual($0, element) }
                    == true
            },
            windowLinks: { element in
                var links = [AXUIElement]()
                for key in [kAXWindowAttribute, kAXTopLevelUIElementAttribute] {
                    switch AXAccess.read(element, key) {
                    case .unsupported, .unavailable(.noValue): continue
                    case .value(let value):
                        guard CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
                        links.append(unsafeDowncast(value, to: AXUIElement.self))
                    default: return nil
                    }
                }
                return links
            },
            nativeAttachment: { child, parent in
                try RequestDiagnostics.checkCancellation()
                guard let childID = BackgroundEvents.nativeWindowID(child),
                    let parentID = BackgroundEvents.nativeWindowID(parent), childID != parentID
                else { return false }
                for id in [childID, parentID] {
                    guard let row = WindowServerInfo.window(id),
                        (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == application.processIdentifier
                    else { return false }
                }
                guard NativeWindowParents.read([childID, parentID])?[childID] == parentID else { return false }
                try RequestDiagnostics.checkCancellation()
                guard let current = AXAccess.element(root, kAXFocusedUIElementAttribute), CFEqual(current, child),
                    AXAccess.read(child, kAXFocusedAttribute).value as? Bool == true
                else { return false }
                return true
            })
        let keyboardWindow = attachedRoot ?? originalKeyboardWindow
        let owner = focused.flatMap {
            WindowGeometry.owningWindow($0, pid: application.processIdentifier, in: keyboardWindow)
        }
        // Some transient editors are their own declared AX root. Do not try to
        // focus a different document merely because AXFocusedWindow is absent.
        // A failed lookup, another focused object or stale local focus cannot
        // take this path. Delivery still requires its own native-window route.
        let focusedRoot =
            windowReportAbsent && localFocus == true && focused.map({ CFEqual($0, keyboardWindow) }) == true
            && (attachedRoot != nil || (CFEqual(window, keyboardWindow) && WindowGeometry.isDeclaredWindowRoot(window)))
        let route = NativeReceiverEvidence.windowRoute(
            focusedWindowMatches: focusedWindow.map { CFEqual($0, keyboardWindow) }, focusPresent: focused != nil,
            focusWindowMatches: owner.map { CFEqual($0, keyboardWindow) }, focusedRoot: focusedRoot)
        // A host window does not authorize its other controls as receivers of
        // a popover-scoped request. Without an identified focus in this exact
        // surface, only an explicitly addressed editor's own evidence can help.
        let scopeRoute =
            attachedRoot != nil || CFEqual(window, keyboardWindow)
            ? "yes" : focused.map { WindowGeometry.contains($0, in: window) ? "yes" : "no" } ?? "unknown"
        // A window route can survive an incomplete focus report. The reported
        // object becomes an Element receiver only with confirmed ownership.
        let usable =
            route == "yes" && scopeRoute == "yes" && owner.map({ CFEqual($0, keyboardWindow) }) == true
                && localFocus != false ? focused : nil
        var ancestors = [AXUIElement]()
        // The full chain is input-context observation for Python's semantic
        // selection, not a delivery guard. Selected receiver relations below
        // still validate their own exact ancestry on every sample.
        if includeAncestors, var current = usable {
            for _ in 0..<40 {
                try RequestDiagnostics.checkCancellation()
                guard !CFEqual(current, window), let parent = AXAccess.element(current, kAXParentAttribute),
                    !CFEqual(parent, current), !ancestors.contains(where: { CFEqual($0, parent) })
                else { break }
                let role = AXAccess.read(parent, kAXRoleAttribute).value as? String
                guard role != kAXApplicationRole,
                    ![kAXWindowRole, kAXSheetRole].contains(role ?? "") || CFEqual(parent, window)
                else { break }
                ancestors.append(parent)
                current = parent
            }
        }
        let relatedEditor: AXUIElement?
        let relationFocus = receiverRelation?.usesReportedContainer == true ? focused : usable
        if route == "yes", let relationFocus, let receiverRelation, scopeRoute == "yes",
            try receiverRelation.matches(
                focused: relationFocus, window: keyboardWindow, pid: application.processIdentifier)
        {
            relatedEditor = receiverRelation.editor
        } else {
            relatedEditor = nil
        }
        return Self(
            windowRoute: route, focus: usable, ancestors: ancestors,
            foreground: background.isForeground && route == "yes", relatedEditor: relatedEditor, window: keyboardWindow,
            surface: window, scopeRoute: scopeRoute,
            reportedFocus: route == "yes" && scopeRoute == "yes" ? focused : nil, focusReportAbsent: focusReportAbsent,
            focusedWindowRead: windowRead, focusedWindowStatus: focusedWindowStatus, rawFocus: focused,
            rawFocusOwner: owner, rawLocalFocus: localFocus)
    }
}
