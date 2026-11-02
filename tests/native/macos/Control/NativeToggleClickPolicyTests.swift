// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

@main enum NativeToggleClickPolicyTests {
    static let checkbox = "QApplication.Dialog.QWidget.settings.CheckBox"
    static let radio = "QApplication.Dialog.QWidget.settings.radio_widget.0"

    static func main() {
        for identifier in [checkbox, radio, "QApplication.Dialog.qt_scrollarea_viewport.custom_toggle"] {
            precondition(NativeQtAccessibilityIdentifier.isWidgetPath(identifier))
        }
        for identifier: String? in [
            nil, "", "QApplication", "QApplication.CustomView", "QApplication.CustomView.control",
            "Other.QApplication.QWidget.check", "OtherApplication.QWidget.check", "QApplication..QWidget.check",
            "QApplication.QWidget.check.", "QApplication.CustomQWidget.check"
        ] {
            precondition(!NativeQtAccessibilityIdentifier.isWidgetPath(identifier))
        }

        let action = NativeClickContract(
            routeFamily: "macos.axAction.AXPress", delivery: .objectAction, admission: .ready, reason: nil,
            preparationOptions: [])
        for role in ["AXCheckBox", "AXRadioButton"] {
            let stateOnly = NativeToggleClickPolicy.stateOnlyPress(
                role: role, identifier: checkbox, currentWindowPath: true)
            precondition(stateOnly)
            let offer = NativeToggleClickPolicy.actionOffer(action, action: "AXPress", stateOnlyPress: stateOnly)
            precondition(offer.admission == .unavailable && offer.preparationOptions.isEmpty)
            precondition(offer.routeFamily == action.routeFamily && offer.delivery == action.delivery)
            // An unrelated declared action keeps its own semantics. The policy
            // is only applied to automatic click offers, never explicit AXPress.
            precondition(
                NativeToggleClickPolicy.actionOffer(action, action: "CustomAction", stateOnlyPress: true) == action)
        }
        for role: String? in [nil, "AXButton", "AXMenuButton", "AXStaticText", "AXRow"] {
            precondition(!NativeToggleClickPolicy.stateOnlyPress(
                role: role, identifier: checkbox, currentWindowPath: true))
        }
        precondition(!NativeToggleClickPolicy.stateOnlyPress(
            role: "AXCheckBox", identifier: checkbox, currentWindowPath: false))
        precondition(!NativeToggleClickPolicy.stateOnlyPress(
            role: "AXCheckBox", identifier: "native-control", currentWindowPath: true))
        precondition(NativeToggleClickPolicy.actionOffer(action, action: "AXPress", stateOnlyPress: false) == action)

        print("Qt toggle click policy regressions passed")
    }
}
