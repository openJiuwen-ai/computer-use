import AppKit
import ApplicationServices

/// Native mechanism diagnostic, not public SDK acceptance. One explicit command
/// selects the retained window, observes app activation and the physical hit,
/// then sends at most one intended content gesture using production events.
/// It deliberately does not require or manufacture keyboard-window focus.
@MainActor private final class ActivationPointerController: NSObject, NSApplicationDelegate {
    let app: NSRunningApplication
    let process: NativeProcessIdentity
    let window: CGWindowID
    let output: FileHandle
    private var busy = false
    private var dispatched = false

    init(app: NSRunningApplication, window: CGWindowID, output: FileHandle) throws {
        guard let process = NativeProcessIdentity.read(app.processIdentifier) else {
            throw DriverError("missing process", code: .staleTarget)
        }
        self.app = app
        self.process = process
        self.window = window
        self.output = output
    }

    func record(_ values: [String: Any]) {
        var row = values
        row["time"] = Date().timeIntervalSince1970
        row["uptime"] = ProcessInfo.processInfo.systemUptime
        row["callerPID"] = getpid()
        row["targetPID"] = app.processIdentifier
        row["window"] = window
        row["frontmostPID"] = NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? NSNull()
        row["targetActive"] = app.isActive
        if let data = try? JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]) {
            try? output.write(contentsOf: data + Data([10]))
            try? FileHandle.standardOutput.write(contentsOf: data + Data([10]))
        }
    }

    func perform(_ command: [String: Any]) async {
        guard let op = command["op"] as? String else { return }
        if op == "quit" {
            NSApp.terminate(nil)
            return
        }
        guard op == "select-and-gesture", !busy, !dispatched, let x = command["x"] as? Double,
            let y = command["y"] as? Double
        else {
            record(["kind": "refused"])
            return
        }
        busy = true
        defer { busy = false }
        do {
            guard let row = WindowServerInfo.window(window).flatMap(NativeInputWindowQuery.row),
                row.pid == app.processIdentifier, x >= 0, y >= 0, x < row.frame.width, y < row.frame.height
            else { throw DriverError("invalid retained window", code: .staleTarget) }
            let mapping = WindowGeometry.WindowMapping(
                id: window, pid: app.processIdentifier, frame: row.frame, title: nil,
                width: Int(row.frame.width.rounded(.up)), height: Int(row.frame.height.rounded(.up)))
            let point = CGPoint(x: row.frame.minX + x, y: row.frame.minY + y)
            let root = AXUIElementCreateApplication(app.processIdentifier)
            AXUIElementSetMessagingTimeout(root, 0.1)
            let native = (AXAccess.read(root, kAXWindowsAttribute).value as? [AXUIElement])?.first {
                BackgroundEvents.nativeWindowID($0) == window
            }
            func checkIdentity() throws {
                guard NativeProcessIdentity.read(app.processIdentifier) == process,
                    let fresh = WindowServerInfo.window(window).flatMap(NativeInputWindowQuery.row),
                    fresh.pid == row.pid, fresh.frame == row.frame
                else { throw DriverError("target identity/geometry changed", code: .staleTarget) }
            }
            func sample(_ phase: String, held: Bool = false) throws {
                let hit = BackgroundEvents.inputWindowHit(at: point, retained: mapping, nativeWindow: native)
                let front = NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier
                let active = app.isActive
                let sameProcess = NativeProcessIdentity.read(app.processIdentifier) == process
                let focused = AXAccess.element(root, kAXFocusedWindowAttribute).flatMap(BackgroundEvents.nativeWindowID)
                record([
                    "kind": phase, "point": [point.x, point.y], "hitWindow": hit.windowID as Any? ?? NSNull(),
                    "frontMatches": front, "activeMatches": active, "processMatches": sameProcess,
                    "hitMatches": hit.windowID == window, "reportedFocusedWindow": focused as Any? ?? NSNull()
                ])
                try checkIdentity()
                guard front, active, hit.windowID == window else {
                    throw DriverError("exact active physical destination unavailable", code: .inputUnavailable)
                }
                try BackgroundTarget.validateReleasedInput(allowing: held ? .left : nil)
            }
            try checkIdentity()
            try BackgroundTarget.validateReleasedInput()
            record([
                "kind": "willSelect", "AXTrusted": AXIsProcessTrusted(), "postAllowed": CGPreflightPostEventAccess()
            ])
            try BackgroundEvents.selectForegroundWindow(window)
            record(["kind": "selectionReturned"])
            for _ in 0..<24 {
                try checkIdentity()
                if NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier, app.isActive {
                    break
                }
                try await Task.sleep(for: .milliseconds(25))
            }
            if command["raise"] as? Bool == true, let native {
                let status = AXUIElementPerformAction(native, kAXRaiseAction as CFString)
                record(["kind": "raiseReturned", "status": status.rawValue])
            }
            try await Task.sleep(for: .milliseconds(60))
            try sample("beforeGesture")
            let request = try NativePointerIntent(
                unit: "pointer_sequence", kind: "click", parameters: ["x": x, "y": y], foreground: false)
            let events = try NativePointerEvents(
                request: request, from: point, to: nil, viewport: row.frame.size, source: Input.source())
            try await events.deliver(
                check: { _, held in try sample("eventCheck", held: held) },
                post: { event, position in
                    event.location = position
                    self.record([
                        "kind": "post", "type": event.type.rawValue, "point": [position.x, position.y],
                        "number": event.getIntegerValueField(.mouseEventNumber)
                    ])
                    event.post(tap: .cghidEventTap)
                }, canRelease: { NativeProcessIdentity.read(self.app.processIdentifier) == self.process },
                skipped: { self.record(["kind": "releaseSkipped"]) },
                onDispatch: {
                    self.dispatched = true
                    self.record(["kind": "dispatchBoundary"])
                }, onPosted: {})
            record(["kind": "gestureComplete"])
        } catch { record(["kind": "error", "error": String(describing: error), "dispatched": dispatched]) }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        record(["kind": "ready", "automaticActions": false])
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

@main private struct ActivationPointerProbe {
    @MainActor static func main() throws {
        guard CommandLine.arguments.count == 4, let pid = pid_t(CommandLine.arguments[1]),
            let window = CGWindowID(CommandLine.arguments[2]), let app = NSRunningApplication(processIdentifier: pid)
        else { exit(2) }
        let path = CommandLine.arguments[3]
        guard !FileManager.default.fileExists(atPath: path), FileManager.default.createFile(atPath: path, contents: nil)
        else { exit(3) }
        let output = try FileHandle(forWritingTo: URL(fileURLWithPath: path))
        let application = NSApplication.shared
        application.setActivationPolicy(.prohibited)
        let controller = try ActivationPointerController(app: app, window: window, output: output)
        application.delegate = controller
        application.run()
        try output.close()
    }
}
