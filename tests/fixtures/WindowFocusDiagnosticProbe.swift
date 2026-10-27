import AppKit
import ApplicationServices

// Standalone diagnostic for an ALREADY OBSERVED process/window, never discovery
// by title or application name. Compile without product sources:
// swiftc -parse-as-library WindowFocusDiagnosticProbe.swift -o /tmp/window-focus-probe
// /tmp/window-focus-probe --pid 123 --window 456 --seconds 5
// Add --set-focused to request AXFocused=true ONCE after a 1-second baseline.
// Default is read-only. No keys, mouse, AXRaise, activation, or GUI are sent.
// AX reports identify a reported recipient, not proof of actual key delivery.
// Output contains identities/status only: no titles, values, document URLs/text.
@main struct WindowFocusDiagnosticProbe {
    struct ProbeError: Error, CustomStringConvertible { let description: String }
    struct Options {
        let pid: pid_t
        let window: CGWindowID
        let seconds: Double
        let setFocused: Bool

        init(_ arguments: [String]) throws {
            var pid: pid_t?, window: CGWindowID?, seconds = 5.0, setFocused = false
            var seen = Set<String>(), index = 0
            while index < arguments.count {
                let key = arguments[index]
                guard seen.insert(key).inserted else { throw ProbeError(description: "duplicate option: \(key)") }
                if key == "--set-focused" {
                    setFocused = true
                    index += 1
                    continue
                }
                guard ["--pid", "--window", "--seconds"].contains(key), index + 1 < arguments.count else {
                    throw ProbeError(
                        description: "usage: probe --pid PID --window WINDOW_ID [--seconds 2...60] [--set-focused]")
                }
                let value = arguments[index + 1]
                switch key {
                case "--pid": pid = pid_t(value)
                case "--window": window = CGWindowID(value)
                default: seconds = Double(value) ?? .nan
                }
                index += 2
            }
            guard let pid, pid > 0, let window, window > 0, seconds.isFinite, (2...60).contains(seconds) else {
                throw ProbeError(
                    description: "a positive observed PID/window ID and duration of 2...60 seconds are required")
            }
            self.pid = pid
            self.window = window
            self.seconds = seconds
            self.setFocused = setFocused
        }
    }

    @MainActor final class Probe {
        typealias NativeWindow = @convention(c) (AXUIElement, UnsafeMutablePointer<CGWindowID>) -> Int32
        typealias ActualPID = @convention(c) (AXUIElement, UnsafeMutablePointer<pid_t>) -> Int32
        let options: Options
        let app: NSRunningApplication
        let launch: Date?
        let root: AXUIElement
        let window: AXUIElement
        let nativeWindow: NativeWindow
        let actualPID: ActualPID?

        init(_ options: Options) throws {
            self.options = options
            guard AXIsProcessTrusted() else {
                throw ProbeError(description: "accessibility permission unavailable; no input attempted")
            }
            guard let app = NSRunningApplication(processIdentifier: options.pid), !app.isTerminated else {
                throw ProbeError(description: "observed process is no longer running")
            }
            self.app = app
            launch = app.launchDate
            guard
                let library = dlopen(
                    """
                    /System/Library/Frameworks/ApplicationServices.framework/Frameworks/HIServices.framework\
                    /HIServices
                    """, RTLD_LAZY | RTLD_LOCAL), let pointer = dlsym(library, "_AXUIElementGetWindow")
            else { throw ProbeError(description: "exact native window identity lookup unavailable") }
            nativeWindow = unsafeBitCast(pointer, to: NativeWindow.self)
            actualPID = dlsym(library, "_AXUIElementGetActualPid").map { unsafeBitCast($0, to: ActualPID.self) }
            root = AXUIElementCreateApplication(options.pid)
            AXUIElementSetMessagingTimeout(root, 0.1)
            var raw: CFTypeRef?
            let status = AXUIElementCopyAttributeValue(root, kAXWindowsAttribute as CFString, &raw)
            guard status == .success else { throw ProbeError(description: "AXWindows unavailable: \(status.rawValue)") }
            let lookup = nativeWindow
            let candidates = (raw as? [AXUIElement] ?? []).filter {
                var id: CGWindowID = 0
                return lookup($0, &id) == 0 && id == options.window
            }
            guard candidates.count == 1 else {
                throw ProbeError(description: "observed AX window identity is absent or ambiguous")
            }
            window = candidates[0]
            try validate()
        }

