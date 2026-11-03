// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation

/// Read-only AX comparison for the disposable stable-reference fixture. CF equality and the original AXRole status are reported separately from
/// the fixture's NSView object identity; reparenting a view can invalidate AX.
@main enum StableReferenceAXProbe {
    static func read(_ element: AXUIElement, _ name: String) -> (AXError, CFTypeRef?) {
        var value: CFTypeRef?
        let status = AXUIElementCopyAttributeValue(element, name as CFString, &value)
        return (status, value)
    }

    static func elements(_ element: AXUIElement, _ name: String) -> [AXUIElement] {
        let (status, raw) = read(element, name)
        return status == .success ? raw as? [AXUIElement] ?? [] : []
    }

    static func string(_ element: AXUIElement, _ name: String) -> String? {
        let (status, raw) = read(element, name)
        return status == .success ? raw as? String : nil
    }

    static func button(_ application: AXUIElement) -> AXUIElement? {
        var queue = elements(application, kAXWindowsAttribute)
        var seen = [AXUIElement]()
        while !queue.isEmpty && seen.count < 128 {
            let node = queue.removeFirst()
            if seen.contains(where: { CFEqual($0, node) }) { continue }
            seen.append(node)
            if string(node, kAXRoleAttribute) == kAXButtonRole,
               string(node, kAXIdentifierAttribute) == "stable-button" { return node }
            queue += elements(node, kAXChildrenAttribute)
        }
        return nil
    }

    static func wrapper(_ button: AXUIElement) -> AXUIElement? {
        var current = button
        for _ in 0..<12 {
            if string(current, kAXRoleAttribute) == kAXGroupRole { return current }
            let (status, parent) = read(current, kAXParentAttribute)
            guard status == .success, let parent, CFGetTypeID(parent) == AXUIElementGetTypeID() else { return nil }
            current = unsafeDowncast(parent, to: AXUIElement.self)
        }
        return nil
    }

    static func facts(_ element: AXUIElement?) -> [String: Any] {
        guard let element else { return ["available": false] }
        var result: [String: Any] = ["available": true, "cfHash": CFHash(element)]
        for key in [kAXRoleAttribute, kAXIdentifierAttribute, kAXTitleAttribute, kAXDescriptionAttribute,
                    kAXValueAttribute, kAXEnabledAttribute] {
            let (status, raw) = read(element, key)
            result[key] = ["status": status.rawValue, "value": (raw as? String) as Any? ??
                          (raw as? NSNumber) as Any? ?? NSNull()] as [String: Any]
        }
        return result
    }

    static func main() {
        guard CommandLine.arguments.count == 2, let pid = Int32(CommandLine.arguments[1]) else { return }
        let application = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(application, 0.5)
        var originalButton: AXUIElement?
        var originalWrapper: AXUIElement?
        while let command = readLine() {
            let currentButton = button(application)
            let currentWrapper = currentButton.flatMap(wrapper)
            if command == "capture" {
                originalButton = currentButton
                originalWrapper = currentWrapper
            }
            let sameButton = originalButton.flatMap { old in currentButton.map { CFEqual(old, $0) } }
            let sameWrapper = originalWrapper.flatMap { old in currentWrapper.map { CFEqual(old, $0) } }
            let result: [String: Any] = [
                "trusted": AXIsProcessTrusted(), "command": command,
                "sameButton": sameButton as Any? ?? NSNull(), "sameWrapper": sameWrapper as Any? ?? NSNull(),
                "button": facts(currentButton), "wrapper": facts(currentWrapper), "originalButton": facts(originalButton),
            ]
            if var data = try? JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]) {
                data.append(0x0A)
                FileHandle.standardOutput.write(data)
            }
        }
    }
}
