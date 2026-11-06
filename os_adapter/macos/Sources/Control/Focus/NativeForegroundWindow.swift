// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// The exact window at a foreground claim. This never chooses an input target
/// or restores an editor/selection, and is not a desktop history service.
@MainActor struct NativeForegroundWindow {
    let element: AXUIElement
    let id: CGWindowID?

    func matches(_ other: Self?) -> Bool {
        guard let other else { return false }
        return CFEqual(element, other.element) || id != nil && id == other.id
    }

    static func focused(_ pid: pid_t) -> Self? {
        let root = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(root, 0.05)
        guard let element = AXAccess.element(root, kAXFocusedWindowAttribute) else { return nil }
        AXUIElementSetMessagingTimeout(element, 0.05)
        var owner: pid_t = 0
        guard AXUIElementGetPid(element, &owner) == .success, owner == pid else { return nil }
        return .init(element: element, id: BackgroundEvents.nativeWindowID(element))
    }

    func restore(_ application: NSRunningApplication, check: () throws -> Void,
                 onPreparation: (String) -> Void) throws -> Bool {
        if matches(Self.focused(application.processIdentifier)) { return false }
        try check()
        var pid: pid_t = 0
        guard AXUIElementGetPid(element, &pid) == .success, pid == application.processIdentifier,
            AXAccess.read(element, kAXRoleAttribute).value != nil else {
            throw DriverError("original foreground window is unavailable", code: .staleTarget)
        }
        var actions: CFArray?
        var invoked = false
        if AXUIElementCopyActionNames(element, &actions) == .success,
            (actions as? [String])?.contains(kAXRaiseAction) == true {
            try check()
            onPreparation("return original foreground window")
            let activity = NativeInputActivity.begin(device: .focus, transport: .accessibility,
                mechanism: "AXRaiseReturn", pid: pid, stage: .cleanup)
            let status = AXMessaging.performAction(element, kAXRaiseAction as CFString)
            activity?.returned(status: Int(status.rawValue))
            invoked = true
            if status == .invalidUIElement { throw DriverError("original foreground window ended", code: .staleTarget) }
        }
        try NativeDocumentContext(application: application, window: element).select(
            check: check, onPreparation: { detail in invoked = true; onPreparation(detail) }, onNativePreparation: { _ in })
        return invoked
    }
}
