// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// Immutable observation provenance for one popup list. It records a logical
/// AX parent chain separately from the popup's forward children. It neither
/// claims reciprocal containment nor changes the App's fixed root.
struct NativePopupOwnerStamp<Node, Process: Equatable> {
    let popup: Node
    let list: Node
    let reverse: [Node]
    let roles: [String]
    let popupWindowID: UInt32
    let ownerWindowID: UInt32
    let process: Process

    init?(
        popup: Node, list: Node, reverse: [Node], roles: [String], popupWindowID: UInt32, ownerWindowID: UInt32,
        process: Process
    ) {
        guard reverse.count >= 2, reverse.count == roles.count, roles.first == "AXList", roles.last == "AXWindow",
            popupWindowID != 0, ownerWindowID != 0, popupWindowID != ownerWindowID
        else { return nil }
        self.popup = popup
        self.list = list
        self.reverse = reverse
        self.roles = roles
        self.popupWindowID = popupWindowID
        self.ownerWindowID = ownerWindowID
        self.process = process
    }

    var owner: Node { reverse.last! }

    func matches(_ other: Self, same: (Node, Node) -> Bool) -> Bool {
        process == other.process && popupWindowID == other.popupWindowID && ownerWindowID == other.ownerWindowID
            && roles == other.roles && same(popup, other.popup) && same(list, other.list)
            && reverse.count == other.reverse.count && zip(reverse, other.reverse).allSatisfy { same($0.0, $0.1) }
    }
}
