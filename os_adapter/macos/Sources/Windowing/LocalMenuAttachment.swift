// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// A local popup may be a child of a document AX tree while AXWindow names
/// its separate popup surface. Only a live, exact forward path authorizes an
/// action. A choice list published by a menu/combobox has the same logical
/// attachment even when its pixels live in a separate non-modal popup.
enum LocalMenuAttachment {
    /// Addressed object actions can prove their logical menu boundary through
    /// the exact observed forward path even when AXWindow is unavailable. A
    /// pointer or keyboard route still needs its physical window membership.
    static func confirmsInput<Node>(
        leaf: Node, ancestors: [Node], window: Node, currentWindow: Node?, attachedMenuAction: Bool,
        same: (Node, Node) -> Bool, role: (Node) -> String?, children: (Node) throws -> [Node]
    ) rethrows -> Bool {
        if let currentWindow, same(currentWindow, window) { return true }
        guard attachedMenuAction else { return false }
        return try confirms(
            leaf: leaf, ancestors: ancestors, window: window, requireKnownRoles: currentWindow == nil,
            same: same, role: role, children: children)
    }

    static func confirms<Node>(
        leaf: Node, ancestors: [Node], window: Node, requireKnownRoles: Bool = false,
        same: (Node, Node) -> Bool, role: (Node) -> String?,
        children: (Node) throws -> [Node]
    ) rethrows -> Bool {
        guard let start = ancestors.lastIndex(where: { same($0, window) })
        else { return false }
        let path = Array(ancestors[start...]) + [leaf]
        let roles = path.map { role($0) ?? "" }
        // Missing physical membership needs positive evidence that no hidden
        // window/sheet/menu-bar boundary lies on this exact forward path. A
        // missing role is not evidence of a harmless wrapper. Keep the existing
        // separate-popup policy, and use this one role sample for every check.
        guard !requireKnownRoles || roles.allSatisfy({ !$0.isEmpty && $0 != "AXUnknown" }) else { return false }
        let menu = ["AXMenuItem", "AXMenu"].contains(roles.last ?? "") && roles.contains("AXMenu")
        let choices = roles.firstIndex(where: { ["AXMenuButton", "AXPopUpButton", "AXComboBox"].contains($0) }).map {
            roles.dropFirst($0 + 1).contains("AXList")
        } ?? false
        guard path.count <= 41, menu || choices,
            !roles.dropFirst().contains(where: {
                ["AXMenuBar", "AXMenuBarItem", "AXWindow", "AXSheet"].contains($0)
            })
        else { return false }
        return try ObservedPath.matches(path, root: window, same: same, children: children)
    }
}
