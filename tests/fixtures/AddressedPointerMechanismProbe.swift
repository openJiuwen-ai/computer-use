// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import AppKit
import ApplicationServices
import Darwin

/// An opt-in primitive diagnostic for the disposable PointerButtonProbe only.
/// It selects the existing window-directed unit explicitly; it neither changes
/// production admission policy nor proves that the public SDK accepted a click.
@MainActor private final class AddressedPointerProbeRun {
    let options: [String: String]
    var report: [String: Any] = [
        "passed": false, "scope": "primitive_diagnostic_not_sdk_acceptance",
        "selected_delivery": "surface_pointer", "global_input_allowed": false,
        "application_effect": "unconfirmed"
    ]
    var preparation = [String]()
    var inputActivity = [[String: Any]]()
    var dispatch = "notDispatched"
    var cleanup = "complete"
    var posted = 0

    init(_ arguments: [String]) throws {
        let required: Set<String> = [
            "--pid", "--window", "--bundle", "--x", "--y", "--width", "--height", "--button", "--count", "--output"
        ]
        var values = [String: String]()
        var index = 1
        while index < arguments.count {
            let key = arguments[index]
            guard required.union(["--preparation"]).contains(key), index + 1 < arguments.count,
                values[key] == nil else { throw DriverError("invalid probe arguments", code: .invalidRequest) }
            values[key] = arguments[index + 1]
            index += 2
        }
        guard required.isSubset(of: Set(values.keys)),
            ["background", "foreground"].contains(values["--preparation"] ?? "background"),
            values["--bundle"]!.hasPrefix("local.computer-use.pointer-buttons.") else {
            throw DriverError("probe accepts only a disposable pointer-buttons fixture", code: .invalidRequest)
        }
        options = values
        report["request"] = Dictionary(uniqueKeysWithValues: values.map { (String($0.key.dropFirst(2)), $0.value) })
    }

    private func numeric(_ key: String) throws -> Double {
        guard let value = options[key].flatMap(Double.init), value.isFinite else {
            throw DriverError("invalid numeric probe argument \(key)", code: .invalidRequest)
        }
        return value
    }

