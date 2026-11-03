// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Automatic check/radio clicks require full activation, not just a changed
/// checked value. Declaring AXPress does not establish that equivalence for
/// these roles: providers can implement it as state-only toggle(). Keep the
/// ordinary pointer offers independent; explicit AX actions bypass this policy.
enum NativeToggleClickPolicy {
    static func actionOffer(
        _ original: NativeClickContract, action: String, role: String?
    ) -> NativeClickContract {
        guard action == "AXPress", ["AXCheckBox", "AXRadioButton"].contains(role ?? ""),
            original.delivery == .objectAction,
            [.ready, .preparationRequired].contains(original.admission)
        else { return original }
        return .init(
            routeFamily: original.routeFamily, delivery: original.delivery, admission: .insufficientEvidence,
            reason: "Full click activation through AXPress is unconfirmed for this check or radio control; mouse routes remain independent.",
            preparationOptions: [])
    }
}
