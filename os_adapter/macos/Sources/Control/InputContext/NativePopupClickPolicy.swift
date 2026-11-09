// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// A conflicting reverse popup owner needs the original visible point. Ordinary
/// controls and menu items without that retained provenance keep their routes.
enum NativePopupClickPolicy {
    static func actionOffer(_ original: NativeClickContract, requiresPointer: Bool) -> NativeClickContract {
        guard requiresPointer else { return original }
        return .init(
            routeFamily: original.routeFamily, delivery: original.delivery, admission: .insufficientEvidence,
            reason: "The declared popup action does not confirm a click within its observed input surface.",
            preparationOptions: [])
    }

    static func pointerOffer(_ proof: NativePopupPointerQualification?) -> NativeClickContract {
        var offer = NativeClickContract(
            routeFamily: "macos.windowDirected.v1", delivery: .windowDirected,
            admission: proof == nil ? .insufficientEvidence : .ready,
            reason: proof == nil ? "The original popup choice point is not physically confirmed." : nil,
            preparationOptions: [])
        offer.popupPointer = proof
        return offer
    }

    static func permitsIntent(_ intent: NativeIntent) -> Bool {
        intent.kind == "click" && (intent.parameters["button"] as? String ?? "left") == "left"
            && (intent.parameters["count"] as? Int ?? 1) == 1
    }

    static func requiresPointer<Node, Process>(
        context: NativePopupOwnerStamp<Node, Process>?, window: Node, currentWindowID: UInt32?, role: String?,
        enabled: Bool?, focusWritable: Bool, actions: [NativeAction.Item]?, same: (Node, Node) -> Bool
    ) -> Bool {
        guard let context, same(context.popup, window) else { return false }
        if context.kind == .submenu {
            // Fresh loss of the original reverse owner cannot move this retained
            // choice onto the ordinary action route.
            return role == "AXMenuItem"
        }
        guard currentWindowID == context.ownerWindowID, currentWindowID != context.popupWindowID,
            role == "AXStaticText", enabled == true, focusWritable,
            let actions, actions.count == 1, actions[0].name == "AXPress"
        else { return false }
        return true
    }
}
