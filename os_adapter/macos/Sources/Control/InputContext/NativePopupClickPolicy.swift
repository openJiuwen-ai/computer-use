// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// A declared action on interactive text inside a conflicting popup list does
/// not prove click/activation semantics. Ordinary controls keep their routes.
enum NativePopupClickPolicy {
    static func actionOffer(_ original: NativeClickContract, requiresPointer: Bool) -> NativeClickContract {
        guard requiresPointer else { return original }
        return .init(
            routeFamily: original.routeFamily, delivery: original.delivery, admission: .insufficientEvidence,
            reason: "The declared popup text action is not confirmed as a choice click.", preparationOptions: [])
    }

    static func pointerOffer(_ proof: NativePopupPointerQualification?) -> NativeClickContract {
        var offer = NativeClickContract(
            routeFamily: "macos.hid.v1", delivery: .systemPointer,
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
        guard let context, same(context.popup, window), currentWindowID == context.ownerWindowID,
            currentWindowID != context.popupWindowID, role == "AXStaticText", enabled == true, focusWritable,
            let actions, actions.count == 1, actions[0].name == "AXPress"
        else { return false }
        return true
    }
}
