// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// A Qt combo choice has two independent paths: the document publishes the
/// list under its combo, while an actual popup window publishes the same list.
/// Only that exact forward popup membership authorizes addressed mouse input.
/// In Qt's Cocoa bridge TableCell.toggle is advertised as AXPress, but AXPress
/// maps back to press for a ListItem; QAccessibleTableCell handles only toggle.
/// That declaration cannot implement an ordinary choice click. Custom QWidget
/// choices have separate action semantics and do not inherit this exclusion.
struct NativeQtComboChoice {
    enum Kind: Equatable { case tableCell, widget }
    /// The original delivered capture fixes the point's coordinate space. This
    /// does not turn a coordinate intent into an element click or move its point.
    struct CapturedPoint: Equatable {
        let capture: String
        let pixels: CGPoint
    }

    let element: AXUIElement
    let list: AXUIElement
    let row: AXUIElement?
    let popup: AXUIElement
    let observedWindow: AXUIElement
    let ancestors: [AXUIElement]
    let pid: pid_t
    let popupID: CGWindowID
    let ownerID: CGWindowID
    let observedWindowID: CGWindowID
    let ownerStamp: NativePopupOwnerStamp<AXUIElement, NativeProcessIdentity>?
    let kind: Kind
    var capturedPoint: CapturedPoint? = nil

    nonisolated static func kind(
        role: String?, identifier: String?, listIdentifier: String?, rowIdentifier: String?, hasRow: Bool
    ) -> Kind? {
        guard role == kAXStaticTextRole, let identifier, let listIdentifier,
            NativeQtAccessibilityIdentifier.isWidgetPath(listIdentifier)
        else { return nil }
        let parts = listIdentifier.split(separator: ".")
        if parts.last == "QComboBoxListView", hasRow, identifier == listIdentifier, rowIdentifier == listIdentifier {
            return .tableCell
        }
        if role == kAXStaticTextRole, !hasRow, parts.last == "QListWidget", parts.contains("QComboBoxPrivateContainer"),
            identifier.hasPrefix(listIdentifier + ".")
        {
            return .widget
        }
        return nil
    }

    /// Ordinary AXWindow descendants intentionally have no generic nested
    /// interaction-surface anchor. This mechanism separately needs its original
    /// observed window to prove the combo's forward attachment to the popup.
    /// Select only from that observation path, never from focus or a new parent.
    nonisolated static func observationAnchor<Node>(
        logicalSurface: Node?, ancestors: [Node], role: (Node) -> String?
    ) -> Node? {
        logicalSurface ?? ancestors.last(where: { role($0) == kAXWindowRole })
    }

    /// Missing leaf AXWindow is ordinary Qt behavior. A known different native
    /// window is a conflict; neither a Qt identifier nor a matching role wins.
    nonisolated static func acceptsWindowIDs(popup: UInt32, owner: UInt32, members: [UInt32?]) -> Bool {
        popup != 0 && owner != 0 && members.allSatisfy { $0 == nil || $0 == popup || $0 == owner }
    }

    /// Observation may start at the document or directly at its popup. The
    /// reverse owner is independent of that presentation choice.
    nonisolated static func acceptsObservedWindow(popup: UInt32, owner: UInt32, observed: UInt32) -> Bool {
        observed != 0 && (observed == popup || observed == owner)
    }

    @MainActor private static func sampleOwner(
        popup: AXUIElement, list: AXUIElement, pid: pid_t
    ) throws -> NativePopupOwnerStamp<AXUIElement, NativeProcessIdentity>? {
        let facts = NativePopupAXFacts(
            pid: pid, until: RequestDiagnostics.deadline ?? .infinity,
            check: {
                try RequestDiagnostics.checkCancellation()
                if let until = RequestDiagnostics.deadline, ProcessInfo.processInfo.systemUptime >= until {
                    throw RequestDeadlineExceeded()
                }
            }).facts()
        return try NativePopupAttachment.observe(popup: popup, list: list, facts: facts, same: { CFEqual($0, $1) })
    }

    @MainActor private static func children(_ node: AXUIElement) -> [AXUIElement]? {
        AXAccess.elements(node, kAXChildrenAttribute, limit: 2048)
    }

    @MainActor private static func owns(_ node: AXUIElement, pid: pid_t) -> Bool {
        var actual: pid_t = 0
        return AXUIElementGetPid(node, &actual) == .success && actual == pid
    }

    nonisolated static func containsChoice<Node>(
        element: Node, list: Node, row: Node?, popupChildren: [Node]?, listChildren: [Node]?, rowChildren: [Node]?,
        same: (Node, Node) -> Bool
    ) -> Bool {
        guard let popupChildren, popupChildren.count == 1, same(popupChildren[0], list),
            listChildren?.filter({ same($0, row ?? element) }).count == 1
        else { return false }
        guard let row, !same(row, element) else { return true }
        return rowChildren?.filter({ same($0, element) }).count == 1
    }

