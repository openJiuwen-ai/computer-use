// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Existing modal policy queried for the original logical owner, independently
/// from the same query-local scope catalogue. This type grants no input permission.
enum NativePopupOwnerBoundary {
    struct Window<Node> {
        let ref: String
        let element: Node
        var parent: String? = nil
        var modal: Bool? = false
    }
    static func blocked<Node>(
        popup: Node, owner: Node, windows: [Window<Node>], same: (Node, Node) -> Bool
    ) throws -> [String] {
        let owners = windows.filter { same($0.element, owner) }
        guard owners.count == 1 else {
            throw DriverError("the popup's original logical owner is unavailable", code: .staleTarget)
        }
        let original = owners[0]
        guard original.modal == false else {
            throw DriverError("the popup owner's modality is unavailable or changed", code: .inputUnavailable)
        }
        if let popupWindow = windows.first(where: { same($0.element, popup) }) {
            guard popupWindow.modal == false else {
                throw DriverError("popup modality is unavailable or changed", code: .inputUnavailable)
            }
            if let parent = popupWindow.parent, parent != original.ref {
                throw DriverError("popup has a conflicting window parent", code: .staleTarget)
            }
        }
        return try NativeWindowBoundary.blocked(
            root: original.ref, target: original.ref,
            windows: windows.map { .init(ref: $0.ref, parent: $0.parent, modal: $0.modal == true) })
    }
}
