// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices
import Darwin

/// Read-only provider identity evidence for the disposable WKWebTextProbe.
/// No focus, activation, messaging-timeout setter, event or AX mutation is used.
@MainActor private final class WebTextProviderInspection {
    private typealias ActualPID = @convention(c) (AXUIElement, UnsafeMutablePointer<pid_t>) -> Int32
    private let libraryPath = "/System/Library/Frameworks/ApplicationServices.framework/Frameworks/HIServices.framework/HIServices"
    private var actualPID: ActualPID?
    private var references = [AXUIElement]()
    private var queries = [[String: Any]]()
    private let deadline = ProcessInfo.processInfo.systemUptime + 10
    private var report: [String: Any] = [
        "passed": false, "diagnostic_only": true, "read_only": true,
        "scope": "owned_fixture_provider_observation", "provider_identity": "uninterpreted",
    ]

    private enum Failure: Error, CustomStringConvertible {
        case unavailable(String)
        var description: String {
            switch self { case .unavailable(let detail): return detail }
        }
    }

    private func budget() throws {
        guard ProcessInfo.processInfo.systemUptime < deadline else {
            throw Failure.unavailable("read-only query budget exhausted; no further AX queries issued")
        }
    }

    private func reference(_ object: AXUIElement) -> String {
        if let index = references.firstIndex(where: { CFEqual($0, object) }) { return "ax\(index)" }
        references.append(object)
        return "ax\(references.count - 1)"
    }

    private struct AttributeRead {
        let value: CFTypeRef?
        let status: AXError
    }

    private func read(_ object: AXUIElement, _ attribute: String) throws -> AttributeRead {
        try budget()
        var value: CFTypeRef?
        let status = AXUIElementCopyAttributeValue(object, attribute as CFString, &value)
        var item: [String: Any] = [
            "object": reference(object), "api": "AXUIElementCopyAttributeValue", "attribute": attribute,
            "status": status.rawValue, "hasValue": value != nil,
        ]
        if let value {
            item["typeID"] = CFGetTypeID(value)
            if CFGetTypeID(value) == AXUIElementGetTypeID() {
                item["reference"] = reference(unsafeDowncast(value, to: AXUIElement.self))
            } else if let text = value as? String { item["value"] = text }
            else if let number = value as? NSNumber { item["value"] = number }
        }
        queries.append(item)
        return AttributeRead(value: status == .success ? value : nil, status: status)
    }

    private func text(_ object: AXUIElement, _ attribute: String) throws -> String? {
        try read(object, attribute).value as? String
    }

    private func object(_ source: AXUIElement, _ attribute: String) throws -> AXUIElement? {
        guard let raw = try read(source, attribute).value, CFGetTypeID(raw) == AXUIElementGetTypeID() else { return nil }
        return unsafeDowncast(raw, to: AXUIElement.self)
    }

    private func matchingText(_ object: AXUIElement, _ attribute: String, expected: String) throws
        -> (value: String?, matches: Bool, known: Bool)
    {
        let result = try read(object, attribute)
        if let value = result.value as? String { return (value, value == expected, true) }
        let absent = result.status == .attributeUnsupported || result.status == .noValue
        return (nil, false, absent)
    }

    /// Count+bounded copy keeps truncation distinct from an empty/unsupported
    /// child collection. Search completeness is evidence, not a prerequisite
    /// for inspecting the one positively matched fixture object.
    private func elements(_ object: AXUIElement, _ attribute: String, limit: Int) throws -> ([AXUIElement], Bool) {
        try budget()
        var count: CFIndex = 0
        let countStatus = AXUIElementGetAttributeValueCount(object, attribute as CFString, &count)
        queries.append([
            "object": reference(object), "api": "AXUIElementGetAttributeValueCount", "attribute": attribute,
            "status": countStatus.rawValue, "count": countStatus == .success ? count as Any : NSNull(),
        ])
        if countStatus == .attributeUnsupported || countStatus == .noValue { return ([], true) }
        guard countStatus == .success, count >= 0 else { return ([], false) }
        guard count > 0 else { return ([], true) }
        let requested = min(count, limit)
        guard requested > 0 else { return ([], false) }
        try budget()
        var values: CFArray?
        let status = AXUIElementCopyAttributeValues(object, attribute as CFString, 0, requested, &values)
        let raw = values as? [AnyObject] ?? []
        let found = raw.compactMap { value -> AXUIElement? in
            guard CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
            return unsafeDowncast(value, to: AXUIElement.self)
        }
        let complete = status == .success && count <= limit && found.count == count
        queries.append([
            "object": reference(object), "api": "AXUIElementCopyAttributeValues", "attribute": attribute,
            "status": status.rawValue, "requestedCount": requested, "reportedCount": count,
            "returnedCount": raw.count, "complete": complete, "references": found.map(reference),
        ])
        return (status == .success ? found : [], complete)
    }

