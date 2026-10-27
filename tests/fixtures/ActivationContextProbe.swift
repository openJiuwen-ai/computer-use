import AppKit
import ApplicationServices

/// Controlled diagnostic fixture. Compile with the frozen native Sources except
/// main.swift. It never clicks, types, opens an app or automatically changes
/// routes. Each newline JSON command requests one named activation mechanism.
@MainActor private final class ActivationContextController: NSObject, NSApplicationDelegate {
    let target: NSRunningApplication
    let other: NSRunningApplication
    let process: NativeProcessIdentity
    let otherProcess: NativeProcessIdentity
    let window: CGWindowID
    let output: FileHandle
    private var busy = false
    private var eventWindow: NSWindow?

    init(target: NSRunningApplication, other: NSRunningApplication, window: CGWindowID, output: FileHandle) throws {
        guard let process = NativeProcessIdentity.read(target.processIdentifier),
            let otherProcess = NativeProcessIdentity.read(other.processIdentifier)
        else { throw NSError(domain: "ActivationContextProbe", code: 1) }
        self.target = target
        self.other = other
        self.process = process
        self.otherProcess = otherProcess
        self.window = window
        self.output = output
    }

    func record(_ values: [String: Any]) {
        var row = values
        row["time"] = Date().timeIntervalSince1970
        row["uptime"] = ProcessInfo.processInfo.systemUptime
        row["callerPID"] = getpid()
        row["callerPolicy"] = NSRunningApplication.current.activationPolicy.rawValue
        row["currentEventTimestamp"] = NSApp.currentEvent?.timestamp as Any? ?? NSNull()
        row["frontmostPID"] = NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? NSNull()
        row["targetPID"] = target.processIdentifier
        row["window"] = window
        row["targetPolicy"] = target.activationPolicy.rawValue
        row["targetActive"] = target.isActive
        if let data = try? JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]) {
            try? output.write(contentsOf: data + Data([10]))
            try? FileHandle.standardOutput.write(contentsOf: data + Data([10]))
        }
    }

    func sample(_ phase: String, label: String) {
        let root = AXUIElementCreateApplication(target.processIdentifier)
        AXUIElementSetMessagingTimeout(root, 0.1)
        var value: CFTypeRef?
        let status = AXUIElementCopyAttributeValue(root, kAXFrontmostAttribute as CFString, &value)
        let focused = AXAccess.element(root, kAXFocusedWindowAttribute).flatMap(BackgroundEvents.nativeWindowID)
        record([
            "kind": phase, "label": label, "reportedActiveStatus": status.rawValue,
            "reportedActive": value as? Bool as Any? ?? NSNull(), "reportedFocusedWindow": focused as Any? ?? NSNull()
        ])
    }

    private func validateTarget() throws {
        guard !target.isTerminated, NativeProcessIdentity.read(target.processIdentifier) == process,
            let row = WindowServerInfo.window(window),
            (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == target.processIdentifier
        else { throw NSError(domain: "ActivationContextProbe.targetIdentityChanged", code: 2) }
    }

    func perform(_ command: [String: Any]) async {
        let label = command["label"] as? String ?? "unlabelled"
        guard let operation = command["op"] as? String else {
            record(["kind": "invalidCommand", "label": label])
            return
        }
        if operation == "sample" {
            sample("sample", label: label)
            return
        }
        if operation == "quit" {
            record(["kind": "quit", "label": label])
            NSApp.terminate(nil)
            return
        }
        if operation == "show-caller-window" {
            if eventWindow == nil {
                let created = NSWindow(
                    contentRect: NSRect(x: 400, y: 650, width: 420, height: 180), styleMask: [.titled, .closable],
                    backing: .buffered, defer: false)
                created.title = "Activation caller timing"
                created.isReleasedWhenClosed = false
                eventWindow = created
            }
            eventWindow?.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            record([
                "kind": "callerWindowSetup", "label": label,
                "callerWindow": eventWindow?.windowNumber as Any? ?? NSNull()
            ])
            return
        }
        guard !busy else {
            record(["kind": "busy", "label": label])
            return
        }
        busy = true
        defer { busy = false }
        do {
            try validateTarget()
            sample("before", label: label)
            var accepted: Bool?
            switch operation {
            case "activate-held", "activate-fresh":
                guard
                    let selected = operation == "activate-held"
                        ? target : NSRunningApplication(processIdentifier: target.processIdentifier),
                    !selected.isTerminated
                else { throw NSError(domain: "ActivationContextProbe.noProxy", code: 3) }
                record([
                    "kind": "willActivate", "label": label, "op": operation,
                    "proxyAddress": String(describing: Unmanaged.passUnretained(selected).toOpaque()),
                    "retainedProxyAddress": String(describing: Unmanaged.passUnretained(target).toOpaque())
                ])
                accepted = selected.activate(options: [])
            case "ax-frontmost":
                let root = AXUIElementCreateApplication(target.processIdentifier)
                AXUIElementSetMessagingTimeout(root, 0.2)
                record(["kind": "willSetAXFrontmost", "label": label, "op": operation])
                let status = AXUIElementSetAttributeValue(root, kAXFrontmostAttribute as CFString, kCFBooleanTrue)
                accepted = status == .success
                record(["kind": "AXFrontmostReturned", "label": label, "status": status.rawValue])
            case "select-window":
                record(["kind": "willSelectWindow", "label": label, "op": operation])
                // Invoke exactly the existing native implementation under test.
                try BackgroundEvents.selectForegroundWindow(window)
                accepted = true
            case "raise-target", "set-main", "focus-target", "set-focused-window":
                let root = AXUIElementCreateApplication(target.processIdentifier)
                AXUIElementSetMessagingTimeout(root, 0.1)
                guard let windows = AXAccess.read(root, kAXWindowsAttribute).value as? [AXUIElement],
                    let selected = windows.first(where: { BackgroundEvents.nativeWindowID($0) == window })
                else { throw NSError(domain: "ActivationContextProbe.targetAXWindowUnconfirmed", code: 5) }
                record(["kind": "willSelectNativeWindow", "label": label, "op": operation])
                let status: AXError
                switch operation {
                case "raise-target": status = AXUIElementPerformAction(selected, kAXRaiseAction as CFString)
                case "set-main":
                    status = AXUIElementSetAttributeValue(selected, kAXMainAttribute as CFString, kCFBooleanTrue)
                case "focus-target":
                    status = AXUIElementSetAttributeValue(selected, kAXFocusedAttribute as CFString, kCFBooleanTrue)
                default: status = AXUIElementSetAttributeValue(root, kAXFocusedWindowAttribute as CFString, selected)
                }
                accepted = status == .success
                record([
                    "kind": "nativeWindowSelectionReturned", "label": label, "op": operation, "status": status.rawValue
                ])
            case "activate-other":
                guard !other.isTerminated, NativeProcessIdentity.read(other.processIdentifier) == otherProcess else {
                    throw NSError(domain: "ActivationContextProbe.otherIdentityChanged", code: 4)
                }
                record([
                    "kind": "willActivateOther", "label": label, "op": operation, "otherPID": other.processIdentifier
                ])
                accepted = other.activate(options: [])
            default:
                record(["kind": "unknownCommand", "label": label, "op": operation])
                return
            }
            record([
                "kind": "requestReturned", "label": label, "op": operation, "accepted": accepted as Any? ?? NSNull()
            ])
            let expected = operation == "activate-other" ? other.processIdentifier : target.processIdentifier
            // Same bounded observation cadence as ForegroundAssist; no action
            // or alternate route runs during/after this wait.
            for _ in 0..<24 {
                try validateTarget()
                if NSWorkspace.shared.frontmostApplication?.processIdentifier == expected { break }
                try await Task.sleep(for: .milliseconds(25))
            }
            sample("after", label: label)
        } catch { record(["kind": "error", "label": label, "op": operation, "error": String(describing: error)]) }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        record([
            "kind": "ready", "otherPID": other.processIdentifier,
            "input": """
            newline JSON sample / activate-held / activate-fresh / ax-frontmost / select-window / \
            raise-target / set-main / focus-target / set-focused-window / activate-other / \
            show-caller-window / quit
            """, "automaticActions": false
        ])
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            while let line = readLine() {
                DispatchQueue.main.async { [weak self] in
                    guard let data = line.data(using: .utf8),
                        let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
                    else { return }
                    Task { @MainActor in await self?.perform(command) }
                }
            }
            DispatchQueue.main.async { NSApp.terminate(nil) }
        }
    }
}

@main private struct ActivationContextProbe {
    @MainActor static func main() throws {
        guard CommandLine.arguments.count == 5, let pid = pid_t(CommandLine.arguments[1]),
            let otherPID = pid_t(CommandLine.arguments[2]), pid != otherPID,
            let window = CGWindowID(CommandLine.arguments[3]), window != 0,
            let target = NSRunningApplication(processIdentifier: pid), !target.isTerminated,
            let other = NSRunningApplication(processIdentifier: otherPID), !other.isTerminated
        else {
            try? FileHandle.standardError.write(
                contentsOf: Data("Usage: ActivationContextProbe TARGET_PID OTHER_PID WINDOW_ID OUTPUT_JSONL\n".utf8))
            exit(2)
        }
        let path = CommandLine.arguments[4]
        guard !FileManager.default.fileExists(atPath: path), FileManager.default.createFile(atPath: path, contents: nil)
        else { exit(3) }
        let output = try FileHandle(forWritingTo: URL(fileURLWithPath: path))
        let application = NSApplication.shared
        application.setActivationPolicy(.prohibited)
        let delegate = try ActivationContextController(target: target, other: other, window: window, output: output)
        application.delegate = delegate
        application.run()
        try output.close()
    }
}
