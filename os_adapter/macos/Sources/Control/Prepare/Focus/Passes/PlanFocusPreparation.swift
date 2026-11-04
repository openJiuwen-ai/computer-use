// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import Foundation

/// PURE: preserve an established editor and notify only a missing window context.
enum PlanFocusPreparationPass {
    static func run(_ state: NativeFocusSnapshot, window: UInt32, needsKeyWindow: Bool = true) -> NativeFocusPlan {
        NativeFocusPlan(
            notify: !state.foreground
                && (state.reportedActive == false || (state.reportedActive == nil && !state.prepared)),
            // A missing transport receipt does not undo a positive native
            // receiver fact. Rewriting AXMain/AXFocused can activate the app.
            selectWindow: needsKeyWindow && state.focusedWindow != window)
    }
}
