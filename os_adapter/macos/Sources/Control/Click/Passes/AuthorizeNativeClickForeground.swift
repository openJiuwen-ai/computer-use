import AppKit

/// QUERY: verify native operation evidence and host authority; never select a fallback.
@MainActor enum AuthorizeNativeClickForegroundPass {
    static func run(_ plan: NativeClickPlan) throws {
        guard plan.command.foreground else {
            throw NativePreparationUnavailable(
                description: "the selected HID click is no longer ready; foreground preparation was not selected")
        }
        try plan.operation.requireClickForeground(
            target: plan.command.target, within: plan.within, backgroundFamily: plan.offer.backgroundFamily,
            backgroundUnavailable: plan.offer.backgroundUnavailable,
            hostAllowed: ForegroundAssist.enabled || BackgroundTarget.isExclusive,
            exclusive: BackgroundTarget.isExclusive)
    }
}
