// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation

/// QUERY only: compare the original standard button and its current AX facts.
@main enum CommandIdentityAXProbe {
    static func read(_ element: AXUIElement, _ name: String) -> (AXError, CFTypeRef?) {
        var value: CFTypeRef?
        let status = AXUIElementCopyAttributeValue(element, name as CFString, &value)
        return (status, value)
    }

    static func relation(_ element: AXUIElement?, _ name: String) -> AXUIElement? {
        guard let element else { return nil }
        let (status, value) = read(element, name)
        guard status == .success, let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
        return unsafeDowncast(value, to: AXUIElement.self)
    }

    static func elements(_ element: AXUIElement, _ name: String) -> [AXUIElement] {
        let (status, value) = read(element, name)
        return status == .success ? value as? [AXUIElement] ?? [] : []
    }

    static func button(_ application: AXUIElement) -> AXUIElement? {
        var queue = elements(application, kAXWindowsAttribute)
        var seen = [AXUIElement]()
        while !queue.isEmpty && seen.count < 128 {
            let node = queue.removeFirst()
            if seen.contains(where: { CFEqual($0, node) }) { continue }
            seen.append(node)
            if read(node, kAXRoleAttribute).1 as? String == kAXButtonRole,
                read(node, kAXIdentifierAttribute).1 as? String != "other-command-control"
            { return node }
            queue += elements(node, kAXChildrenAttribute)
        }
        return nil
    }

    static func facts(_ element: AXUIElement?) -> [String: Any] {
        guard let element else { return ["available": false] }
        var result: [String: Any] = ["available": true, "cfHash": CFHash(element)]
        for key in [kAXRoleAttribute, kAXSubroleAttribute, kAXIdentifierAttribute,
                    kAXTitleAttribute, kAXDescriptionAttribute] {
            let (status, value) = read(element, key)
            result[key] = ["status": status.rawValue, "value": value as? String ?? NSNull()] as [String: Any]
        }
        var actions: CFArray?
        let status = AXUIElementCopyActionNames(element, &actions)
        result["actions"] = ["status": status.rawValue, "names": actions as? [String] ?? []] as [String: Any]
        return result
    }

    static func equal(_ original: AXUIElement?, _ current: AXUIElement?) -> Any {
        guard let original, let current else { return NSNull() }
        return CFEqual(original, current)
    }

    static func main() {
        guard CommandLine.arguments.count == 2, let pid = Int32(CommandLine.arguments[1]) else { return }
        let application = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(application, 0.5)
        var original: AXUIElement?, originalWindow: AXUIElement?, originalParent: AXUIElement?
        while let command = readLine() {
            let current = button(application)
            let window = relation(current, kAXWindowAttribute), parent = relation(current, kAXParentAttribute)
            if command == "capture" {
                original = current
                originalWindow = window
                originalParent = parent
            }
            let result: [String: Any] = [
                "trusted": AXIsProcessTrusted(), "command": command,
                "sameButton": equal(original, current), "sameWindow": equal(originalWindow, window),
                "sameParent": equal(originalParent, parent), "button": facts(current),
                "originalButton": facts(original)
            ]
            if var data = try? JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]) {
                data.append(0x0A)
                FileHandle.standardOutput.write(data)
            }
        }
    }
}