    @MainActor static func query(
        element: AXUIElement, role: String?, observedWindow: AXUIElement?, ancestors: [AXUIElement], pid: pid_t,
        capturedPoint: CapturedPoint? = nil
    ) throws -> Self? {
        guard role == kAXStaticTextRole,
            let identifier = AXAccess.read(element, kAXIdentifierAttribute).value as? String,
            NativeQtAccessibilityIdentifier.isWidgetPath(identifier),
            let parent = AXAccess.element(element, kAXParentAttribute)
        else { return nil }
        let row: AXUIElement?
        let list: AXUIElement
        if AXAccess.read(parent, kAXRoleAttribute).value as? String == kAXRowRole {
            row = parent
            guard let owner = AXAccess.element(parent, kAXParentAttribute) else { return nil }
            list = owner
        } else {
            row = nil
            list = parent
        }
        guard AXAccess.read(list, kAXRoleAttribute).value as? String == kAXListRole,
            let kind = kind(
                role: role, identifier: identifier,
                listIdentifier: AXAccess.read(list, kAXIdentifierAttribute).value as? String,
                rowIdentifier: row.flatMap { AXAccess.read($0, kAXIdentifierAttribute).value as? String },
                hasRow: row != nil),
            let observedWindow = observationAnchor(
                logicalSurface: observedWindow, ancestors: ancestors,
                role: { AXAccess.read($0, kAXRoleAttribute).value as? String }),
            let observedWindowID = BackgroundEvents.nativeWindowID(observedWindow)
        else { return nil }
        try RequestDiagnostics.checkCancellation()
        // Start with the actual parent surface when available. Qt's standard
        // QComboBox instead reverses through the combo; its popup is discoverable
        // through AXWindows and must publish this exact list as its only child.
        var candidates = [AXUIElement]()
        if capturedPoint != nil {
            // A captured popup already identifies its exact physical window.
            // Never search for a different popup to justify this pixel intent.
            candidates = [observedWindow]
        } else {
            if let direct = AXAccess.element(list, kAXParentAttribute) { candidates.append(direct) }
            candidates += AXAccess.elements(AXUIElementCreateApplication(pid), kAXWindowsAttribute, limit: 64) ?? []
        }
        var seen = [AXUIElement](), matches = [Self]()
        for popup in candidates where !seen.contains(where: { CFEqual($0, popup) }) {
            seen.append(popup)
            guard AXAccess.read(popup, kAXRoleAttribute).value as? String == kAXWindowRole,
                AXAccess.read(popup, kAXSubroleAttribute).value as? String == kAXDialogSubrole,
                AXAccess.read(popup, kAXModalAttribute).value as? Bool == false,
                let popupID = BackgroundEvents.nativeWindowID(popup)
            else { continue }
            let ownerStamp = try sampleOwner(popup: popup, list: list, pid: pid)
            let ownerID = ownerStamp?.ownerWindowID
                ?? (capturedPoint != nil ? BackgroundEvents.nativeWindowID(list) : nil) ?? observedWindowID
            var proof = Self(
                element: element, list: list, row: row, popup: popup, observedWindow: observedWindow,
                ancestors: ancestors, pid: pid, popupID: popupID, ownerID: ownerID,
                observedWindowID: observedWindowID, ownerStamp: ownerStamp, kind: kind)
            proof.capturedPoint = capturedPoint
            let confirmed = try proof.confirms()
            diagnose([
                "kind": "qt_combo_choice_receiver", "pid": pid, "popupWindow": popupID, "ownerWindow": ownerID,
                "observedWindow": observedWindowID,
                "choiceKind": kind == .tableCell ? "tableCell" : "widget", "confirmed": confirmed
            ])
            if confirmed { matches.append(proof) }
        }
        return matches.count == 1 ? matches[0] : nil
    }

    /// A specific native hit and the same existing forward Qt membership qualify
    /// the capture route. Missing/coarse AX leaves ordinary visual routes intact.
    @MainActor static func captured(
        _ input: NativeCapture.Input, pixels: CGPoint, hit: AXUIElement, role: String?
    ) throws -> Self? {
        guard let observed = input.entry.surface.element,
            let capture = input.entry.binding["capture"] as? String
        else { return nil }
        let proof = try query(
            element: hit, role: role,
            observedWindow: observed, ancestors: [], pid: input.mapping.pid,
            capturedPoint: .init(capture: capture, pixels: pixels))
        guard let proof, proof.popupID == input.mapping.id else { return nil }
        return proof
    }

    @MainActor private static func hit(pid: pid_t, at point: CGPoint) throws -> AXUIElement? {
        try RequestDiagnostics.checkCancellation()
        var value: AXUIElement?
        let status = AXUIElementCopyElementAtPosition(
            AXUIElementCreateApplication(pid), Float(point.x), Float(point.y), &value)
        try RequestDiagnostics.checkCancellation()
        return status == .success ? value : nil
    }

