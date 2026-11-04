// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices

/// One known preparation loss inside an already selected addressed gesture.
/// This marker is produced only after the original scope/recipient/geometry
/// checks have succeeded. It grants no new target, route, or requested input.
enum NativePointerActivityRecovery {
    struct Missing: ClassifiedFailure, CustomStringConvertible {
        let window: CGWindowID
        var code: FailureCode { .inputUnavailable }
        var description: String { "background pointer local activity was lost before mouse-down" }
    }

    static func missing(
        window: CGWindowID, kind: NativePointerIntent.Kind, delivery: NativeClickDelivery,
        requirement: NativePointerReadiness.Requirement, remote: Bool,
        initialReady: Bool, initialFocus: NativePointerReadiness.WindowFocus,
        initialForeground: Bool, initialPrepared: Bool?,
        currentReady: Bool, currentForeground: Bool, windowKind: NativePointerReadiness.WindowKind,
        currentFocus: NativePointerReadiness.WindowFocus, currentActive: Bool?
    ) -> Missing? {
        guard kind == .click, delivery == .windowDirected, requirement == .contentReceiver, !remote,
            initialReady, initialFocus == .target, initialForeground || initialPrepared == true,
            !currentReady, !currentForeground, windowKind == .keyRequiring, currentActive == false,
            currentFocus == .target || currentFocus == .absent
        else { return nil }
        return Missing(window: window)
    }
}