    private func process(_ pid: pid_t) -> (NativeProcessIdentity?, [String: Any]) {
        var failure: [String: Any]?
        let identity = NativeProcessIdentity.read(pid, onFailure: { failure = $0.diagnostic })
        var value: [String: Any] = ["pid": pid, "identityKnown": identity != nil]
        if let identity {
            value["identity"] = [
                "pid": identity.pid, "parent": identity.parent, "incarnation": identity.incarnation,
                "seconds": identity.seconds, "microseconds": identity.microseconds, "executable": identity.executable,
            ]
        } else {
            value["identity"] = NSNull()
            value["readFailure"] = failure as Any? ?? NSNull()
        }
        if let app = NSRunningApplication(processIdentifier: pid) {
            value["application"] = [
                "available": true, "bundleIdentifier": app.bundleIdentifier as Any? ?? NSNull(),
                "bundleURL": app.bundleURL?.path as Any? ?? NSNull(),
                "executableURL": app.executableURL?.path as Any? ?? NSNull(),
                "isTerminated": app.isTerminated, "running": app.isTerminated ? "no" : "yes",
            ]
        } else {
            value["application"] = ["available": false, "running": "unknown"]
        }
        return (identity, value)
    }

    private func provider(_ object: AXUIElement) throws -> [String: Any] {
        try budget()
        var publicPID: pid_t = 0
        let publicStatus = AXUIElementGetPid(object, &publicPID)
        let publicKnown = publicStatus == .success && publicPID > 0
        let publicValue: [String: Any] = [
            "status": publicStatus.rawValue, "known": publicKnown, "pid": publicKnown ? publicPID as Any : NSNull(),
        ]
        queries.append([
            "object": reference(object), "api": "AXUIElementGetPid", "result": publicValue,
        ])
        var actualValue: [String: Any] = ["symbolAvailable": actualPID != nil, "status": NSNull(), "pid": NSNull(), "known": false]
        var actualKnownPID: pid_t?
        if let actualPID {
            try budget()
            var pid: pid_t = 0
            let status = actualPID(object, &pid)
            let known = status == 0 && pid > 0
            actualValue["status"] = status
            actualValue["known"] = known
            actualValue["pid"] = known ? pid as Any : NSNull()
            if known { actualKnownPID = pid }
        }
        queries.append([
            "object": reference(object), "api": "_AXUIElementGetActualPid", "result": actualValue,
        ])
        var processes = [String: Any]()
        if publicKnown { processes["public"] = process(publicPID).1 }
        if let actualKnownPID {
            processes["actual"] = actualKnownPID == publicPID && publicKnown
                ? processes["public"] : process(actualKnownPID).1
        }
        return [
            "reference": reference(object), "public_pid": publicValue, "actual_pid": actualValue,
            "processes": processes,
        ]
    }

