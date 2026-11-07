// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// Qualify an application root whose Children represent application-level
/// content. The window catalogue's absence of a root is not this evidence.
@MainActor enum NativeApplicationContent {
    enum Evidence { case confirmed, absent, incomplete }

    static func array(
        _ element: AXUIElement, _ attribute: String, limit: Int, api: AXRelationReader.API = .init(),
        withinBudget: () throws -> Bool
    ) throws -> AXRead<[AXUIElement]> {
        try AXRelationReader(api: api).completeArray(element, property: attribute, limit: limit, withinBudget: withinBudget)
    }

    static func evidence(
        _ application: AXUIElement, pid: pid_t, containing: AXUIElement? = nil,
        role: (AXUIElement) -> String?,
        elements: (AXUIElement, String, Int) throws -> AXRead<[AXUIElement]>,
        owner: (AXUIElement) -> pid_t?, withinBudget: () throws -> Bool
    ) throws -> Evidence {
        let limit = 256
        guard try withinBudget(), case .value(let windows) = try elements(application, kAXWindowsAttribute, limit) else {
            return .incomplete
        }
        guard windows.isEmpty else { return .absent }
        let windowsAndSheets = [kAXWindowRole, kAXSheetRole, kAXDrawerRole, "AXPopover"]
        let menus = [kAXMenuBarRole, kAXMenuBarItemRole, kAXMenuRole, kAXMenuItemRole]
        var queue = [application], seen = [AXUIElement](), hasContent = false
        while let element = queue.popLast() {
            if seen.contains(where: { CFEqual($0, element) }) { continue }
            guard seen.count < limit, try withinBudget(), owner(element) == pid,
                let kind = role(element), !kind.isEmpty, kind != kAXUnknownRole else {
                return .incomplete
            }
            if CFEqual(element, application) {
                guard kind == kAXApplicationRole else { return .incomplete }
            } else if windowsAndSheets.contains(kind) || kind == kAXApplicationRole {
                return .absent
            } else if !menus.contains(kind) {
                hasContent = true
            }
            seen.append(element)
            guard try withinBudget() else { return .incomplete }
            switch try elements(element, kAXChildrenAttribute, limit - seen.count) {
            case .value(let children): queue.append(contentsOf: children)
            case .unsupported, .unavailable(.noValue): break
            default: return .incomplete
            }
        }
        guard hasContent else { return .absent }
        if let containing, !seen.contains(where: { CFEqual($0, containing) }) { return .absent }
        return .confirmed
    }
}
