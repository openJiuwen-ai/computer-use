// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// Immutable observation provenance for one popup member. It records a logical
/// AX parent chain separately from the popup's forward children. It neither
/// claims reciprocal containment nor changes the App's fixed root.
struct NativePopupOwnerStamp<Node, Process: Equatable> {
    enum Kind { case list, submenu }

    let kind: Kind
    let popup: Node
    let member: Node
    let reverse: [Node]
    let roles: [String]
    let popupWindowID: UInt32
    let ownerWindowID: UInt32
    let process: Process

    init?(
        popup: Node, member: Node, reverse: [Node], roles: [String], popupWindowID: UInt32, ownerWindowID: UInt32,
        process: Process, kind: Kind = .list
    ) {
        guard reverse.count >= 2, reverse.count == roles.count, roles.last == "AXWindow",
            kind == .list ? roles.first == "AXList" : roles == ["AXMenuItem", "AXMenuItem", "AXWindow"],
            popupWindowID != 0, ownerWindowID != 0, popupWindowID != ownerWindowID
        else { return nil }
        self.kind = kind
        self.popup = popup
        self.member = member
        self.reverse = reverse
        self.roles = roles
        self.popupWindowID = popupWindowID
        self.ownerWindowID = ownerWindowID
        self.process = process
    }

    var owner: Node { reverse.last! }

    func matchesForwardPath(_ ancestors: [Node], same: (Node, Node) -> Bool) -> Bool {
        switch kind {
        case .list: return ancestors.count == 2 && same(ancestors[0], popup) && same(ancestors[1], member)
        case .submenu: return ancestors.count == 1 && same(ancestors[0], popup)
        }
    }

    func matches(_ other: Self, same: (Node, Node) -> Bool) -> Bool {
        kind == other.kind && process == other.process && popupWindowID == other.popupWindowID
            && ownerWindowID == other.ownerWindowID && roles == other.roles
            && same(popup, other.popup) && same(member, other.member)
            && reverse.count == other.reverse.count && zip(reverse, other.reverse).allSatisfy { same($0.0, $0.1) }
    }
}
