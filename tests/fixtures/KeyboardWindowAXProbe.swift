// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices
import Darwin

/// Query only: report the original owned window's real accessibility facts.
@main @MainActor enum KeyboardWindowAXProbe {
    typealias WindowID = @convention(c) (AXUIElement, UnsafeMutablePointer<UInt32>) -> Int32
    static let library = dlopen(
        "/System/Library/Frameworks/ApplicationServices.framework/Frameworks/HIServices.framework/HIServices",
        RTLD_LAZY | RTLD_LOCAL)
    static let windowID: WindowID? = library.flatMap { dlsym($0, "_AXUIElementGetWindow") }.map {
        unsafeBitCast($0, to: WindowID.self)
    }

    static func nativeID(_ element: AXUIElement) -> UInt32? {
        var value: UInt32 = 0
        return windowID?(element, &value) == 0 && value != 0 ? value : nil
    }

    static func read(_ element: AXUIElement, _ key: String) -> (AXError, CFTypeRef?) {
        var value: CFTypeRef?
        let status = AXUIElementCopyAttributeValue(element, key as CFString, &value)
        return (status, value)
    }

    static func boolean(_ element: AXUIElement, _ key: String) -> [String: Any] {
        let (status, raw) = read(element, key)
        let value: Bool? = raw.flatMap { CFGetTypeID($0) == CFBooleanGetTypeID() ? ($0 as? NSNumber)?.boolValue : nil }
        return ["status": status.rawValue, "value": value as Any? ?? NSNull()]
    }

    static func writable(_ element: AXUIElement, _ key: String) -> [String: Any] {
        var value = DarwinBoolean(false)
        let status = AXUIElementIsAttributeSettable(element, key as CFString, &value)
        return ["status": status.rawValue, "value": status == .success ? value.boolValue as Any : NSNull()]
    }

    static func relation(_ key: String, application: AXUIElement, original: AXUIElement) -> [String: Any] {
        let (status, raw) = read(application, key)
        guard let raw, CFGetTypeID(raw) == AXUIElementGetTypeID() else {
            return ["status": status.rawValue, "sameOriginal": NSNull(), "windowID": NSNull()]
        }
        let value = unsafeDowncast(raw, to: AXUIElement.self)
        return [
            "status": status.rawValue, "sameOriginal": CFEqual(value, original),
            "windowID": nativeID(value) as Any? ?? NSNull()
        ]
    }

    static func main() throws {
        guard CommandLine.arguments.count == 3, let pid = pid_t(CommandLine.arguments[1]),
            let expected = UInt32(CommandLine.arguments[2])
        else { exit(2) }
        let application = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(application, 0.2)
        let (status, raw) = read(application, kAXWindowsAttribute)
        let candidates = (raw as? [AXUIElement] ?? []).filter { nativeID($0) == expected }
        guard status == .success, candidates.count == 1 else {
            let result: [String: Any] = [
                "setup": "original window unresolved", "status": status.rawValue, "matches": candidates.count
            ]
            print(String(data: try JSONSerialization.data(withJSONObject: result), encoding: .utf8)!)
            exit(2)
        }
        let window = candidates[0]
        let front = NSWorkspace.shared.frontmostApplication?.processIdentifier
        let focused = boolean(window, kAXFocusedAttribute), main = boolean(window, kAXMainAttribute)
        let windowWritable = writable(window, kAXFocusedAttribute)
        let appWritable = writable(application, kAXFocusedWindowAttribute)
        let mainWindow = relation(kAXMainWindowAttribute, application: application, original: window)
        let focusedWindow = relation(kAXFocusedWindowAttribute, application: application, original: window)
        let role = read(window, kAXRoleAttribute).1 as? String
        let subrole = read(window, kAXSubroleAttribute).1 as? String
        let predicate =
            front != nil && front != pid && role == kAXWindowRole && subrole == kAXStandardWindowSubrole
            && focused["value"] as? Bool == false && main["value"] as? Bool == true
            && windowWritable["value"] as? Bool == false && appWritable["value"] as? Bool == false
            && mainWindow["sameOriginal"] as? Bool == true && focusedWindow["sameOriginal"] as? Bool == true
        let result: [String: Any] = [
            "pid": pid, "originalWindowID": expected, "foregroundPID": front as Any? ?? NSNull(),
            "role": role as Any? ?? NSNull(), "subrole": subrole as Any? ?? NSNull(), "AXFocused": focused,
            "AXMain": main, "windowFocusWritable": windowWritable, "appFocusWritable": appWritable,
            "mainWindow": mainWindow, "focusedWindow": focusedWindow,
            "appFrontmost": boolean(application, kAXFrontmostAttribute), "predicateMatches": predicate,
            "queryOnly": true
        ]
        print(
            String(data: try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]), encoding: .utf8)!)
    }
}
