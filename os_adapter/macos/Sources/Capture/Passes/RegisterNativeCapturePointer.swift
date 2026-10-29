// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// RESOURCE: publish pointer observation only after the original window capture completed.
@MainActor enum RegisterNativeCapturePointerPass {
    static func run(_ captured: NativeCapturedWindow, observe: ([String: Any]) -> Void) -> [String: Any] {
        observe(captured.binding)
        return captured.result
    }
}
