// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

@main enum NativeToggleClickPolicyTests {
    static func main() {
        let action = NativeClickContract(
            routeFamily: "macos.axAction.AXPress", delivery: .objectAction, admission: .ready, reason: nil,
            preparationOptions: [])
        for role in ["AXCheckBox", "AXRadioButton"] {
            // No provider/class identifier participates in this boundary. Both
            // standard and custom implementations may have state-only AXPress.
            for admission in [NativeClickContract.Admission.ready, .preparationRequired] {
                let original = NativeClickContract(
                    routeFamily: action.routeFamily, delivery: .objectAction, admission: admission, reason: nil,
                    preparationOptions: admission == .ready ? [] : ["background", "foreground"],
                    backgroundFamily: action.routeFamily)
                let offer = NativeToggleClickPolicy.actionOffer(original, action: "AXPress", role: role)
                precondition(offer.admission == .insufficientEvidence && offer.preparationOptions.isEmpty)
                precondition(offer.routeFamily == action.routeFamily && offer.delivery == action.delivery)
                precondition(offer.reason?.contains("unconfirmed") == true)
                precondition(offer.backgroundFamily == nil)
            }
            for admission in [NativeClickContract.Admission.unavailable, .insufficientEvidence] {
                let original = NativeClickContract(
                    routeFamily: action.routeFamily, delivery: .objectAction, admission: admission,
                    reason: "The selected native action is disabled.", preparationOptions: [])
                precondition(NativeToggleClickPolicy.actionOffer(original, action: "AXPress", role: role) == original,
                             "a known blocker must retain its specific evidence")
            }
            // An unrelated declared action keeps its own semantics. The policy
            // is only applied to automatic click offers, never explicit AXPress.
            precondition(NativeToggleClickPolicy.actionOffer(action, action: "CustomAction", role: role) == action)
            for delivery in [NativeClickDelivery.windowDirected, .systemPointer, .objectSelection] {
                let original = NativeClickContract(
                    routeFamily: "independent", delivery: delivery, admission: .ready, reason: nil,
                    preparationOptions: [])
                precondition(NativeToggleClickPolicy.actionOffer(original, action: "AXPress", role: role) == original)
            }
        }
        for role: String? in [nil, "AXButton", "AXMenuButton", "AXStaticText", "AXRow"] {
            precondition(NativeToggleClickPolicy.actionOffer(action, action: "AXPress", role: role) == action)
        }
        print("Toggle full-click equivalence regressions passed")
    }
}
