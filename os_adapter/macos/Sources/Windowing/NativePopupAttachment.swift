// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Query ports preserve unavailable facts. Complete children are required;
/// a truncated list is never positive containment evidence.
struct NativePopupFacts<Node, Process: Equatable> {
    let role: (Node) throws -> String?
    let subrole: (Node) throws -> String?
    let modal: (Node) throws -> Bool?
    let parent: (Node) throws -> Node?
    let children: (Node) throws -> [Node]?
    let windowID: (Node) throws -> UInt32?
    let process: () throws -> Process?
}

/// A direct nonmodal dialog/list surface can publish choices whose reverse AX
/// parent instead names a document control. These two independently retained
/// paths constrain identity; they grant no native action or pointer permission.
enum NativePopupAttachment {
    static func observe<Node, Process>(
        popup: Node, list: Node, facts: NativePopupFacts<Node, Process>, same: (Node, Node) -> Bool
    ) throws -> NativePopupOwnerStamp<Node, Process>? {
        guard try facts.role(popup) == "AXWindow", try facts.subrole(popup) == "AXDialog",
            try facts.modal(popup) == false, try facts.role(list) == "AXList", let children = try facts.children(popup),
            children.count == 1, same(children[0], list), let popupID = try facts.windowID(popup), popupID != 0,
            let listID = try facts.windowID(list), listID != 0, listID != popupID, let process = try facts.process()
        else { return nil }
        var reverse = [Node](), roles = [String](), current = list
        for _ in 0..<12 {
            guard !reverse.contains(where: { same($0, current) }), let role = try facts.role(current), !role.isEmpty,
                role != "AXUnknown"
            else { return nil }
            reverse.append(current)
            roles.append(role)
            if role == "AXWindow" {
                guard !same(current, popup), try facts.subrole(current) == "AXStandardWindow",
                    try facts.modal(current) == false, try facts.windowID(current) == listID,
                    try facts.process() == process
                else { return nil }
                return .init(
                    popup: popup, list: list, reverse: reverse, roles: roles, popupWindowID: popupID,
                    ownerWindowID: listID, process: process)
            }
            guard !["AXSheet", "AXPopover", "AXMenuBar", "AXMenuBarItem", "AXApplication"].contains(role),
                let parent = try facts.parent(current)
            else { return nil }
            current = parent
        }
        return nil
    }

    static func confirms<Node, Process>(
        _ stamp: NativePopupOwnerStamp<Node, Process>, leaf: Node, ancestors: [Node], popup: Node, currentOwner: Node,
        facts: NativePopupFacts<Node, Process>, same: (Node, Node) -> Bool
    ) throws -> Bool {
        guard same(stamp.reverse[0], stamp.list), ancestors.count == 2, same(ancestors[0], stamp.popup),
            same(ancestors[1], stamp.list), same(popup, stamp.popup), same(currentOwner, stamp.owner),
            try facts.process() == stamp.process, try facts.role(popup) == "AXWindow",
            try facts.subrole(popup) == "AXDialog", try facts.modal(popup) == false,
            try facts.role(leaf) == "AXStaticText", try facts.windowID(popup) == stamp.popupWindowID,
            try facts.windowID(stamp.list) == stamp.ownerWindowID, try facts.windowID(leaf) == stamp.ownerWindowID,
            try facts.windowID(currentOwner) == stamp.ownerWindowID,
            try facts.subrole(currentOwner) == "AXStandardWindow", try facts.modal(currentOwner) == false,
            let parent = try facts.parent(leaf), same(parent, stamp.list),
            let popupChildren = try facts.children(popup), popupChildren.count == 1, same(popupChildren[0], stamp.list),
            let choices = try facts.children(stamp.list), choices.contains(where: { same($0, leaf) })
        else { return false }
        for index in stamp.reverse.indices {
            guard try facts.role(stamp.reverse[index]) == stamp.roles[index] else { return false }
            if index + 1 < stamp.reverse.count {
                guard let parent = try facts.parent(stamp.reverse[index]), same(parent, stamp.reverse[index + 1]) else {
                    return false
                }
            }
        }
        return true
    }
}
