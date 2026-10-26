/// PURE: separate semantic receiver readiness from delivery preparation.
enum NativeKeyboardTransportPolicy {
    enum Window: Equatable { case target, other, hidden, unavailable }
    enum Decision: Equatable { case process, scopedForeground, foregroundPreparation, unavailable }

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

    static func select(
        usesText: Bool, interpreter: NativeKeyboardInterpreterEvidence.Kind, applicationForeground: Bool, window: Window
    ) -> Decision {
        guard usesText else { return .process }
        switch interpreter {
        case .noObservedWeb, .remoteWeb: return .process
        case .observedWebOwnershipUnknown: return .unavailable
        case .localWeb:
            if !applicationForeground || window == .other || window == .hidden { return .foregroundPreparation }
            // Unknown is not known-other: scoped admission's finite read-only
            // gate must confirm before any preparation or requested input.
            return .scopedForeground
        }
    }
}
