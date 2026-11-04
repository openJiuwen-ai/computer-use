// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One query's borrowed deadline/cancellation boundary. No cache or input.
@MainActor struct NativePopupAXFacts {
    let pid: pid_t
    let until: TimeInterval
    let check: () throws -> Void
    var adapter = Adapter()

    /// Per-query OS ports; no proxy registry or independent observation state.
    @MainActor struct Adapter {
        var timeout: (AXUIElement, Float) -> AXError = { AXUIElementSetMessagingTimeout($0, $1) }
        var pid: (AXUIElement) -> pid_t? = { element in
            var actual: pid_t = 0
            return AXUIElementGetPid(element, &actual) == .success ? actual : nil
        }
        var value: (AXUIElement, String) -> CFTypeRef? = { AXAccess.read($0, $1).value }
        var count: (AXUIElement) -> CFIndex? = { element in
            var count: CFIndex = 0
            return AXUIElementGetAttributeValueCount(element, kAXChildrenAttribute as CFString, &count) == .success
                ? count : nil
        }
        var children: (AXUIElement, CFIndex) -> [AXUIElement]? = { element, count in
            var raw: CFArray?
            guard AXUIElementCopyAttributeValues(element, kAXChildrenAttribute as CFString, 0, count, &raw) == .success
            else { return nil }
            return raw as? [AXUIElement]
        }
        var windowID: (AXUIElement) -> UInt32? = { BackgroundEvents.nativeWindowID($0) }
    }

    /// Timeout is mutable state on this exact retained AX proxy. Every borrowed
    /// query restores the ordinary project limit, including PID-only checks and
    /// cancellation. Cleanup performs no AX content query and cannot mask errors.
    private func scoped<Value>(_ element: AXUIElement, _ query: () throws -> Value?) throws -> Value? {
        try check()
        let remaining = until - ProcessInfo.processInfo.systemUptime
        guard remaining > 0 else {
            try check()
            return nil
        }
        _ = adapter.timeout(element, Float(min(Double(AXMessaging.timeoutSeconds), remaining)))
        defer { _ = adapter.timeout(element, AXMessaging.timeoutSeconds) }
        guard adapter.pid(element) == pid else { return nil }
        return try query()
    }
    private func value(_ element: AXUIElement, _ key: String) throws -> CFTypeRef? {
        try scoped(element) {
            let result = adapter.value(element, key)
            try check()
            return result
        }
    }
    func facts() -> NativePopupFacts<AXUIElement, NativeProcessIdentity> {
        .init(
            role: { try value($0, kAXRoleAttribute) as? String },
            subrole: { try value($0, kAXSubroleAttribute) as? String },
            modal: { try value($0, kAXModalAttribute) as? Bool },
            parent: { element in
                guard let raw = try value(element, kAXParentAttribute), CFGetTypeID(raw) == AXUIElementGetTypeID()
                else { return nil }
                let parent = unsafeDowncast(raw, to: AXUIElement.self)
                return try scoped(parent) { parent }
            },
            children: { element in
                try scoped(element) {
                    guard let count = adapter.count(element), count >= 0, count <= 2048 else { return nil }
                    try check()
                    guard let children = adapter.children(element, count), children.count == count else { return nil }
                    try check()
                    return children
                }
            },
            windowID: { element in
                try scoped(element) {
                    let result = adapter.windowID(element)
                    try check()
                    return result
                }
            },
            process: {
                try check()
                let result = NativeProcessIdentity.read(pid)
                try check()
                return result
            })
    }
}

/// Exhausting optional observation evidence cannot hide the original node.
struct NativePopupObservationUnavailable: Error {}
