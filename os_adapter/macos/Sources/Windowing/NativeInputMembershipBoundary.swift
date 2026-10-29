// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Final membership for ordinary routes. Logical popup provenance grants no
/// AX action or general physical permission here.
@MainActor enum NativeInputMembershipBoundary {
    static func validate<Node>(
        leaf: Node, ancestors: [Node], window: Node, currentWindow: Node?, attachedMenuAction: Bool,
        same: (Node, Node) -> Bool, role: (Node) -> String?, children: (Node) throws -> [Node]
    ) throws {
        if try LocalMenuAttachment.confirmsInput(
            leaf: leaf, ancestors: ancestors, window: window, currentWindow: currentWindow,
            attachedMenuAction: attachedMenuAction, same: same, role: role, children: children)
        {
            return
        }
        if currentWindow == nil {
            throw DriverError("native target's current window membership is unavailable", code: .inputUnavailable)
        }
        throw DriverError("native target moved outside its observed interaction surface", code: .staleTarget)
    }
}
