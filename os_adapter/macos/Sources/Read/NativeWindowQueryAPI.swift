// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// The existing collector's native query adapters. Tests replace these leaf
/// calls, not the collector, input-scope policy, or delivery loop.
@MainActor struct NativeWindowQueryAPI {
    var trusted: () -> Bool = { AXIsProcessTrusted() }
    var application: (pid_t) -> AXUIElement = { pid in
        let root = AXUIElementCreateApplication(pid)
        AXMessaging.configure(root)
        return root
    }
    var attribute: (AXUIElement, String) -> CFTypeRef?
    var read: (AXUIElement, String) -> AXRead<CFTypeRef> = { AXAccess.read($0, $1) }
    var element: (AXUIElement, String) -> AXUIElement? = { AXAccess.element($0, $1) }
    var pid: (AXUIElement) -> (AXError, pid_t) = { element in
        var pid: pid_t = 0
        let status = AXUIElementGetPid(element, &pid)
        return (status, pid)
    }
    var inputWindow: (pid_t, NativeWindowsReadObserver?, NativeWindowQueryTiming?) -> AXUIElement? = {
        WindowGeometry.inputWindow(pid: $0, onWindowsRead: $1, queryTiming: $2)
    }
    var presentedWindow: (AXUIElement, pid_t, NativeWindowQueryTiming?) -> AXUIElement = {
        WindowGeometry.presentedWindow($0, pid: $1, queryTiming: $2)
    }
    var hint: (AXUIElement, NativeWindowQueryTiming?) -> WindowSelection.Hint? = {
        WindowGeometry.hint($0, onWindowsRead: nil, queryTiming: $1)
    }
    var nativeID: (AXUIElement) -> CGWindowID? = { BackgroundEvents.nativeWindowID($0) }
    var relations = AXRelationReader.API()
}