    private func inspect(pid: pid_t, title: String) throws {
        let (identity, host) = process(pid)
        report["host_before"] = host
        guard let identity, let app = NSRunningApplication(processIdentifier: pid), !app.isTerminated,
            app.bundleIdentifier?.hasPrefix("local.computer-use.web-text.") == true,
            app.executableURL?.lastPathComponent == "WKWebTextProbe" else {
            throw Failure.unavailable("the PID is not the live owned WKWebTextProbe fixture")
        }
        let trusted = AXIsProcessTrusted()
        report["ax_trusted"] = trusted
        guard trusted else { throw Failure.unavailable("Accessibility is not trusted; no prompt or mutation attempted") }
        _ = dlerror()
        let library = dlopen(libraryPath, RTLD_LAZY | RTLD_LOCAL)
        let libraryError = library == nil ? dlerror().map { String(cString: $0) } : nil
        _ = dlerror()
        let symbol = library.flatMap { dlsym($0, "_AXUIElementGetActualPid") }
        let symbolError = symbol == nil ? dlerror().map { String(cString: $0) } : nil
        actualPID = symbol.map { unsafeBitCast($0, to: ActualPID.self) }
        report["symbol_access"] = [
            "library": libraryPath, "libraryLoaded": library != nil, "symbolAvailable": symbol != nil,
            "libraryError": libraryError as Any? ?? NSNull(), "symbolError": symbolError as Any? ?? NSNull(),
        ]
        let root = AXUIElementCreateApplication(pid)
        let (windows, windowsComplete) = try elements(root, kAXWindowsAttribute, limit: 64)
        var matches = [AXUIElement](), titlesComplete = true
        for window in windows {
            let observedTitle = try text(window, kAXTitleAttribute)
            if observedTitle == nil { titlesComplete = false }
            if observedTitle == title { matches.append(window) }
        }
        let windowSearchComplete = windowsComplete && titlesComplete
        report["window_search"] = [
            "complete": windowSearchComplete, "matches": matches.map(reference),
            "uniqueness": matches.count > 1 ? "multiple" : windowSearchComplete ? "complete" : "incomplete",
        ]
        guard matches.count == 1, let window = matches.first else {
            throw Failure.unavailable("expected one positive exact-title fixture window match")
        }
        report["window"] = try provider(window)
        var queue: [(AXUIElement, Int)] = [(window, 0)], visited = [AXUIElement](), inputs = [AXUIElement]()
        var cursor = 0, complete = true
        var candidates = [[String: Any]]()
        while cursor < queue.count {
            try budget()
            let (node, depth) = queue[cursor]
            cursor += 1
            if visited.contains(where: { CFEqual($0, node) }) { continue }
            guard visited.count < 128, depth <= 16 else { complete = false; break }
            visited.append(node)
            let role = try text(node, kAXRoleAttribute)
            if role == nil { complete = false }
            if role == kAXTextFieldRole || role == kAXTextAreaRole {
                let description = try matchingText(node, kAXDescriptionAttribute, expected: "Web callback input")
                let label = try matchingText(node, "AXLabel", expected: "Web callback input")
                let nodeTitle = try matchingText(node, kAXTitleAttribute, expected: "Web callback input")
                let domID = try matchingText(node, "AXDOMIdentifier", expected: "target")
                let fields = [description, label, nodeTitle, domID]
                let found = fields.contains(where: { $0.matches })
                let matchKnown = found || fields.allSatisfy { $0.known }
                if !matchKnown { complete = false }
                candidates.append([
                    "reference": reference(node), "role": role as Any? ?? NSNull(),
                    "matches": found, "matchKnown": matchKnown,
                    "description": description.value as Any? ?? NSNull(), "label": label.value as Any? ?? NSNull(),
                    "title": nodeTitle.value as Any? ?? NSNull(), "domIdentifier": domID.value as Any? ?? NSNull(),
                ])
                if found { inputs.append(node) }
            }
            let (children, childrenComplete) = try elements(node, kAXChildrenAttribute, limit: 128)
            complete = complete && childrenComplete
            for child in children { queue.append((child, depth + 1)) }
            report["tree"] = ["visited": visited.count, "complete": complete, "pending": queue.count - cursor]
            report["input_candidates"] = candidates
        }
        let treeComplete = complete && cursor == queue.count
        report["tree"] = [
            "visited": visited.count, "complete": treeComplete, "pending": queue.count - cursor,
            "positiveMatches": inputs.count,
            "uniqueness": inputs.count > 1 ? "multiple" : treeComplete ? "complete" : "incomplete",
        ]
        guard inputs.count == 1, let input = inputs.first else {
            throw Failure.unavailable("expected one positively matched input in the observed fixture tree")
        }
        report["input"] = try provider(input)
        var current = input, parents = [input], chain = [[String: Any]](), area: AXUIElement?
        for _ in 0..<24 {
            try budget()
            guard let parent = try object(current, kAXParentAttribute),
                !parents.contains(where: { CFEqual($0, parent) }) else { break }
            parents.append(parent)
            let role = try text(parent, kAXRoleAttribute)
            chain.append(["reference": reference(parent), "role": role as Any? ?? NSNull()])
            report["parent_chain"] = chain
            guard let role else { break }
            if role == "AXWebArea" { area = parent; break }
            if CFEqual(parent, window) || role == kAXApplicationRole || role == kAXWindowRole { break }
            current = parent
        }
        guard let area else { throw Failure.unavailable("nearest AXWebArea ancestor is unconfirmed") }
        report["web_area"] = try provider(area)
        let (after, hostAfter) = process(pid)
        report["host_after"] = hostAfter
        guard after == identity else { throw Failure.unavailable("host process incarnation changed during observation") }
        report["passed"] = true
    }

    func run(_ arguments: [String]) -> Int32 {
        var options = [String: String](), output: String?
        // Recover the requested output first so argument/identity failures also
        // preserve partial evidence; duplicate/unknown flags still fail below.
        if let position = arguments.firstIndex(of: "--output"), arguments.indices.contains(position + 1) {
            output = arguments[position + 1]
        }
        do {
            let allowed: Set<String> = ["--pid", "--title", "--output"]
            var index = 1
            while index < arguments.count {
                let key = arguments[index]
                guard allowed.contains(key), index + 1 < arguments.count, options[key] == nil else {
                    throw Failure.unavailable("invalid provider observation arguments")
                }
                options[key] = arguments[index + 1]
                index += 2
            }
            report["request"] = options
            guard Set(options.keys) == allowed, let pid = options["--pid"].flatMap(Int32.init), pid > 0,
                let title = options["--title"], !title.isEmpty else {
                throw Failure.unavailable("provider observation requires positive PID, exact title and output")
            }
            try inspect(pid: pid, title: title)
        } catch { report["error"] = ["type": String(describing: type(of: error)), "detail": String(describing: error)] }
        report["queries"] = queries
        report["query_count"] = queries.count
        report["observed_at"] = ISO8601DateFormatter().string(from: Date())
        do {
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) + Data([10])
            if let output { try data.write(to: URL(fileURLWithPath: output), options: .atomic) }
            else { FileHandle.standardOutput.write(data) }
        } catch {
            FileHandle.standardError.write(Data("Could not persist provider observation: \(error)\n".utf8))
            return 2
        }
        return report["passed"] as? Bool == true ? 0 : 1
    }
}

@main enum WebTextProviderProbe {
    @MainActor static func main() { exit(WebTextProviderInspection().run(CommandLine.arguments)) }
}
