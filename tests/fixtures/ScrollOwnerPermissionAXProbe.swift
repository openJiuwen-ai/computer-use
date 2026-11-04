// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation

/// Read-only native provider evidence. This executable has no AX mutation or
/// input API; it records the exact original scrolling objects and their links.
@main enum ScrollOwnerPermissionAXProbe {
    static func read(_ element: AXUIElement, _ key: String) -> (AXError, CFTypeRef?) {
        var value: CFTypeRef?
        let status = AXUIElementCopyAttributeValue(element, key as CFString, &value)
        return (status, value)
    }
    static func identifier(_ element: AXUIElement) -> String? { read(element, kAXIdentifierAttribute).1 as? String }
    static func related(_ element: AXUIElement, _ key: String) -> [AXUIElement] {
        let (status, value) = read(element, key)
        guard status == .success, let value else { return [] }
        if CFGetTypeID(value) == AXUIElementGetTypeID() { return [unsafeDowncast(value, to: AXUIElement.self)] }
        return value as? [AXUIElement] ?? []
    }
    static func main() throws {
        guard CommandLine.arguments.count == 2, let pid = pid_t(CommandLine.arguments[1]) else { exit(2) }
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 0.5)
        var queue = related(app, kAXWindowsAttribute), seen = [AXUIElement]()
        var found = [String: AXUIElement]()
        while !queue.isEmpty && seen.count < 300 {
            let item = queue.removeFirst()
            if seen.contains(where: { CFEqual($0, item) }) { continue }
            seen.append(item)
            if let key = identifier(item), key.hasPrefix("scroll-permission-") { found[key] = item }
            queue += related(item, kAXChildrenAttribute)
        }
        var objects = [String: [String: Any]]()
        for (key, item) in found {
            var value: [String: Any] = ["hash": CFHash(item)]
            for attribute in [kAXRoleAttribute, kAXSubroleAttribute, kAXOrientationAttribute] {
                let (status, raw) = read(item, attribute)
                value[attribute] = ["status": status.rawValue, "value": raw as? String as Any? ?? NSNull()]
            }
            let (status, enabled) = read(item, kAXEnabledAttribute)
            value["AXEnabled"] = ["status": status.rawValue, "value": enabled as? Bool as Any? ?? NSNull()]
            for attribute in [kAXParentAttribute, kAXChildrenAttribute, kAXVerticalScrollBarAttribute, kAXWindowAttribute] {
                value[attribute] = related(item, attribute).map { identifier($0) ?? "unlabelled native object" }
            }
            value["windowHashes"] = related(item, kAXWindowAttribute).map { CFHash($0) }
            var actions: CFArray?
            let actionStatus = AXUIElementCopyActionNames(item, &actions)
            value["actions"] = ["status": actionStatus.rawValue, "names": actions as? [String] ?? []]
            objects[key] = value
        }
        let result: [String: Any] = ["queryOnly": true, "pid": pid, "objects": objects, "visited": seen.count]
        print(String(data: try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]), encoding: .utf8)!)
    }
}
