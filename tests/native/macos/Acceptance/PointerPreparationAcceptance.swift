import AppKit
import ApplicationServices

/// Opt-in mechanism acceptance for the production legacy element-pointer
/// preparation path. This is NOT the public NativeClickPipeline and never
/// chooses an alternative route or retries a possibly dispatched gesture.
@main struct PointerPreparationAcceptance {
    @MainActor static func main() async throws {
        // Match the driver entry: the spawned production activation helper
        // must run before this parent creates any NSApplication event context.
        if let status = ForegroundActivation.runIfRequested(CommandLine.arguments) { exit(status) }
        NSApplication.shared.setActivationPolicy(.prohibited)
        let args = CommandLine.arguments
        guard args.count == 6, let pid = Int32(args[1]), let windowID = UInt32(args[2]) else {
            throw DriverError("usage: PointerPreparationAcceptance observedPID observedCGWindowID oracleDirectory mode output.json",
                              code: .invalidRequest)
        }
        let folder = URL(fileURLWithPath: args[3]).standardizedFileURL
        let mode = args[4]
        let output = URL(fileURLWithPath: args[5]).standardizedFileURL
        let modes = ["stationary", "move-window", "move-element", "remove-original", "key-context"]
        guard modes.contains(mode),
              ProcessInfo.processInfo.environment["COMPUTER_USE_DESKTOP"] == "shared",
              ProcessInfo.processInfo.environment["COMPUTER_USE_FOREGROUND_ASSIST"] == "1" else {
            throw DriverError("requires explicit shared desktop, foreground assist and a supported fixture mode", code: .invalidRequest)
        }
        func state() throws -> [String: Any] {
            guard let value = try JSONSerialization.jsonObject(with: Data(contentsOf: folder.appendingPathComponent("state.json")))
                    as? [String: Any] else { throw DriverError("invalid fixture oracle", code: .invalidRequest) }
            return value
        }
        let before = try state()
        // #filePath is absolute in the documented compile command; this binds
        // the tool to this repo's disposable fixture, not arbitrary bundle IDs.
        var repo = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 { repo.deleteLastPathComponent() }
        let fixture = repo.appendingPathComponent(".artifacts/first-direct-action-fixes-20261026/PointerPreparationMotionProbe.app")
            .resolvingSymlinksInPath().standardizedFileURL
        guard let app = NSRunningApplication(processIdentifier: pid),
              app.bundleIdentifier == "com.computeruse.tests.PointerPreparationMotionProbe",
              app.bundleURL?.resolvingSymlinksInPath().standardizedFileURL == fixture,
              app.executableURL?.lastPathComponent == "PointerPreparationMotionProbe",
              let identity = NativeProcessIdentity.read(pid),
              (before["pid"] as? NSNumber)?.int32Value == pid,
              (before["windowNumber"] as? NSNumber)?.uint32Value == windowID,
              before["mode"] as? String == mode,
              (before["activations"] as? Int) == 1,
              (before["transitions"] as? Int) == 0,
              (before["originalMouseDowns"] as? Int) == 0,
              (before["replacementMouseDowns"] as? Int) == 0,
              before["originalAttached"] as? Bool == true,
              before["guard"] as? String == "GUARD",
              (before["frontmostPID"] as? NSNumber)?.int32Value != pid,
              NSWorkspace.shared.frontmostApplication?.processIdentifier != pid else {
            throw DriverError("requires one already observed, untouched, background disposable fixture with exact PID/window identity",
                              code: .invalidRequest)
        }
        let root = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(root, 0.2)
        let windows = AXAccess.read(root, kAXWindowsAttribute).value as? [AXUIElement] ?? []
        let matches = windows.filter { BackgroundEvents.nativeWindowID($0) == windowID }
        guard matches.count == 1 else { throw DriverError("observed fixture window is not unique/current", code: .staleTarget) }
        let window = matches[0]
        let initialMapping = try NativeWindowUnit(application: app, window: window).mapping()
        guard initialMapping.id == windowID else { throw DriverError("native fixture window mismatch", code: .staleTarget) }
        var pending = [window], seen = [AXUIElement](), targets = [AXUIElement]()
        let lookupDeadline = ProcessInfo.processInfo.systemUptime + 2
        while !pending.isEmpty, seen.count < 128, ProcessInfo.processInfo.systemUptime < lookupDeadline {
            let node = pending.removeFirst()
            if seen.contains(where: { CFEqual($0, node) }) { continue }
            seen.append(node)
            if AXAccess.read(node, kAXIdentifierAttribute).value as? String == "original-motion-target" { targets.append(node) }
            pending += AXAccess.read(node, kAXChildrenAttribute).value as? [AXUIElement] ?? []
        }
        guard targets.count == 1 else { throw DriverError("original fixture target must resolve uniquely before preparation", code: .invalidRequest) }
        let original = targets[0]
        let target = BackgroundTarget(app)
        // Keep the original native element/window boundaries during PREPARE
        // and every posted event; never re-resolve the identifier after setup.
        @MainActor func check() throws {
            try RequestDiagnostics.checkCancellation()
            try target.validate(input: true)
            guard NativeProcessIdentity.read(pid) == identity else { throw DriverError("fixture process changed", code: .staleTarget) }
            var node: AXUIElement? = original
            var visited = [AXUIElement]()
            while let current = node, visited.count < 32 {
                if CFEqual(current, window) { return }
                guard !visited.contains(where: { CFEqual($0, current) }) else { break }
                visited.append(current)
                node = AXAccess.element(current, kAXParentAttribute)
            }
            throw DriverError("original fixture element no longer belongs to the retained window", code: .staleTarget)
        }
        let unit = NativeElementPointerUnit(application: app, window: window, element: original)
        let initialGeometry = try unit.geometry()
        var report: [String: Any] = [
            "publicApiAcceptance": false, "mechanism": "production NativeElementPointerUnit.prepare(foreground:true) then at most one deliver",
            "senderPID": getpid(), "fixturePID": pid, "windowID": windowID, "mode": mode,
            "before": before, "initialPoint": [initialGeometry.point.x, initialGeometry.point.y],
            "initialMappingFrame": [initialMapping.frame.minX, initialMapping.frame.minY, initialMapping.frame.width, initialMapping.frame.height],
            "originalIdentifierResolvedOnce": true, "sourceFile": #filePath
        ]
        var preparations = [String](), prepared = false, cleanupUnknown = false
        var dispatchBoundaryCalls = 0, postedCallbacks = 0, deliveryInvocations = 0
        var stage = "prepare"
        let deadline = ProcessInfo.processInfo.systemUptime + 20
        do {
            try await RequestDiagnostics.$id.withValue(34034) {
                try await RequestDiagnostics.$deadline.withValue(deadline) {
                    try await unit.prepare(foreground: true, check: check,
                                           onPreparation: { preparations.append($0) },
                                           onCleanupUnknown: { cleanupUnknown = true })
                    prepared = true
                    report["afterPreparation"] = try state()
                    stage = "deliver"
                    deliveryInvocations += 1
                    let intent = try NativePointerIntent(unit: "pointer_sequence", kind: "click",
                                                         parameters: ["button": "left", "count": 1],
                                                         foreground: false, element: true)
                    try await unit.deliver(intent, check: check,
                                           onDispatch: { dispatchBoundaryCalls += 1 },
                                           onPosted: { postedCallbacks += 1 },
                                           onCleanupUnknown: { cleanupUnknown = true })
                    stage = "returned"
                }
            }
        } catch {
            report["error"] = String(describing: error)
            report["errorCode"] = (error as? any ClassifiedFailure)?.code.rawValue ?? "unclassified"
            report["failedStage"] = stage
        }
        // Read-only settling for the actual fixture callback; no input replay.
        try? await Task.sleep(for: .milliseconds(180))
        let after = try state()
        report["after"] = after
        report["prepared"] = prepared
        report["preparations"] = preparations
        report["deliveryInvocations"] = deliveryInvocations
        report["dispatchBoundaryCalls"] = dispatchBoundaryCalls
        report["postedCallbacks"] = postedCallbacks
        report["requestedEffect"] = dispatchBoundaryCalls > 0 ? "possible" : "none"
        report["cleanupUnknown"] = cleanupUnknown
        let guardIntact = after["guard"] as? String == "GUARD"
        let originalClicks = after["originalMouseDowns"] as? Int ?? -1
        let replacementClicks = after["replacementMouseDowns"] as? Int ?? -1
        let positive = ["stationary", "move-window", "move-element"].contains(mode)
        let sameFixture = (after["pid"] as? NSNumber)?.int32Value == pid
            && (after["windowNumber"] as? NSNumber)?.uint32Value == windowID
            && after["mode"] as? String == mode
        let activated = (after["activations"] as? Int ?? 0) >= 2
            && (after["transitions"] as? Int) == 1
            && (after["frontmostPID"] as? NSNumber)?.int32Value == pid
            && after["appActive"] as? Bool == true
        func unchanged(_ key: String) -> Bool {
            guard let a = before[key] as? [Double], let b = after[key] as? [Double] else { return false }
            return a == b
        }
        let attached = after["originalAttached"] as? Bool == true
        let replacementAttached = after["replacementAttached"] as? Bool == true
        let modeApplied: Bool
        switch mode {
        case "stationary": modeApplied = attached && !replacementAttached && unchanged("windowFrame") && unchanged("targetFrame")
        case "move-window": modeApplied = attached && !replacementAttached && !unchanged("windowFrame") && unchanged("targetFrame")
        case "move-element": modeApplied = attached && !replacementAttached && unchanged("windowFrame") && !unchanged("targetFrame")
        case "remove-original": modeApplied = !attached && replacementAttached
        case "key-context":
            let other = after["unrelatedWindowNumber"] as? Int ?? 0
            modeApplied = attached && other > 0 && (after["keyWindow"] as? Int) == other
                && (after["keyRedirections"] as? Int ?? 0) > 0
        default: modeApplied = false
        }
        let passed = sameFixture && activated && modeApplied && guardIntact && replacementClicks == 0 && !cleanupUnknown
            && (positive ? (stage == "returned" && originalClicks == 1 && deliveryInvocations == 1)
                         : (stage != "returned" && originalClicks == 0 && dispatchBoundaryCalls == 0))
        report["oracleChecks"] = ["sameFixture": sameFixture, "actualActivation": activated,
                                  "modeApplied": modeApplied, "guardIntact": guardIntact]
        report["expectedContract"] = positive ? "same original element receives exactly one mouseDown; guard unchanged"
                                             : "refuse with zero requested dispatch and no original/replacement mouseDown; guard unchanged"
        report["passed"] = passed
        report["noReplay"] = true
        SyntheticFocusCoordinator.shared.stop()
        let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: output, options: .atomic)
        print(String(data: data, encoding: .utf8)!)
        if !passed { exit(1) }
    }
}
