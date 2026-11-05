// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// Fresh local first-responder evidence can establish addressed delivery
/// without a historical synthetic-activation receipt. Window commands retain
/// their separate preparation contract; unknown and remote routes are unchanged.
enum NativeKeyboardReceiverReadiness {
    struct Facts {
        let windowRoute: String
        let scopeRoute: String
        let concreteFocus: Bool
        let focusedWindowMatches: Bool
        let focusOwnerMatches: Bool
        let locallyFocused: Bool?
        let actualPID: Int32?
        let presenterPID: Int32
        let nativeWindowKnown: Bool
    }

    static func confirmedLocal(_ facts: Facts) -> Bool {
        facts.windowRoute == "yes" && facts.scopeRoute == "yes" && facts.concreteFocus
            && facts.focusedWindowMatches && facts.focusOwnerMatches && facts.locallyFocused == true
            && facts.actualPID == facts.presenterPID && facts.nativeWindowKnown
    }
}
