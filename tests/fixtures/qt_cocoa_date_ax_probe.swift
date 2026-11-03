// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ApplicationServices
import Foundation
import Darwin

/// Query only: a bounded comparison against one disposable fixture's exact
/// process and unique window title. No activation, focus setter or input API.
@main enum QtCocoaDateAXProbe {
    struct Read {
        let status: AXError
        let raw: CFTypeRef?
        var json: [String: Any] {
            ["status": status.rawValue, "value": raw.map(decode) ?? NSNull()]
        }
    }

    static func decode(_ raw: CFTypeRef) -> Any {
        if CFGetTypeID(raw) == AXValueGetTypeID() {
            let value = unsafeDowncast(raw, to: AXValue.self)
            switch AXValueGetType(value) {
            case .cgPoint:
                var point = CGPoint.zero
                if AXValueGetValue(value, .cgPoint, &point) { return ["x": point.x, "y": point.y] }
            case .cgSize:
                var size = CGSize.zero
                if AXValueGetValue(value, .cgSize, &size) { return ["width": size.width, "height": size.height] }
            case .cgRect:
                var rect = CGRect.zero
                if AXValueGetValue(value, .cgRect, &rect) {
                    return ["x": rect.minX, "y": rect.minY, "width": rect.width, "height": rect.height]
                }
            case .cfRange:
                var range = CFRange()
                if AXValueGetValue(value, .cfRange, &range) {
                    return ["location": range.location, "length": range.length]
                }
            default: break
            }
            return ["nativeType": "AXValue", "valueType": AXValueGetType(value).rawValue]
        }
        if let value = raw as? String { return value }
        if let value = raw as? NSNumber { return value }
        if let value = raw as? [String] { return value }
        return ["nativeTypeID": CFGetTypeID(raw)]
    }

    static func read(_ element: AXUIElement, _ name: String) -> Read {
        var value: CFTypeRef?
        let status = AXUIElementCopyAttributeValue(element, name as CFString, &value)
        return Read(status: status, raw: value)
    }

    static func owned(_ element: AXUIElement, pid: pid_t) -> Bool {
        var owner: pid_t = 0
        return AXUIElementGetPid(element, &owner) == .success && owner == pid
    }

    static func children(_ element: AXUIElement, attribute: String, limit: Int) -> ([AXUIElement], [String: Any], Bool) {
        var count: CFIndex = 0
        let countStatus = AXUIElementGetAttributeValueCount(element, attribute as CFString, &count)
        var evidence: [String: Any] = ["countStatus": countStatus.rawValue, "count": count]
        if countStatus == .attributeUnsupported || countStatus == .noValue {
            return ([], evidence, true)
        }
        guard countStatus == .success, count >= 0 else { return ([], evidence, false) }
        if count == 0 { return ([], evidence, true) }
        var array: CFArray?
        let status = AXUIElementCopyAttributeValues(element, attribute as CFString, 0, min(count, limit), &array)
        evidence["status"] = status.rawValue
        let nodes = array as? [AXUIElement] ?? []
        evidence["returned"] = nodes.count
        let complete = status == .success && count <= limit && nodes.count == count
        evidence["complete"] = complete
        return (nodes, evidence, complete)
    }

    static func parameter(_ element: AXUIElement, _ name: String, range: CFRange) -> [String: Any] {
        var requested = range
        guard let value = AXValueCreate(.cfRange, &requested) else {
            return ["queried": false, "reason": "cannot encode CFRange"]
        }
        var raw: CFTypeRef?
        let status = AXUIElementCopyParameterizedAttributeValue(element, name as CFString, value, &raw)
        var result = Read(status: status, raw: raw).json
        result["range"] = ["location": range.location, "length": range.length]
        return result
    }