    private func windowFrame(_ id: CGWindowID, pid: pid_t) throws -> (CGRect, String?) {
        guard let info = WindowServerInfo.window(id),
            (info[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid,
            let bounds = info[kCGWindowBounds as String] as? [String: Any],
            let frame = CGRect(dictionaryRepresentation: bounds as CFDictionary),
            frame.origin.x.isFinite, frame.origin.y.isFinite,
            frame.width.isFinite, frame.height.isFinite, frame.width > 0, frame.height > 0 else {
            throw DriverError("the exact probe window or its owner is unavailable", code: .staleTarget)
        }
        return (frame, info[kCGWindowName as String] as? String)
    }

    private func perform() async throws {
        guard let pid = options["--pid"].flatMap(Int32.init), pid > 0,
            let windowID = options["--window"].flatMap(UInt32.init), windowID > 0,
            let application = NSRunningApplication(processIdentifier: pid),
            application.bundleIdentifier == options["--bundle"], !application.isTerminated,
            let identity = NativeProcessIdentity.read(pid) else {
            throw DriverError("the exact disposable fixture process is unavailable", code: .staleTarget)
        }
        guard AXIsProcessTrusted() else {
            throw DriverError("the independent probe lacks Accessibility permission; no input was sent", code: .permissionRequired)
        }
        let width = try numeric("--width"), height = try numeric("--height")
        let x = try numeric("--x"), y = try numeric("--y")
        guard width > 0, height > 0, width <= 100_000, height <= 100_000,
            width.rounded() == width, height.rounded() == height,
            x >= 0, y >= 0, x < width, y < height,
            let count = options["--count"].flatMap(Int.init) else {
            throw DriverError("invalid original window point or dimensions", code: .invalidRequest)
        }
        let (frame, title) = try windowFrame(windowID, pid: pid)
        guard frame.width == width, frame.height == height else {
            throw DriverError("fixture dimensions changed after observation", code: .staleTarget)
        }
        let root = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(root, 0.2)
        guard let windows = AXAccess.elements(root, kAXWindowsAttribute, limit: 64) else {
            throw DriverError("the fixture AX window catalogue is unavailable", code: .staleTarget)
        }
        let matchingWindows = windows.filter { BackgroundEvents.nativeWindowID($0) == windowID }
        guard matchingWindows.count == 1, let nativeWindow = matchingWindows.first else {
            throw DriverError("the exact original fixture AX window is not uniquely confirmed", code: .staleTarget)
        }
        let mapping = WindowGeometry.WindowMapping(
            id: windowID, pid: pid, frame: frame, title: title, width: Int(width), height: Int(height))
        let unit = NativePointerUnit(application: application, window: mapping, nativeWindow: nativeWindow)
        let target = BackgroundTarget(application)
        let request = try NativePointerIntent(
            unit: "pointer_sequence", kind: "click",
            parameters: ["x": x, "y": y, "button": options["--button"]!, "count": count], foreground: false)
        try unit.validateParameters(request)
        let point = try unit.point(request.start!)
        report["frame"] = [frame.minX, frame.minY, frame.width, frame.height]
        report["screen_point"] = [point.x, point.y]
        report["process_incarnation"] = identity.incarnation

        // These are real read-only qualification facts. They are retained as
        // evidence; this diagnostic's explicit route does not consume the
        // production semantic-role/action gate being investigated.
        report["receiver_qualified"] = try unit.addressedPointerReceiver(at: point) == .declaredInteraction
        var hit: AXUIElement?
        let hitStatus = AXUIElementCopyElementAtPosition(root, Float(point.x), Float(point.y), &hit)
        var evidence: [String: Any] = ["queryStatus": hitStatus.rawValue]
        if let hit, hitStatus == .success {
            var actions: CFArray?
            let actionStatus = AXUIElementCopyActionNames(hit, &actions)
            let owner = WindowGeometry.owningWindow(hit, pid: pid, in: nativeWindow)
            evidence["role"] = AXAccess.read(hit, kAXRoleAttribute).value as? String ?? "unknown"
            evidence["actions"] = actions as? [String] ?? []
            evidence["actionStatus"] = actionStatus.rawValue
            evidence["sameWindow"] = owner.map { CFEqual($0, nativeWindow) } == true
        }
        report["receiver_evidence"] = evidence

        let check: @MainActor () throws -> Void = {
            try RequestDiagnostics.checkCancellation()
            try target.validate(input: true)
            guard NativeProcessIdentity.read(pid) == identity,
                application.bundleIdentifier == self.options["--bundle"] else {
                throw DriverError("fixture process identity changed", code: .staleTarget)
            }
            let (current, _) = try self.windowFrame(windowID, pid: pid)
            guard current == frame else {
                throw DriverError("fixture window geometry changed during the diagnostic", code: .staleTarget)
            }
            guard let currentWindows = AXAccess.elements(root, kAXWindowsAttribute, limit: 64) else {
                throw DriverError("fixture AX window catalogue became unavailable", code: .staleTarget)
            }
            let currentMatches = currentWindows.filter { BackgroundEvents.nativeWindowID($0) == windowID }
            guard currentMatches.count == 1, CFEqual(currentMatches[0], nativeWindow),
                AXAccess.elements(nativeWindow, "AXSheets", limit: 8)?.isEmpty != false else {
                throw DriverError("fixture original window scope or modal state changed", code: .staleTarget)
            }
            try unit.validateWindowIdentity()
            try BackgroundEvents.requirePointerRoute(window: mapping)
        }
        try check()
        let foreground = options["--preparation"] == "foreground"
        report["foreground_preparation_requested"] = foreground
        try await NativeInputActivity.$stage.withValue(.preparation) {
            try await unit.prepare(
                foreground: foreground, atNative: point, kind: .click, delivery: .windowDirected, check: check,
                onPreparation: { self.preparation.append($0) }, onCleanupUnknown: { self.cleanup = "unknown" })
        }
        // Exactly one existing production gesture. No retry, alternate route,
        // AXPress, callback simulation or manually constructed input is used.
        try await unit.deliver(
            request, check: check,
            onDispatch: {
                try check()
                self.dispatch = "unknown"
            }, onPosted: { self.posted += 1 }, onCleanupUnknown: { self.cleanup = "unknown" },
            delivery: .windowDirected)
        dispatch = "dispatched"
        report["passed"] = cleanup == "complete"
    }

    func run() async -> Int32 {
        let activity = NativeInputActivity(
            operation: UUID().uuidString, step: 1, command: "fixedWindowDirectedDiagnostic", intent: "click",
            target: ["pid": options["--pid"]!, "window": options["--window"]!, "bundle": options["--bundle"]!],
            globalInputAllowed: false, emit: { self.inputActivity.append($0) })
        await NativeInputAuthorization.$globalInputAllowed.withValue(false) {
            await NativeInputActivity.$current.withValue(activity) {
                await RequestDiagnostics.$deadline.withValue(ProcessInfo.processInfo.systemUptime + 15) {
                    do {
                        try await self.perform()
                    } catch {
                        if self.posted > 0 { self.dispatch = "partial" }
                        self.report["error"] = [
                            "code": (error as? ClassifiedFailure)?.code.rawValue ?? "execution_failed",
                            "detail": String(describing: error)
                        ]
                    }
                    // End the primitive's process-local activation lease before
                    // finishing evidence; it never switches the user back.
                    SyntheticFocusCoordinator.shared.stop()
                }
            }
        }
        report["impact"] = activity.finish(dispatch: dispatch, cleanup: cleanup)
        report["dispatch"] = dispatch
        report["cleanup"] = cleanup
        report["preparation"] = preparation
        report["posted_event_callbacks"] = posted
        report["input_activity"] = inputActivity
        do {
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try (data + Data([10])).write(to: URL(fileURLWithPath: options["--output"]!), options: .atomic)
        } catch {
            diagnose("could not write primitive diagnostic result: \(error)")
            return 2
        }
        return report["passed"] as? Bool == true ? 0 : 1
    }
}

@MainActor private final class AddressedPointerProbeDelegate: NSObject, NSApplicationDelegate {
    let run: AddressedPointerProbeRun
    var task: Task<Void, Never>?
    var signals = [DispatchSourceSignal]()
    init(_ run: AddressedPointerProbeRun) { self.run = run }

    func applicationDidFinishLaunching(_ notification: Notification) {
        task = Task { @MainActor in exit(await self.run.run()) }
    }
    func observeTermination() {
        for number in [SIGTERM, SIGINT, SIGHUP] {
            signal(number, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
            source.setEventHandler { MainActor.assumeIsolated { self.task?.cancel() } }
            source.resume()
            signals.append(source)
        }
    }
}

@main enum AddressedPointerMechanismProbe {
    @MainActor static func main() {
        if let status = ForegroundActivation.runIfRequested(CommandLine.arguments) { exit(status) }
        do {
            let run = try AddressedPointerProbeRun(CommandLine.arguments)
            let application = NSApplication.shared
            application.setActivationPolicy(.prohibited)
            signal(SIGPIPE, SIG_IGN)
            let delegate = AddressedPointerProbeDelegate(run)
            delegate.observeTermination()
            application.delegate = delegate
            application.run()
        } catch {
            diagnose("primitive diagnostic arguments: \(error)")
            exit(2)
        }
    }
}
