// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// A selected local process recipe may restore the exact Element receiver
/// when the active original window temporarily reports itself as the focus.
@MainActor struct NativeReplacementWindowReceiver {
    let windowOnly: () throws -> Bool
    let canSetFocus: () -> Bool
    let focusOriginal: () throws -> Void
    let desktopUnchanged: () -> Bool

    static func isWindowOnly(_ state: NativeReceiverSnapshot, original: AXUIElement, window: AXUIElement) -> Bool {
        guard state.windowRoute == "yes", state.scopeRoute == "yes", state.foreground,
            state.relatedEditor == nil, state.rawLocalFocus != false, !state.focusReportAbsent,
            !CFEqual(original, window), let keyboardWindow = state.window, CFEqual(keyboardWindow, window),
            let focus = state.focus, let raw = state.rawFocus, let reported = state.reportedFocus
        else { return false }
        return CFEqual(focus, window) && CFEqual(raw, window) && CFEqual(reported, window)
            && state.rawFocusOwner.map({ CFEqual($0, window) }) != false
    }

    func prepare(ifUnchanged unchanged: () -> Bool) throws -> Bool {
        // Text and permission reads can run provider code. Recheck the
        // original foreground Window-only state after both queries.
        guard try windowOnly(), canSetFocus(), unchanged(), try windowOnly() else { return false }
        try focusOriginal()
        return true
    }
}
