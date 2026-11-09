// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// Evidence only for optional restitution after a completed click. Absence of
/// a new physical window cannot prove that an inline menu is safe to dismiss.
enum NativeForegroundResult {
    enum Presentation { case present, absent, unknown }
    struct Window {
        let id: CGWindowID
        let role: String?
        let subrole: String?
        let modal: Bool?
    }
    enum Decision: String { case persistentResult, noTransientResult, transientResult, unknownResult }

    /// Inspect the exact opener/recipient, not its role as a prediction of an
    /// action's success. Static application menu commands are not open menus.
    @MainActor static func presentation(_ element: AXUIElement?, pid: pid_t) throws -> Presentation {
        guard let element else { return .unknown }
        var pending: AXUIElement? = element, seen = [AXUIElement]()
        while let node = pending, seen.count < 12, !seen.contains(where: { CFEqual($0, node) }) {
            try RequestDiagnostics.checkCancellation()
            seen.append(node)
            AXUIElementSetMessagingTimeout(node, 0.05)
            let value = AXAccess.read(node, kAXRoleAttribute)
            if case .invalid = value { return .absent }
            guard let role = value.value as? String else { return .unknown }
            if let expanded = AXAccess.read(node, kAXExpandedAttribute).value as? Bool {
                return expanded ? .present : .absent
            }
            if role == kAXMenuRole { return NativeMenuSurfaces.isPresentedPopup(node, pid: pid) ? .present : .absent }
            if [kAXMenuButtonRole, kAXPopUpButtonRole, kAXComboBoxRole, kAXMenuBarItemRole].contains(role) {
                guard let children = AXAccess.elements(node, kAXChildrenAttribute, limit: 64) else { return .unknown }
                for child in children {
                    try RequestDiagnostics.checkCancellation()
                    let childRole = AXAccess.read(child, kAXRoleAttribute).value as? String
                    if childRole == kAXMenuRole, NativeMenuSurfaces.isPresentedPopup(child, pid: pid) { return .present }
                    if childRole == "AXList", AXAccess.read(child, "AXVisible").value as? Bool != false { return .present }
                }
                return .absent
            }
            if [kAXWindowRole, kAXSheetRole, kAXApplicationRole].contains(role) { return .absent }
            pending = AXAccess.element(node, kAXParentAttribute)
        }
        return .unknown
    }

    static func persistentWindow(role: String?, subrole: String?, modal: Bool?) -> Bool {
        guard role == kAXWindowRole else { return false }
        return subrole == kAXStandardWindowSubrole
            || modal == true && [kAXDialogSubrole, kAXSystemDialogSubrole].contains(subrole ?? "")
    }

    static func decide(
        before: Set<CGWindowID>?, after: Set<CGWindowID>?, windows: [Window], knownTransient: Bool
    ) -> Decision {
        if knownTransient { return .transientResult }
        guard let before, let after else { return .unknownResult }
        let new = after.subtracting(before)
        guard !new.isEmpty else { return .noTransientResult }
        for id in new {
            let matches = windows.filter { $0.id == id }
            guard matches.count == 1, let value = matches.first else { return .unknownResult }
            if [kAXMenuRole, "AXPopover"].contains(value.role ?? "") { return .transientResult }
            guard persistentWindow(role: value.role, subrole: value.subrole, modal: value.modal) else { return .unknownResult }
        }
        return .persistentResult
    }
}
