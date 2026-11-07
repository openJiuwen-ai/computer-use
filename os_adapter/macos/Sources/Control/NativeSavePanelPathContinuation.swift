// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Native SavePanel can consume the start of an absolute path in its own
/// name field and transfer editing to its direct Go To sheet.
@MainActor final class NativeSavePanelPathContinuation {
    @MainActor struct Access {
        var receiver = NativeKeyboardRecipient.Queries()
        var read: @MainActor (AXUIElement, String) -> AXRead<CFTypeRef> = AXAccess.read
        var publicPID: (AXUIElement) -> pid_t? = {
            var pid: pid_t = 0
            return AXUIElementGetPid($0, &pid) == .success ? pid : nil
        }
        var owner: (CGWindowID) -> pid_t? = {
            (WindowServerInfo.window($0)?[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value
        }
        var parents: @MainActor ([CGWindowID]) -> [CGWindowID: CGWindowID]? = NativeWindowParents.read
        var ownedWindows: (Set<pid_t>) -> Set<CGWindowID>? = { owners in
            guard let rows = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID)
                as? [[String: Any]] else { return nil }
            var ids = Set<CGWindowID>()
            for row in rows {
                guard let owner = (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value,
                    owners.contains(owner) else { continue }
                guard let id = (row[kCGWindowNumber as String] as? NSNumber)?.uint32Value else { return nil }
                ids.insert(id)
            }
            return ids
        }

        func element(_ object: AXUIElement, _ key: String) -> AXUIElement? {
            guard let value = read(object, key).value, CFGetTypeID(value) == AXUIElementGetTypeID()
            else { return nil }
            return unsafeDowncast(value, to: AXUIElement.self)
        }
    }

    @MainActor struct Proof {
        let original: AXUIElement
        let panel: AXUIElement
        let sheet: AXUIElement
        let panelID: CGWindowID
        let sheetID: CGWindowID
        let presenter: NativeProcessIdentity
        let service: NativeProcessIdentity
        let recipient: NativeKeyboardRecipient
        fileprivate let originalRecipient: NativeKeyboardRecipient
        fileprivate let access: Access

        func authorizes(_ initial: NativeKeyboardRecipient, lifetime: NativeProcessIdentity?) -> Bool {
            lifetime == service && initial.pid == service.pid && initial.presenter == presenter.pid
                && initial.window == originalRecipient.window
                && initial.remoteElement.map { CFEqual($0, original) } == true
                && recipient.pid == initial.pid && recipient.presenter == initial.presenter
        }

        func validateFocus() throws {
            try RequestDiagnostics.checkCancellation()
            guard let field = recipient.remoteElement, let focused = access.receiver.focus(presenter.pid),
                CFEqual(focused, field), access.read(field, kAXFocusedAttribute).value as? Bool == true,
                access.element(field, kAXParentAttribute).map({ CFEqual($0, sheet) }) == true,
                access.receiver.actualPID(field) == service.pid,
                access.receiver.windowID(field) == recipient.window,
                access.receiver.process(presenter.pid) == presenter, access.receiver.process(service.pid) == service
            else { throw DriverError("the proved Go To path field is no longer focused", code: .staleTarget) }
            try RequestDiagnostics.checkCancellation()
        }

        func blocked(in windows: [NativeApplicationAccess.Window], root: String, focused: String?) throws -> [String] {
            let panels = windows.filter { CFEqual($0.element, panel) && $0.nativeID == panelID }
            let sheets = windows.filter { CFEqual($0.element, sheet) && $0.nativeID == sheetID }
            guard panels.count == 1, sheets.count == 1, let panel = panels.first, let sheet = sheets.first,
                sheet.parent == panel.ref, !sheet.parentUnknown, sheet.modal == true
            else { throw DriverError("the Go To sheet has no confirmed original SavePanel scope", code: .inputUnavailable) }
            return try NativeWindowBoundary.blocked(
                root: root, target: sheet.ref,
                windows: windows.map { .init(ref: $0.ref, parent: $0.parent, modal: $0.modal == true) }, focused: focused)
        }
    }

    private let original: AXUIElement
    private let panel: AXUIElement
    private let group: AXUIElement
    private let panelID: CGWindowID
    private let serviceWindow: CGWindowID
    private let presenter: NativeProcessIdentity
    private let service: NativeProcessIdentity
    private let initialWindows: Set<CGWindowID>
    private let recipient: NativeKeyboardRecipient
    private let access: Access
    private var retained: Proof?
    var transitioned: Bool { retained != nil }

    private init(
        original: AXUIElement, panel: AXUIElement, group: AXUIElement, panelID: CGWindowID,
        serviceWindow: CGWindowID, presenter: NativeProcessIdentity, service: NativeProcessIdentity,
        initialWindows: Set<CGWindowID>, recipient: NativeKeyboardRecipient, access: Access
    ) {
        self.original = original
        self.panel = panel
        self.group = group
        self.panelID = panelID
        self.serviceWindow = serviceWindow
        self.presenter = presenter
        self.service = service
        self.initialWindows = initialWindows
        self.recipient = recipient
        self.access = access
    }

    static func capture(
        window: AXUIElement, element: AXUIElement?, text: String, state: NativeReceiverSnapshot,
        delivery: NativeKeyboardDelivery, access: Access = Access()
    ) throws -> Self? {
        guard text.hasPrefix("/"), let element, let recipient = delivery.processRecipient,
            case .viewService(let host) = recipient.remoteRoute, let service = recipient.process,
            recipient.pid == service.pid, recipient.endpointProcess == service,
            recipient.remoteElement.map({ CFEqual($0, element) }) == true,
            state.windowRoute == "yes", state.scopeRoute == "yes", state.rawLocalFocus == true,
            state.focus.map({ CFEqual($0, element) }) == true, state.window.map({ CFEqual($0, window) }) == true
        else { return nil }
        try RequestDiagnostics.checkCancellation()
        guard Self.field(element, identifier: "saveAsNameTextField", access),
            access.read(element, kAXValueAttribute).value as? String == "", Self.caret(element, at: 0, access),
            Self.panel(window, access), let group = access.element(element, kAXParentAttribute),
            Self.role(group, access) == kAXSplitGroupRole, Self.link(group, kAXParentAttribute, to: window, access),
            let groupChildren = Self.children(group, access), groupChildren.contains(where: { CFEqual($0, element) }),
            let panelChildren = Self.children(window, access), panelChildren.contains(where: { CFEqual($0, group) }),
            Self.sheets(panelChildren, access)?.isEmpty == true, let panelID = access.receiver.windowID(window),
            panelID != 0, let serviceWindow = recipient.window, serviceWindow != 0,
            recipient.axWindow == serviceWindow, access.receiver.windowID(element) == serviceWindow,
            access.owner(panelID) == host.process.pid, access.owner(serviceWindow) == service.pid,
            access.publicPID(element) == host.process.pid, access.publicPID(window) == host.process.pid,
            access.receiver.actualPID(window) == host.process.pid, access.receiver.actualPID(element) == service.pid,
            access.receiver.actualPID(group) == service.pid, access.receiver.windowID(group) == serviceWindow,
            let owned = access.ownedWindows([host.process.pid, service.pid]),
            owned.contains(panelID), owned.contains(serviceWindow),
            access.receiver.focus(host.process.pid).map({ CFEqual($0, element) }) == true,
            access.read(element, kAXFocusedAttribute).value as? Bool == true,
            access.receiver.process(host.process.pid) == host.process, access.receiver.process(service.pid) == service
        else { return nil }
        try RequestDiagnostics.checkCancellation()
        return Self(original: element, panel: window, group: group, panelID: panelID, serviceWindow: serviceWindow,
                    presenter: host.process, service: service, initialWindows: owned, recipient: recipient, access: access)
    }

    func resolve(postedPrefix: String, state: NativeReceiverSnapshot) throws -> Proof? {
        // Compare the consumed prefix once. A live bound path field need not
        // publish its AXValue on every following keystroke.
        guard postedPrefix.hasPrefix("/"), state.rawLocalFocus == true,
            state.window.map({ CFEqual($0, panel) }) == true,
            let reported = state.rawFocus, !CFEqual(reported, original),
            let reportedPID = access.publicPID(reported), reportedPID == presenter.pid || reportedPID == service.pid,
            access.receiver.actualPID(reported) == service.pid
        else { return nil }
        try RequestDiagnostics.checkCancellation()
        guard access.receiver.process(presenter.pid) == presenter, access.receiver.process(service.pid) == service,
            access.publicPID(original) == presenter.pid, access.receiver.actualPID(original) == service.pid,
            access.receiver.actualPID(group) == service.pid, Self.role(group, access) == kAXSplitGroupRole,
            access.receiver.windowID(group) == serviceWindow, access.owner(serviceWindow) == service.pid,
            Self.panel(panel, access), access.receiver.windowID(panel) == panelID, access.owner(panelID) == presenter.pid,
            Self.link(original, kAXParentAttribute, to: group, access), Self.link(group, kAXParentAttribute, to: panel, access),
            let groupChildren = Self.children(group, access), groupChildren.contains(where: { CFEqual($0, original) }),
            let panelChildren = Self.children(panel, access), panelChildren.contains(where: { CFEqual($0, group) }),
            let sheets = Self.sheets(panelChildren, access), sheets.count == 1, let sheet = sheets.first,
            access.read(sheet, kAXIdentifierAttribute).value as? String == "GoToWindow", access.publicPID(sheet) == presenter.pid,
            Self.link(sheet, kAXParentAttribute, to: panel, access),
            let sheetID = access.receiver.windowID(sheet), sheetID != 0, !initialWindows.contains(sheetID),
            access.owner(sheetID) == presenter.pid, access.parents([panelID, sheetID])?[sheetID] == panelID,
            let children = Self.children(sheet, access)
        else { return nil }
        // Service-namespace focus is usable through the same CF object in
        // the original panel's direct host projection.
        let matches = children.filter { CFEqual($0, reported) }
        guard matches.count == 1, let field = matches.first, Self.field(field, identifier: "PathTextField", access),
            Self.link(field, kAXParentAttribute, to: sheet, access), access.publicPID(field) == presenter.pid,
            access.receiver.actualPID(field) == service.pid
        else { return nil }
        for key in [kAXWindowAttribute, kAXTopLevelUIElementAttribute] {
            if let raw = access.read(field, key).value {
                guard CFGetTypeID(raw) == AXUIElementGetTypeID(),
                    CFEqual(raw, sheet) || CFEqual(raw, panel) else { return nil }
            }
        }
        let eventWindow = access.receiver.windowID(field)
        if let eventWindow {
            guard eventWindow != 0, !initialWindows.contains(eventWindow), access.owner(eventWindow) == service.pid,
                access.parents([serviceWindow, eventWindow])?[eventWindow] == serviceWindow else { return nil }
        }
        if let retained {
            guard retained.sheetID == sheetID, CFEqual(retained.sheet, sheet), retained.recipient.window == eventWindow,
                retained.recipient.remoteElement.map({ CFEqual($0, field) }) == true
            else { return nil }
        } else {
            guard Self.field(original, identifier: "saveAsNameTextField", access),
                access.read(original, kAXValueAttribute).value as? String == "", Self.caret(original, at: 0, access),
                let value = access.read(field, kAXValueAttribute).value as? String,
                value.utf16.elementsEqual(postedPrefix.utf16), Self.caret(field, at: postedPrefix.utf16.count, access)
            else { return nil }
        }
        guard let endpoint = try access.receiver.endpoint(field), endpoint.kind == .prohibitedApplication,
            endpoint.pid == service.pid
        else { return nil }
        // The native service and presenter stay fixed. The new field and its
        // sheet own the path proof rather than the old generic focus token.
        let next = NativeKeyboardRecipient(
            pid: service.pid, window: eventWindow, remoteElement: field, presenter: presenter.pid,
            process: service, axWindow: eventWindow, remoteRoute: recipient.remoteRoute,
            endpoint: endpoint, endpointProcess: service)
        let proof = Proof(original: original, panel: panel, sheet: sheet, panelID: panelID, sheetID: sheetID,
                          presenter: presenter, service: service, recipient: next, originalRecipient: recipient, access: access)
        try RequestDiagnostics.checkCancellation()
        if retained == nil {
            diagnose(["kind": "save_panel_path_receiver_proved", "presenterPID": presenter.pid,
                      "servicePID": service.pid, "originalWindow": panelID, "sheetWindow": sheetID,
                      "releasedPrefixUTF16": postedPrefix.utf16.count])
        }
        retained = proof
        return proof
    }

    private static func field(_ object: AXUIElement, identifier: String, _ access: Access) -> Bool {
        role(object, access) == kAXTextFieldRole && access.read(object, kAXIdentifierAttribute).value as? String == identifier
            && access.read(object, kAXEnabledAttribute).value as? Bool != false
    }

    private static func panel(_ object: AXUIElement, _ access: Access) -> Bool {
        role(object, access) == kAXWindowRole && access.read(object, kAXSubroleAttribute).value as? String == kAXDialogSubrole
            && access.read(object, kAXIdentifierAttribute).value as? String == "save-panel"
            && access.read(object, kAXModalAttribute).value as? Bool == true
    }

    private static func role(_ object: AXUIElement, _ access: Access) -> String? {
        access.read(object, kAXRoleAttribute).value as? String
    }

    private static func link(_ object: AXUIElement, _ attribute: String, to target: AXUIElement, _ access: Access) -> Bool {
        access.element(object, attribute).map { CFEqual($0, target) } == true
    }

    private static func children(_ object: AXUIElement, _ access: Access) -> [AXUIElement]? {
        guard let values = access.read(object, kAXChildrenAttribute).value as? [AXUIElement], values.count <= 16
        else { return nil }
        return values
    }

    private static func sheets(_ children: [AXUIElement], _ access: Access) -> [AXUIElement]? {
        var sheets = [AXUIElement]()
        for child in children {
            guard let role = role(child, access) else { return nil }
            if role == kAXSheetRole { sheets.append(child) }
        }
        return sheets
    }

    private static func caret(_ field: AXUIElement, at location: Int, _ access: Access) -> Bool {
        guard let raw = access.read(field, kAXSelectedTextRangeAttribute).value, CFGetTypeID(raw) == AXValueGetTypeID()
        else { return false }
        var range = CFRange()
        return AXValueGetValue(unsafeDowncast(raw, to: AXValue.self), .cfRange, &range)
            && range.location == location && range.length == 0
    }
}