        func validate() throws {
            guard !app.isTerminated, let current = NSRunningApplication(processIdentifier: options.pid),
                current.launchDate == launch
            else { throw ProbeError(description: "observed process identity changed") }
            let windows = CGWindowListCopyWindowInfo(.optionAll, kCGNullWindowID) as? [[String: Any]] ?? []
            guard
                windows.contains(where: {
                    ($0[kCGWindowNumber as String] as? NSNumber)?.uint32Value == options.window
                        && ($0[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == options.pid
                })
            else { throw ProbeError(description: "observed WindowServer owner/identity changed") }
            var pid: pid_t = 0, id: CGWindowID = 0
            guard AXUIElementGetPid(window, &pid) == .success, pid == options.pid, nativeWindow(window, &id) == 0,
                id == options.window
            else { throw ProbeError(description: "observed AX window identity changed") }
        }

        func identity(_ element: AXUIElement) -> [String: Any] {
            var pid: pid_t = 0, actual: pid_t = 0, id: CGWindowID = 0, raw: CFTypeRef?
            let pidStatus = AXUIElementGetPid(element, &pid)
            let windowStatus = nativeWindow(element, &id)
            let roleStatus = AXUIElementCopyAttributeValue(element, kAXRoleAttribute as CFString, &raw)
            var result: [String: Any] = [
                "pidStatus": pidStatus.rawValue, "windowStatus": windowStatus, "roleStatus": roleStatus.rawValue
            ]
            if pidStatus == .success { result["pid"] = pid }
            if windowStatus == 0 { result["windowID"] = id }
            if roleStatus == .success { result["role"] = raw as? String ?? "<not a string>" }
            if let actualPID {
                let status = actualPID(element, &actual)
                result["actualPIDStatus"] = status
                if status == 0 { result["actualPID"] = actual }
            }
            return result
        }

        func reference(_ element: AXUIElement, _ key: String) -> (AXUIElement?, [String: Any]) {
            var raw: CFTypeRef?
            let status = AXUIElementCopyAttributeValue(element, key as CFString, &raw)
            guard status == .success, let raw, CFGetTypeID(raw) == AXUIElementGetTypeID() else {
                return (nil, ["status": status.rawValue, "isElement": false])
            }
            let value = unsafeDowncast(raw, to: AXUIElement.self)
            return (value, ["status": status.rawValue, "isElement": true, "element": identity(value)])
        }

        func flag(_ key: String) -> [String: Any] {
            var raw: CFTypeRef?, writable = DarwinBoolean(false)
            let status = AXUIElementCopyAttributeValue(window, key as CFString, &raw)
            let settableStatus = AXUIElementIsAttributeSettable(window, key as CFString, &writable)
            var value: [String: Any] = ["status": status.rawValue, "settableStatus": settableStatus.rawValue]
            if status == .success, let boolean = raw as? NSNumber { value["value"] = boolean.boolValue }
            if settableStatus == .success { value["settable"] = writable.boolValue }
            return value
        }

        func sample(_ elapsed: Double) throws {
            try validate()
            let focusedWindow = reference(root, kAXFocusedWindowAttribute)
            let mainWindow = reference(root, kAXMainWindowAttribute)
            let focus = reference(root, kAXFocusedUIElementAttribute)
            var state: [String: Any] = [
                "kind": "sample", "elapsedMs": elapsed * 1000,
                "foregroundPID": NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0,
                "expectedPID": options.pid, "expectedWindowID": options.window, "AXFocusedWindow": focusedWindow.1,
                "AXMainWindow": mainWindow.1, "AXFocusedUIElement": focus.1,
                "targetAXFocused": flag(kAXFocusedAttribute), "targetAXMain": flag(kAXMainAttribute),
                "flags": CGEventSource.flagsState(.combinedSessionState).rawValue
            ]
            if let element = focus.0 {
                state["focusAXWindow"] = reference(element, kAXWindowAttribute).1
                state["focusAXTopLevelUIElement"] = reference(element, kAXTopLevelUIElementAttribute).1
            }
            if let point = CGEvent(source: nil)?.location { state["cursor"] = ["x": point.x, "y": point.y] }
            try emit(state)
        }

        func requestFocus() throws {
            try validate()
            var writable = DarwinBoolean(false)
            let query = AXUIElementIsAttributeSettable(window, kAXFocusedAttribute as CFString, &writable)
            guard query == .success, writable.boolValue else {
                try emit([
                    "kind": "focus_request", "attempted": false, "settableStatus": query.rawValue,
                    "settable": writable.boolValue
                ])
                return
            }
            // Recheck the exact selected identity immediately before the only write.
            try validate()
            let status = AXUIElementSetAttributeValue(window, kAXFocusedAttribute as CFString, kCFBooleanTrue)
            try emit([
                "kind": "focus_request", "attempted": true, "status": status.rawValue, "expectedPID": options.pid,
                "expectedWindowID": options.window
            ])
        }
    }

    static func emit(_ value: [String: Any]) throws {
        try FileHandle.standardOutput.write(
            contentsOf: JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) + Data([10]))
    }

    @MainActor static func run() async throws {
        let options = try Options(Array(CommandLine.arguments.dropFirst()))
        let probe = try Probe(options)
        try emit([
            "kind": "start", "pid": options.pid, "windowID": options.window, "readOnly": !options.setFocused,
            "seconds": options.seconds,
            "recipientEvidence": "AX reports only; no keyboard events are sent by this probe"
        ])
        let started = ProcessInfo.processInfo.systemUptime
        var attempted = false
        while ProcessInfo.processInfo.systemUptime - started <= options.seconds {
            let elapsed = ProcessInfo.processInfo.systemUptime - started
            try probe.sample(elapsed)
            if options.setFocused, !attempted, elapsed >= 1 {
                attempted = true
                try probe.requestFocus()
                try probe.sample(ProcessInfo.processInfo.systemUptime - started)
            }
            try await Task.sleep(for: .milliseconds(50))
        }
    }

    @MainActor static func main() {
        NSApplication.shared.setActivationPolicy(.prohibited)
        Task { @MainActor in
            do {
                try await run()
                exit(0)
            } catch {
                try? emit(["kind": "error", "detail": String(describing: error)])
                exit(1)
            }
        }
        NSApplication.shared.run()
    }
}
