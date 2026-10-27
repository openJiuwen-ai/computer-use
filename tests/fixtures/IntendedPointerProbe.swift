import AppKit
import ApplicationServices

/// Native diagnostic only: one intended content gesture after externally
/// established real app activation. Deliberately does not apply the candidate's
/// keyboard-focus admission, prepare, activate, raise, retry or send a primer.
/// It reuses the project's unchanged NativePointerEvents and physical boundary.
@main private struct IntendedPointerProbe {
    @MainActor static func main() async throws {
        guard CommandLine.arguments.count == 6, let pid = pid_t(CommandLine.arguments[1]),
            let id = CGWindowID(CommandLine.arguments[2]), let x = Double(CommandLine.arguments[3]),
            let y = Double(CommandLine.arguments[4]), let app = NSRunningApplication(processIdentifier: pid),
            let process = NativeProcessIdentity.read(pid),
            let row = WindowServerInfo.window(id).flatMap(NativeInputWindowQuery.row), row.pid == pid, x >= 0, y >= 0,
            x < row.frame.width, y < row.frame.height
        else { exit(2) }
        let file = URL(fileURLWithPath: CommandLine.arguments[5])
        guard !FileManager.default.fileExists(atPath: file.path),
            FileManager.default.createFile(atPath: file.path, contents: nil)
        else { exit(3) }
        let log = try FileHandle(forWritingTo: file)
        func record(_ values: [String: Any]) {
            var row = values
            row["time"] = Date().timeIntervalSince1970
            row["uptime"] = ProcessInfo.processInfo.systemUptime
            if let data = try? JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]) {
                try? log.write(contentsOf: data + Data([10]))
                try? FileHandle.standardOutput.write(contentsOf: data + Data([10]))
            }
        }
        let mapping = WindowGeometry.WindowMapping(
            id: id, pid: pid, frame: row.frame, title: nil, width: Int(row.frame.width.rounded(.up)),
            height: Int(row.frame.height.rounded(.up)))
        let point = CGPoint(x: row.frame.minX + x, y: row.frame.minY + y)
        let root = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(root, 0.1)
        let native = (AXAccess.read(root, kAXWindowsAttribute).value as? [AXUIElement])?.first {
            BackgroundEvents.nativeWindowID($0) == id
        }
        record([
            "kind": "before", "targetPID": pid, "window": id, "point": [point.x, point.y],
            "frontmost": NSWorkspace.shared.frontmostApplication?.processIdentifier as Any? ?? NSNull(),
            "targetActive": app.isActive, "AXTrusted": AXIsProcessTrusted(), "postAllowed": CGPreflightPostEventAccess()
        ])
        func check(_ point: CGPoint, held: Bool) throws {
            guard NativeProcessIdentity.read(pid) == process,
                NSWorkspace.shared.frontmostApplication?.processIdentifier == pid, app.isActive,
                BackgroundEvents.inputWindowHit(at: point, retained: mapping, nativeWindow: native).windowID == id
            else { throw DriverError("diagnostic exact active window/physical hit changed", code: .inputUnavailable) }
            try BackgroundTarget.validateReleasedInput(allowing: held ? .left : nil)
        }
        do {
            try check(point, held: false)
            let source = try Input.source()
            let request = try NativePointerIntent(
                unit: "pointer_sequence", kind: "click", parameters: ["x": x, "y": y], foreground: false)
            let events = try NativePointerEvents(
                request: request, from: point, to: nil, viewport: row.frame.size, source: source)
            try await events.deliver(
                check: { try check($0, held: $1) },
                post: { event, position in
                    event.location = position
                    record([
                        "kind": "post", "type": event.type.rawValue, "point": [position.x, position.y],
                        "number": event.getIntegerValueField(.mouseEventNumber)
                    ])
                    event.post(tap: .cghidEventTap)
                }, canRelease: { NativeProcessIdentity.read(pid) == process },
                skipped: { record(["kind": "releaseSkipped"]) }, onDispatch: { record(["kind": "dispatchBoundary"]) },
                onPosted: {})
            record(["kind": "gestureComplete"])
        } catch { record(["kind": "error", "error": String(describing: error)]) }
        try log.close()
    }
}
