// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// Diagnostic preparation for a disposable, uniquely titled Qt fixture only.
/// The default posts production local activity. Explicit owned-only modes
/// establish an inactive local state, inspect, activate, or send one authorized
/// global mouse click. A posted notification is never proof of consumption.
@main enum QtPopupLocalActivityProbe {
    // Diagnostic-only direct WindowServer hit. A rectangle overlap or an
    // application-wide PID match cannot authorize the owned guard click.
    private typealias WindowID = @convention(c) (AXUIElement, UnsafeMutablePointer<CGWindowID>) -> Int32
    private typealias MainConnection = @convention(c) () -> UInt32
    private typealias FindWindow = @convention(c) (
        UInt32, UInt32, Int64, Int64, UnsafePointer<CGPoint>, UnsafeMutablePointer<CGPoint>,
        UnsafeMutablePointer<UInt32>, UnsafeMutablePointer<UInt32>
    ) -> Int32

    static func exactWindowHit(_ window: AXUIElement, at screenPoint: CGPoint) -> CGWindowID? {
        guard let accessibility = dlopen(
            "/System/Library/Frameworks/ApplicationServices.framework/Frameworks/HIServices.framework/HIServices",
            RTLD_LAZY | RTLD_LOCAL)
        else { return nil }
        defer { dlclose(accessibility) }
        guard let skyLight = dlopen(
            "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight", RTLD_LAZY | RTLD_LOCAL)
        else { return nil }
        defer { dlclose(skyLight) }
        guard let axWindow = dlsym(accessibility, "_AXUIElementGetWindow"),
            let connection = dlsym(skyLight, "CGSMainConnectionID"),
            let nativeHit = dlsym(skyLight, "SLSFindWindowAndOwner")
        else { return nil }
        var expected: CGWindowID = 0, found: UInt32 = 0, owner: UInt32 = 0
        var point = screenPoint, local = CGPoint.zero
        guard unsafeBitCast(axWindow, to: WindowID.self)(window, &expected) == 0, expected != 0,
            unsafeBitCast(nativeHit, to: FindWindow.self)(
                unsafeBitCast(connection, to: MainConnection.self)(), 0, 1, 0, &point, &local, &found, &owner) == 0,
            found == expected, owner != 0
        else { return nil }
        return expected
    }

