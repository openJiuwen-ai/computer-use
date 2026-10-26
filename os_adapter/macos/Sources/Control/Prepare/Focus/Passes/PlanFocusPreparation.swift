import Foundation

/// PURE: preserve an established editor and notify only a missing window context.
enum PlanFocusPreparationPass {
    static func run(_ state: NativeFocusSnapshot, window: UInt32) -> NativeFocusPlan {
        NativeFocusPlan(
            notify: !state.foreground
                && (state.reportedActive == false || (state.reportedActive == nil && !state.prepared)),
            selectWindow: !state.prepared || state.focusedWindow != window)
    }
}