    static func inspect(_ element: AXUIElement) -> [String: Any] {
        var result: [String: Any] = [:]
        for (key, name) in [
            ("role", kAXRoleAttribute), ("title", kAXTitleAttribute), ("identifier", kAXIdentifierAttribute),
            ("position", kAXPositionAttribute), ("size", kAXSizeAttribute),
            ("numberOfCharacters", kAXNumberOfCharactersAttribute), ("selectedTextRange", kAXSelectedTextRangeAttribute),
            ("selectedText", kAXSelectedTextAttribute), ("focused", kAXFocusedAttribute), ("value", kAXValueAttribute)
        ] {
            result[key] = read(element, name).json
        }
        var writable = DarwinBoolean(false)
        let writableStatus = AXUIElementIsAttributeSettable(element, kAXSelectedTextRangeAttribute as CFString, &writable)
        result["selectedTextRangeWritable"] = [
            "status": writableStatus.rawValue, "value": writableStatus == .success ? writable.boolValue as Any : NSNull()
        ]
        var names: CFArray?
        let namesStatus = AXUIElementCopyParameterizedAttributeNames(element, &names)
        result["parameterizedNames"] = [
            "status": namesStatus.rawValue, "value": names as? [String] as Any? ?? NSNull()
        ]
        let extent = read(element, kAXNumberOfCharactersAttribute)
        if extent.status == .success, let raw = extent.raw, CFGetTypeID(raw) != CFBooleanGetTypeID(),
            let number = raw as? NSNumber, let count = Int(number.stringValue), count >= 0, count <= 1_000_000
        {
            func sample(_ range: CFRange) -> [String: Any] {
                [
                    "bounds": parameter(element, kAXBoundsForRangeParameterizedAttribute, range: range),
                    "string": parameter(element, kAXStringForRangeParameterizedAttribute, range: range)
                ]
            }
            result["parameterized"] = [
                "extent": extent.json,
                "full": sample(CFRange(location: 0, length: count)),
                "firstCharacter": sample(CFRange(location: 0, length: min(1, count)))
            ]
        } else {
            result["parameterized"] = ["queried": false, "reason": "bounded native text extent unavailable", "extent": extent.json]
        }
        return result
    }

    static func query(pid: pid_t, title: String) -> [String: Any] {
        let deadline = ProcessInfo.processInfo.systemUptime + 5
        let application = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(application, 0.15)
        var result: [String: Any] = [
            "pid": pid, "windowTitle": title, "queryOnly": true, "trusted": AXIsProcessTrusted(),
            "coordinateSpace": "raw macOS AX desktop top-left logical points; no conversion",
            "limits": ["windowCount": 32, "nodeCount": 128, "depth": 12, "childrenPerNode": 64, "seconds": 5]
        ]
        let (windows, windowEvidence, windowsComplete) = children(application, attribute: kAXWindowsAttribute, limit: 32)
        result["windows"] = windowEvidence
        let candidates = windows.filter {
            owned($0, pid: pid) && read($0, kAXTitleAttribute).raw as? String == title
        }
        result["windowMatches"] = candidates.count
        guard windowsComplete, candidates.count == 1 else {
            result["error"] = "unique original window unavailable"
            return result
        }
        let window = candidates[0]
        var queue: [(AXUIElement, Int)] = [(window, 0)], seen = [AXUIElement](), matches = [AXUIElement]()
        var reads = [[String: Any]](), cursor = 0, complete = true
        while cursor < queue.count, seen.count < 128, ProcessInfo.processInfo.systemUptime < deadline {
            let (element, depth) = queue[cursor]
            cursor += 1
            if seen.contains(where: { CFEqual($0, element) }) { continue }
            guard owned(element, pid: pid) else { complete = false; continue }
            seen.append(element)
            AXUIElementSetMessagingTimeout(element, 0.15)
            let role = read(element, kAXRoleAttribute), name = read(element, kAXTitleAttribute)
            if role.raw as? String == kAXIncrementorRole, name.raw as? String == "Meeting date" {
                matches.append(element)
            }
            let (descendants, evidence, all) = children(element, attribute: kAXChildrenAttribute, limit: 64)
            reads.append(["index": seen.count - 1, "depth": depth, "role": role.json, "title": name.json, "children": evidence])
            complete = complete && all
            if depth >= 12 {
                if !descendants.isEmpty { complete = false }
            } else {
                queue += descendants.map { ($0, depth + 1) }
            }
        }
        complete = complete && cursor == queue.count
        result["discovery"] = ["complete": complete, "visited": seen.count, "matches": matches.count, "reads": reads]
        guard complete, matches.count == 1, owned(window, pid: pid),
            read(window, kAXTitleAttribute).raw as? String == title, owned(matches[0], pid: pid)
        else {
            result["error"] = "unique date control unavailable within the bounded original window"
            return result
        }
        result["element"] = inspect(matches[0])
        return result
    }

    static func main() throws {
        let args = CommandLine.arguments
        guard args.count == 5, args[1] == "--pid", let pid = pid_t(args[2]), pid > 0,
            args[3] == "--window-title", !args[4].isEmpty
        else {
            FileHandle.standardError.write(Data("usage: qt-cocoa-date-ax-probe --pid PID --window-title TITLE\n".utf8))
            exit(2)
        }
        let result = query(pid: pid, title: args[4])
        let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys, .prettyPrinted])
        FileHandle.standardOutput.write(data + Data([10]))
        if result["error"] != nil { exit(2) }
    }
}