    /// Revalidate this exact pixel after translation and preparation. List
    /// membership alone cannot authorize a point now occupied by another row.
    @MainActor func confirmsCaptured(_ input: NativeCapture.Input) throws -> Bool {
        guard let point = capturedPoint,
            point.capture == input.entry.binding["capture"] as? String,
            input.mapping.pid == pid, input.mapping.id == popupID,
            let observed = input.entry.surface.element, CFEqual(observed, observedWindow),
            let current = try Self.hit(pid: pid, at: NativePointerUnit(capture: input).point(point.pixels)),
            CFEqual(current, element)
        else { return false }
        return try confirms()
    }

    /// Recheck the retained popup and exact choice before geometry/preparation
    /// and each gesture boundary. A replacement popup cannot be adopted here.
    @MainActor func confirms() throws -> Bool {
        try RequestDiagnostics.checkCancellation()
        let objects = [element, list, popup, observedWindow] + (row.map { [$0] } ?? [])
        guard objects.allSatisfy({ Self.owns($0, pid: pid) }),
            AXAccess.read(popup, kAXRoleAttribute).value as? String == kAXWindowRole,
            AXAccess.read(popup, kAXSubroleAttribute).value as? String == kAXDialogSubrole,
            AXAccess.read(popup, kAXModalAttribute).value as? Bool == false,
            BackgroundEvents.nativeWindowID(popup) == popupID,
            BackgroundEvents.nativeWindowID(observedWindow) == observedWindowID,
            Self.acceptsObservedWindow(popup: popupID, owner: ownerID, observed: observedWindowID),
            Self.acceptsWindowIDs(
                popup: popupID, owner: ownerID,
                members: ([element, list] + (row.map { [$0] } ?? [])).map(BackgroundEvents.nativeWindowID)),
            Self.containsChoice(
                element: element, list: list, row: row, popupChildren: Self.children(popup),
                listChildren: Self.children(list), rowChildren: row.flatMap(Self.children), same: { CFEqual($0, $1) }),
            AXAccess.read(list, kAXRoleAttribute).value as? String == kAXListRole
        else { return false }
        if let row {
            guard let parent = AXAccess.element(row, kAXParentAttribute), CFEqual(parent, list),
                AXAccess.read(row, kAXRoleAttribute).value as? String == kAXRowRole
            else { return false }
            if !CFEqual(row, element) {
                guard let parent = AXAccess.element(element, kAXParentAttribute), CFEqual(parent, row) else {
                    return false
                }
            }
        } else {
            guard let parent = AXAccess.element(element, kAXParentAttribute), CFEqual(parent, list) else {
                return false
            }
        }
        if let ownerStamp {
            guard let current = try Self.sampleOwner(popup: popup, list: list, pid: pid),
                ownerStamp.matches(current, same: { CFEqual($0, $1) })
            else { return false }
        }
        if CFEqual(observedWindow, popup) { return true }
        // Reuse the existing local-menu logical boundary. Reverse AXWindow may
        // name the owner, but the original observed forward path must still hold.
        return try LocalMenuAttachment.confirms(
            leaf: element, ancestors: ancestors, window: observedWindow, requireKnownRoles: true,
            same: { CFEqual($0, $1) }, role: { AXAccess.read($0, kAXRoleAttribute).value as? String },
            children: { node in
                try RequestDiagnostics.checkCancellation()
                guard let result = Self.children(node) else { return [] }
                return result
            })
    }

    func matches(_ other: Self) -> Bool {
        let sameOwner: Bool
        switch (ownerStamp, other.ownerStamp) {
        case (nil, nil): sameOwner = true
        case (let a?, let b?): sameOwner = a.matches(b, same: { CFEqual($0, $1) })
        default: sameOwner = false
        }
        let sameRow: Bool
        switch (row, other.row) {
        case (nil, nil): sameRow = true
        case (let a?, let b?): sameRow = CFEqual(a, b)
        default: sameRow = false
        }
        return capturedPoint == other.capturedPoint && sameOwner && sameRow && pid == other.pid
            && popupID == other.popupID && ownerID == other.ownerID
            && observedWindowID == other.observedWindowID && kind == other.kind
            && CFEqual(element, other.element) && CFEqual(list, other.list) && CFEqual(popup, other.popup)
            && CFEqual(observedWindow, other.observedWindow) && ancestors.count == other.ancestors.count
            && zip(ancestors, other.ancestors).allSatisfy { CFEqual($0.0, $0.1) }
    }

    static func actionOffer(_ original: NativeClickContract, action: String, kind: Kind?) -> NativeClickContract {
        guard kind == .tableCell, action == kAXPressAction else { return original }
        return .init(
            routeFamily: original.routeFamily, delivery: original.delivery, admission: .insufficientEvidence,
            reason:
                "Qt list-cell AXPress maps to a non-activation action; clicking this choice requires its pointer receiver.",
            preparationOptions: [])
    }
}
