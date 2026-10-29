// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices

/// One query's borrowed deadline/cancellation boundary. No cache or input.
@MainActor struct NativePopupAXFacts {
    let pid: pid_t
    let until: TimeInterval
    let check: () throws -> Void

    private func prepare(_ element: AXUIElement) throws -> Bool {
        try check()
        let remaining = until - ProcessInfo.processInfo.systemUptime
        guard remaining > 0 else {
            try check()
            return false
        }
        AXUIElementSetMessagingTimeout(element, Float(min(Double(AXMessaging.timeoutSeconds), remaining)))
        var actual: pid_t = 0
        return AXUIElementGetPid(element, &actual) == .success && actual == pid
    }
    private func value(_ element: AXUIElement, _ key: String) throws -> CFTypeRef? {
        guard try prepare(element) else { return nil }
        let result = AXAccess.read(element, key).value
        try check()
        return result
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
                return try prepare(parent) ? parent : nil
            },
            children: { element in
                guard try prepare(element) else { return nil }
                var count: CFIndex = 0, raw: CFArray?
                guard AXUIElementGetAttributeValueCount(element, kAXChildrenAttribute as CFString, &count) == .success,
                    count >= 0, count <= 2048
                else { return nil }
                try check()
                guard
                    AXUIElementCopyAttributeValues(element, kAXChildrenAttribute as CFString, 0, count, &raw)
                        == .success, let children = raw as? [AXUIElement], children.count == count
                else { return nil }
                try check()
                return children
            },
            windowID: { element in
                guard try prepare(element) else { return nil }
                let result = BackgroundEvents.nativeWindowID(element)
                try check()
                return result
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
