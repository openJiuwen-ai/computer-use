// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation

/// Independent query-only process: no SDK, actions, setters or input events.
/// Sample the fixture's own state immediately around each AX getter.
@main enum MenuReadAXProbe {
    static func main() throws {
        guard CommandLine.arguments.count == 3, let pid = pid_t(CommandLine.arguments[1]) else { exit(2) }
        let stateFile = URL(fileURLWithPath: CommandLine.arguments[2]).appendingPathComponent("state.json")
        func state() throws -> [String: Any] {
            let value = try JSONSerialization.jsonObject(with: Data(contentsOf: stateFile)) as! [String: Any]
            guard value["pid"] as? Int32 == pid else { throw ProbeError("fixture PID changed") }
            let keys = ["pid", "menu", "delegateActions", "documentActions", "text", "guard", "active", "frontmostPID"]
            return Dictionary(uniqueKeysWithValues: keys.compactMap { key in value[key].map { (key, $0) } })
        }
        var calls = [[String: Any]]()
        func read(_ element: AXUIElement, _ attribute: String, at path: String) throws -> CFTypeRef? {
            let before = try state(), started = ProcessInfo.processInfo.systemUptime
            var raw: CFTypeRef?
            let status = AXUIElementCopyAttributeValue(element, attribute as CFString, &raw)
            var result: [String: Any] = [
                "api": "AXUIElementCopyAttributeValue", "path": path, "attribute": attribute,
                "status": status.rawValue, "before": before, "after": try state(),
                "seconds": ProcessInfo.processInfo.systemUptime - started
            ]
            if let string = raw as? String { result["string"] = string }
            if let raw, CFGetTypeID(raw) == CFBooleanGetTypeID() { result["boolean"] = raw as? Bool }
            if let values = raw as? [AXUIElement] { result["children"] = values.count }
            calls.append(result)
            return status == .success ? raw : nil
        }
        let initial = try state()
        var failure: String?
        do {
            let application = AXUIElementCreateApplication(pid)
            guard let raw = try read(application, kAXMenuBarAttribute, at: "application"),
                  CFGetTypeID(raw) == AXUIElementGetTypeID()
            else { throw ProbeError("menu bar unavailable") }
            let bar = unsafeDowncast(raw, to: AXUIElement.self)
            guard let items = try read(bar, kAXChildrenAttribute, at: "menu bar") as? [AXUIElement], items.count <= 64
            else { throw ProbeError("menu headings unavailable or over budget") }
            var matched = [AXUIElement]()
            for (index, item) in items.enumerated() {
                let title = try read(item, kAXTitleAttribute, at: "heading[\(index)]") as? String
                if title == "Commands" { matched.append(item) }
            }
            guard matched.count == 1 else { throw ProbeError("Commands match count \(matched.count)") }
            guard let menus = try read(matched[0], kAXChildrenAttribute, at: "Commands") as? [AXUIElement],
                  menus.count == 1
            else { throw ProbeError("Commands submenu unavailable or ambiguous") }
            guard let leaves = try read(menus[0], kAXChildrenAttribute, at: "Commands/menu") as? [AXUIElement],
                  leaves.count <= 64
            else { throw ProbeError("menu leaves unavailable or over budget") }
            for (index, item) in leaves.enumerated() {
                for attribute in [kAXRoleAttribute, kAXTitleAttribute, kAXEnabledAttribute] {
                    _ = try read(item, attribute, at: "Commands/leaf[\(index)]")
                }
            }
        } catch { failure = String(describing: error) }
        let output: [String: Any] = [
            "queryOnly": true, "pid": pid, "initial": initial, "calls": calls, "final": try state(),
            "failure": failure as Any? ?? NSNull()
        ]
        print(String(data: try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys]), encoding: .utf8)!)
        if failure != nil { exit(1) }
    }

    struct ProbeError: Error, CustomStringConvertible {
        let description: String
        init(_ description: String) { self.description = description }
    }
}
