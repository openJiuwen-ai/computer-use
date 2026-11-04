// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// Qt's Cocoa bridge maps check/radio AXPress to toggle(), which changes state
/// without the clicked signal delivered by a mouse click. Default click must
/// retain its complete activation semantics; explicit AX actions are separate.
enum NativeToggleClickPolicy {
    static func stateOnlyPress(role: String?, identifier: String?, currentWindowPath: Bool) -> Bool {
        currentWindowPath && ["AXCheckBox", "AXRadioButton"].contains(role ?? "")
            && NativeQtAccessibilityIdentifier.isWidgetPath(identifier)
    }

    static func actionOffer(
        _ original: NativeClickContract, action: String, stateOnlyPress: Bool
    ) -> NativeClickContract {
        guard action == "AXPress", stateOnlyPress, original.delivery == .objectAction else { return original }
        return .init(
            routeFamily: original.routeFamily, delivery: original.delivery, admission: .unavailable,
            reason: "This provider's AXPress only changes the checked state; a mouse click supplies full activation.",
            preparationOptions: [])
    }
}
