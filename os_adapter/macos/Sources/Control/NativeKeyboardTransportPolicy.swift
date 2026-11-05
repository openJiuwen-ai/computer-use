// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/// PURE: separate semantic receiver readiness from delivery preparation.
enum NativeKeyboardTransportPolicy {
    enum Window: Equatable { case target, other, hidden, unavailable }

    enum WindowRead { case target, other, unsupported, noValue, cannotComplete, invalid, wrongType, failed }
    static func exactReceiverSubstitutesWindow(
        _ read: WindowRead, scope: Bool, editor: Bool, owner: Bool, locallyFocused: Bool
    ) -> Bool {
        guard scope && editor && owner && locallyFocused else { return false }
        switch read {
        case .unsupported, .noValue, .cannotComplete: return true
        default: return false
        }
    }

}
