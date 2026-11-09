// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Darwin
import Foundation

/// Optional display data for one collection. Topology, geometry, identity and
/// input authority come from the ordinary per-object observation.
@MainActor struct AXSubtreePrefetch {
    typealias Capture = @MainActor (AXUIElement, [String], Int, TimeInterval) -> AXSubtreePrefetch?

    static let displayFields: Set<String> = [
        "AXRoleDescription", "AXTitle", "AXDescription", "AXHelp", "AXPlaceholderValue"
    ]
    static let nativeAPI = "AXUIElementCopyHierarchy"
    static let live: Capture? = {
        guard ProcessInfo.processInfo.environment["COMPUTER_USE_AX_SUBTREE_PREFETCH"] == "1",
            let runtime = Runtime()
        else { return nil }
        return runtime.capture
    }()

    var row: (AXUIElement, String) -> CFTypeRef?

    @MainActor private struct Runtime {
        typealias Copy = @convention(c) (
            AXUIElement, CFArray, CFDictionary?, UnsafeMutablePointer<Unmanaged<CFDictionary>?>?
        ) -> AXError

        let copy: Copy
        let keys: [String: CFString]

        init?() {
            let path = "/System/Library/Frameworks/ApplicationServices.framework/Versions/A/"
                + "Frameworks/HIServices.framework/Versions/A/HIServices"
            guard let library = dlopen(path, RTLD_LAZY | RTLD_LOCAL) else { return nil }
            var opened = false
            defer { if !opened { dlclose(library) } }
            guard let symbol = dlsym(library, AXSubtreePrefetch.nativeAPI) else { return nil }
            var keys: [String: CFString] = [:]
            for name in ["ArrayAttributes", "IncompleteResult", "MaxArrayCount", "MaxDepth", "ResultError",
                         "ResultValue", "ReturnAttributeErrors"] {
                guard let address = dlsym(library, "kAXUIElementCopyHierarchy\(name)Key"),
                    let key = address.assumingMemoryBound(to: Unmanaged<CFString>?.self).pointee
                else { return nil }
                keys[name] = key.takeUnretainedValue()
            }
            self.copy = unsafeBitCast(symbol, to: Copy.self)
            self.keys = keys
            // Keep the framework and its constants loaded while the closure is used.
            opened = true
        }

        func capture(
            _ root: AXUIElement, fields: [String], depth: Int, timeout: TimeInterval
        ) -> AXSubtreePrefetch? {
            guard AXUIElementSetMessagingTimeout(root, Float(timeout)) == .success else { return nil }
            defer { AXMessaging.configure(root) }
            let options: [String: Any] = [
                keys["ArrayAttributes"]! as String: ["AXChildren"],
                keys["MaxArrayCount"]! as String: 128,
                keys["MaxDepth"]! as String: depth,
                keys["ReturnAttributeErrors"]! as String: true
            ]
            var retained: Unmanaged<CFDictionary>?
            let status = copy(root, (fields + ["AXChildren"]) as CFArray, options as CFDictionary, &retained)
            guard let result = retained?.takeRetainedValue(), status == .success,
                CFDictionaryGetCount(result) <= 8192, Self.get(result, keys["IncompleteResult"]!) == nil,
                Self.get(result, root) != nil
            else { return nil }
            return AXSubtreePrefetch { element, field in
                guard let row = Self.dictionary(Self.get(result, element)),
                    Self.get(row, keys["IncompleteResult"]!) == nil,
                    let entry = Self.dictionary(Self.get(row, field as CFString)),
                    Self.get(entry, keys["IncompleteResult"]!) == nil,
                    Self.get(entry, keys["ResultError"]!) == nil
                else { return nil }
                return Self.get(entry, keys["ResultValue"]!)
            }
        }

        private static func get(_ dictionary: CFDictionary, _ key: CFTypeRef) -> CFTypeRef? {
            withExtendedLifetime(key) {
                CFDictionaryGetValue(dictionary, Unmanaged.passUnretained(key).toOpaque()).map {
                    Unmanaged<AnyObject>.fromOpaque($0).takeUnretainedValue()
                }
            }
        }

        private static func dictionary(_ value: CFTypeRef?) -> CFDictionary? {
            guard let value, CFGetTypeID(value) == CFDictionaryGetTypeID() else { return nil }
            return unsafeDowncast(value, to: CFDictionary.self)
        }
    }
}
