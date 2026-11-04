// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// Positive Qt menu-opener semantics, not a general AX action prerequisite.
/// Qt creates its native popup according to the process-local activity state;
/// an AXPress declaration by itself establishes neither that state nor this need.
struct NativeQtPopupOpener: Equatable {
    let role: String
    let identifier: String

    static func query(role: String?, identifier: String?, actions: [NativeAction.Item]?) -> Self? {
        guard let role, [kAXMenuButtonRole, kAXPopUpButtonRole, kAXComboBoxRole].contains(role),
            let identifier, NativeQtAccessibilityIdentifier.isWidgetPath(identifier),
            actions?.contains(where: { $0.name == kAXShowMenuAction }) == true
        else { return nil }
        return .init(role: role, identifier: identifier)
    }

    func applies(action: String) -> Bool { action == kAXPressAction || action == kAXShowMenuAction }

    static func requirePrepared(_ outcome: NativeApplicationContextUnit.Outcome) throws {
        // Unknown initial activity does not prove an inactive receiver. But a
        // positive inactive observation followed only by "posted" is not ready.
        guard outcome == .activityObserved || outcome == .skipped else {
            throw NativePreparationUnavailable(
                description: "the Qt popup opener remains locally inactive; requested action was not sent")
        }
    }
}
