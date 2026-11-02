// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation

/// Read-only AX comparison for the disposable row fixture. CF equality is
/// reported separately from the fixture's model-record identity.
@main enum RowIdentityAXProbe {
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

    static func field(_ application: AXUIElement) -> AXUIElement? {
        var queue = elements(application, kAXWindowsAttribute)
        var seen = [AXUIElement]()
        while !queue.isEmpty && seen.count < 128 {
            let node = queue.removeFirst()
            if seen.contains(where: { CFEqual($0, node) }) { continue }
            seen.append(node)
            if string(node, kAXIdentifierAttribute) == "row-field-control" { return node }
            queue += elements(node, kAXChildrenAttribute)
        }
        return nil
    }

    static func row(_ field: AXUIElement) -> AXUIElement? {
        var current = field
        for _ in 0..<12 {
            if string(current, kAXRoleAttribute) == kAXRowRole { return current }
            let (status, parent) = read(current, kAXParentAttribute)
            guard status == .success, let parent, CFGetTypeID(parent) == AXUIElementGetTypeID() else { return nil }
            current = unsafeDowncast(parent, to: AXUIElement.self)
        }
        return nil
    }

    static func facts(_ element: AXUIElement?) -> [String: Any] {
        guard let element else { return ["available": false] }
        var result: [String: Any] = ["available": true, "cfHash": CFHash(element)]
        for key in [kAXRoleAttribute, kAXIdentifierAttribute, kAXTitleAttribute, kAXDescriptionAttribute, kAXValueAttribute] {
            let (status, raw) = read(element, key)
            result[key] = ["status": status.rawValue, "value": raw as? String ?? NSNull()] as [String: Any]
        }
        return result
    }

    static func main() {
        guard CommandLine.arguments.count == 2, let pid = Int32(CommandLine.arguments[1]) else { return }
        let application = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(application, 0.5)
        var originalField: AXUIElement?
        var originalRow: AXUIElement?
        while let command = readLine() {
            let currentField = field(application)
            let currentRow = currentField.flatMap(row)
            if command == "capture" {
                originalField = currentField
                originalRow = currentRow
            }
            let sameField = originalField.flatMap { old in currentField.map { CFEqual(old, $0) } }
            let sameRow = originalRow.flatMap { old in currentRow.map { CFEqual(old, $0) } }
            let result: [String: Any] = [
                "trusted": AXIsProcessTrusted(), "command": command,
                "sameField": sameField as Any? ?? NSNull(), "sameRow": sameRow as Any? ?? NSNull(),
                "field": facts(currentField), "row": facts(currentRow), "originalField": facts(originalField),
            ]
            if var data = try? JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]) {
                data.append(0x0A)
                FileHandle.standardOutput.write(data)
            }
        }
    }
}
