// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

struct NativeInputScope {
    let wire: [String: Any]
    let application: NSRunningApplication
    let window: AXUIElement?
    let menuRoot: AXUIElement?
    let blocked: [String]
    struct WindowEvidence {
        let windows: [NativeApplicationAccess.Window]
        let focused: String?
    }
    /// One query's result, never a catalogue cache. Qualified popup checks use
    /// the same graph for fixed-root and original-owner modal boundaries.
    var windowEvidence: WindowEvidence? = nil

    func requireWindow() throws -> AXUIElement {
        guard menuRoot == nil, let window else {
            throw DriverError(
                "this input unit requires a window surface, not an application menu", code: .inputUnavailable)
        }
        return window
    }
}