    @MainActor static func main() throws {
        let args = CommandLine.arguments
        let modes = ["--inspect-owned", "--deactivate-owned-local", "--activate-owned-guard", "--global-click-owned-guard"]
        let mode = args.count > 1 && modes.contains(args[1]) ? args[1] : "localActivity"
        let values = Array(args.dropFirst(mode == "localActivity" ? 1 : 2))
        if mode == "--activate-owned-guard" || mode == "--global-click-owned-guard" {
            guard values.count == 2, values[1].hasPrefix("CU Popup AB Guard ") else {
                throw DriverError("guard mutations require this experiment's unique owned guard title", code: .invalidRequest)
            }
        }
        if mode == "localActivity" || mode == "--deactivate-owned-local" {
            guard values.count == 2, values[1].hasPrefix("CU Popup AB Target ") else {
                throw DriverError("local notifications require this experiment's unique owned target title", code: .invalidRequest)
            }
        }
        guard values.count == 2, let pid = pid_t(values[0]), pid > 0,
            let app = NSRunningApplication(processIdentifier: pid), !app.isTerminated
        else { throw DriverError("expected exact owned-fixture PID and window title", code: .invalidRequest) }
        let launch = app.launchDate
        let application = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(application, 0.1)
        var rawWindows: CFTypeRef?
        guard AXUIElementCopyAttributeValue(application, kAXWindowsAttribute as CFString, &rawWindows) == .success,
            let windows = rawWindows as? [AXUIElement]
        else { throw DriverError("owned fixture windows unavailable", code: .staleTarget) }
        let matches = windows.filter { window in
                var raw: CFTypeRef?
                return AXUIElementCopyAttributeValue(window, kAXTitleAttribute as CFString, &raw) == .success
                    && raw as? String == values[1]
            }
        guard matches.count == 1, let owned = matches.first
        else { throw DriverError("owned fixture title does not match this process", code: .staleTarget) }

        func validateOwned() throws {
            var owner: pid_t = 0, rawTitle: CFTypeRef?
            guard let current = NSRunningApplication(processIdentifier: pid), current.isEqual(app),
                current.launchDate == launch, !current.isTerminated,
                AXUIElementGetPid(owned, &owner) == .success, owner == pid,
                AXUIElementCopyAttributeValue(owned, kAXTitleAttribute as CFString, &rawTitle) == .success,
                rawTitle as? String == values[1]
            else { throw DriverError("the original owned fixture ended or changed", code: .staleTarget) }
        }

        func sample() -> [String: Any] {
            var active: CFTypeRef?
            let status = AXUIElementCopyAttributeValue(application, kAXFrontmostAttribute as CFString, &active)
            return [
                "monotonic": ProcessInfo.processInfo.systemUptime,
                "foregroundPID": NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? NSNull(),
                "reportedActive": active as? Bool as Any? ?? NSNull(), "reportedActiveStatus": status.rawValue,
                "processIsActive": app.isActive
            ]
        }
        let before = sample()
        try validateOwned()
        let posted = ProcessInfo.processInfo.systemUptime
        var detail: [String: Any] = [:]
        if mode == "localActivity" || mode == "--deactivate-owned-local" {
            let subtype: NSEvent.EventSubtype = mode == "localActivity" ? .applicationActivated : .applicationDeactivated
            let event: CGEvent?
            if mode == "localActivity" {
                event = SyntheticFocusEvents.applicationActivation()
            } else {
                event = NSEvent.otherEvent(
                    with: .appKitDefined, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: 0,
                    context: nil, subtype: subtype.rawValue, data1: 0, data2: 0)?.cgEvent
                event?.timestamp = DispatchTime.now().uptimeNanoseconds
            }
            guard let event else { throw DriverError("local activity notification unavailable", code: .inputUnavailable) }
            event.postToPid(pid)
            detail = ["transport": "process", "mechanism": mode == "localActivity"
                      ? "SyntheticFocusEvents.applicationActivation" : "AppKit applicationDeactivated",
                      "notificationPosted": true, "consumptionConfirmedByHelper": false,
                      "eventType": event.type.rawValue, "subtype": subtype.rawValue,
                      "interferenceTier": 2]
        } else if mode == "--activate-owned-guard" {
            detail = ["transport": "activation", "mechanism": "NSRunningApplication.activate(options:[])",
                      "accepted": app.activate(options: []), "interferenceTier": 2]
        } else if mode == "--global-click-owned-guard" {
            // The click itself may activate the guard. A successful activation
            // request is not a precondition, nor proof that the point is safe.
            let raiseStatus = AXUIElementPerformAction(owned, kAXRaiseAction as CFString)
            try validateOwned()
            var rawPosition: CFTypeRef?, rawSize: CFTypeRef?
            guard AXUIElementCopyAttributeValue(owned, kAXPositionAttribute as CFString, &rawPosition) == .success,
                AXUIElementCopyAttributeValue(owned, kAXSizeAttribute as CFString, &rawSize) == .success,
                let rawPosition, let rawSize, CFGetTypeID(rawPosition) == AXValueGetTypeID(),
                CFGetTypeID(rawSize) == AXValueGetTypeID()
            else { throw DriverError("owned guard frame unavailable", code: .inputUnavailable) }
            var position = CGPoint.zero, size = CGSize.zero
            guard AXValueGetValue(unsafeDowncast(rawPosition, to: AXValue.self), .cgPoint, &position),
                AXValueGetValue(unsafeDowncast(rawSize, to: AXValue.self), .cgSize, &size), size.width > 20, size.height > 20
            else { throw DriverError("owned guard frame invalid", code: .inputUnavailable) }
            let point = CGPoint(x: position.x + size.width / 2, y: position.y + size.height / 2)
            guard let ownedID = exactWindowHit(owned, at: point),
                let down = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left),
                let up = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left)
            else { throw DriverError("owned guard is not the native window at the click point", code: .inputUnavailable) }
            down.post(tap: .cghidEventTap)
            up.post(tap: .cghidEventTap)
            detail = ["transport": "globalHID", "mechanism": "one owned guard leftMouseDown/leftMouseUp",
                      "point": ["x": point.x, "y": point.y], "windowID": ownedID,
                      "hitSource": "windowServer", "raiseStatus": raiseStatus.rawValue,
                      "interferenceTier": 3]
        } else {
            detail = ["transport": "readOnly", "mechanism": "inspect owned process", "interferenceTier": 1]
        }
        // Diagnostic sampling only. The SDK operation is measured separately;
        // this gives the fixture's 30ms reporter time to publish the A/B state.
        let until = Date(timeIntervalSinceNow: 0.12)
        while Date() < until { RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.01)) }
        let after = sample()
        let netChange: String
        if let original = before["foregroundPID"] as? Int32, let current = after["foregroundPID"] as? Int32 {
            netChange = original == current ? "no" : "yes"
        } else { netChange = "unknown" }
        let result: [String: Any] = [
            "kind": "owned_fixture_local_activity_ab", "pid": pid, "title": values[1], "mode": mode,
            "postedAt": posted, "effect": detail,
            "before": before, "after": after,
            "globalForegroundChanged": netChange
        ]
        let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys, .prettyPrinted])
        try FileHandle.standardOutput.write(contentsOf: data)
        try FileHandle.standardOutput.write(contentsOf: Data([10]))
    }
}
